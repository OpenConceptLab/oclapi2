from django.conf import settings
from django.db.models import F
from django.http import Http404
from drf_yasg.utils import swagger_auto_schema
from pydash import get, compact
from rest_framework import status
from rest_framework.generics import RetrieveAPIView, DestroyAPIView, ListCreateAPIView, RetrieveUpdateDestroyAPIView, \
    UpdateAPIView, ListAPIView
from rest_framework.mixins import CreateModelMixin
from rest_framework.permissions import IsAuthenticatedOrReadOnly, IsAdminUser, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.bundles.models import Bundle
from core.bundles.serializers import BundleSerializer
from core.collections.documents import CollectionDocument
from core.common.constants import (
    HEAD, INCLUDE_INVERSE_MAPPINGS_PARAM, INCLUDE_RETIRED_PARAM, ACCESS_TYPE_NONE, LIMIT_PARAM, LIST_DEFAULT_LIMIT)
from core.common.exceptions import Http400, Http403, Http409
from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryMixin
from core.common.search import CustomESSearch
from core.common.swagger_parameters import (
    q_param, limit_param, sort_desc_param, page_param, sort_asc_param, verbose_param,
    include_facets_header, updated_since_param, include_inverse_mappings_param, include_retired_param,
    compress_header, include_source_versions_param, include_collection_versions_param, cascade_method_param,
    cascade_map_types_param, cascade_exclude_map_types_param, cascade_hierarchy_param, cascade_mappings_param,
    cascade_levels_param, cascade_direction_param, cascade_view_hierarchy, return_map_types_param,
    omit_if_exists_in_param, equivalency_map_types_param, search_from_latest_repo_header)
from core.common.tasks import delete_concept, make_hierarchy
from core.common.throttling import ThrottleUtil
from core.common.utils import to_parent_uri_from_kwargs, generate_temp_version, get_truthy_values, to_int
from core.common.views import SourceChildCommonBaseView, SourceChildExtrasView, \
    SourceChildExtraRetrieveUpdateDestroyView, BaseAPIView
from core.concepts.constants import PARENT_VERSION_NOT_LATEST_CANNOT_UPDATE_CONCEPT
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept, ConceptName
from core.concepts.permissions import CanViewParentDictionary, CanEditParentDictionary
from core.concepts.search import ConceptFacetedSearch, ConceptFuzzySearch
from core.concepts.serializers import (
    ConceptDetailSerializer, ConceptListSerializer, ConceptDescriptionSerializer, ConceptNameSerializer,
    ConceptVersionDetailSerializer,
    ConceptVersionListSerializer, ConceptSummarySerializer, ConceptMinimalSerializer,
    ConceptChildrenSerializer, ConceptParentsSerializer, ConceptLookupListSerializer, ConceptChecksumSerializer)
from core.mappings.serializers import MappingListSerializer
from core.tasks.models import Task
from core.toggles.models import Toggle

TRUTHY = get_truthy_values()


class ConceptBaseView(SourceChildCommonBaseView):
    lookup_field = 'concept'
    model = Concept
    queryset = Concept.objects.filter(is_active=True)
    document_model = ConceptDocument
    facet_class = ConceptFacetedSearch
    es_fields = Concept.es_fields
    default_filters = {}

    def get_detail_serializer(self, obj, data=None, files=None, partial=False):
        return ConceptDetailSerializer(obj, data, files, partial, context={'request': self.request})

    def get_queryset(self):
        return Concept.get_base_queryset(self.params)

    def set_parent_resource(self, __pop=True):
        parent_resource = None
        source = self.kwargs.pop('source', None) if __pop else self.kwargs.get('source', None)
        collection = self.kwargs.pop('collection', None) if __pop else self.kwargs.get('collection', None)
        container_version = self.kwargs.pop('version', HEAD) if __pop else self.kwargs.get('version', HEAD)
        if 'org' in self.kwargs:
            filters = {'organization__mnemonic': self.kwargs['org']}
        else:
            username = self.request.user.username if self.user_is_self else self.kwargs.get('user')
            filters = {'user__username': username}
        if source:
            from core.sources.models import Source
            parent_resource = Source.get_version(source, container_version or HEAD, filters)
        if collection:
            from core.collections.models import Collection
            parent_resource = Collection.get_version(collection, container_version or HEAD, filters)
        self.kwargs['parent_resource'] = self.parent_resource = parent_resource


# this is a cached view (expiry 24 hours)
# used for TermBrowser forms lookup values -- map-types/locales/datatypes/etc
class ConceptLookupValuesView(ListAPIView, BaseAPIView):  # pragma: no cover
    serializer_class = ConceptLookupListSerializer
    permission_classes = (AllowAny, )

    def set_parent_resource(self):
        parent_resource = None
        source = self.kwargs.get('source', None)
        if 'org' in self.kwargs:
            filters = {'organization__mnemonic': self.kwargs['org']}
        else:
            username = self.request.user.username if self.user_is_self else self.kwargs.get('user')
            filters = {'user__username': username}
        if source:
            from core.sources.models import Source
            parent_resource = Source.get_version(source, HEAD, filters)
        self.kwargs['parent_resource'] = self.parent_resource = parent_resource

    def get_queryset(self):
        self.set_parent_resource()
        if self.parent_resource:
            queryset = self.parent_resource.concepts_set.filter(id=F('versioned_object_id'))
            if self.is_verbose():
                queryset = queryset.prefetch_related('names')
            return queryset

        raise Http404()


# this is a cached view (expiry 24 hours)
# driver from settings.DEFAULT_LOCALES_REPO_URI
class ConceptDefaultLocalesView(ListAPIView, BaseAPIView):  # pragma: no cover
    serializer_class = ConceptLookupListSerializer
    permission_classes = (AllowAny, )

    def get_queryset(self):
        from core.sources.models import Source
        source = Source.objects.filter(uri=settings.DEFAULT_LOCALES_REPO_URI).first()
        if source:
            queryset = source.concepts_set.filter(id=F('versioned_object_id'))
            if self.is_verbose():
                queryset = queryset.prefetch_related('names')
            return queryset

        raise Http404()


class ConceptListView(ConceptBaseView, ListWithHeadersMixin, CreateModelMixin):
    serializer_class = ConceptListSerializer

    def get_permissions(self):
        if self.request.method == 'POST':
            return [CanEditParentDictionary(), ]

        return [CanViewParentDictionary(), ]

    def get_serializer_class(self):
        method = self.request.method
        is_get = method == 'GET'

        if is_get and self.is_brief():
            if self.is_checksums():
                return ConceptChecksumSerializer
            return ConceptMinimalSerializer
        if (is_get and self.is_verbose()) or method == 'POST':
            return ConceptDetailSerializer

        return ConceptListSerializer

    def is_sliced(self):
        result = super().is_sliced()
        if result:
            return result
        parent = get(self, 'parent_resource')

        return 'source' in self.kwargs and parent and not parent.is_head

    def get_queryset(self):
        is_latest_version = 'collection' not in self.kwargs and (
                'version' not in self.kwargs or get(self.kwargs, 'version') == HEAD
        )
        parent = get(self, 'parent_resource')
        is_source_nested = 'source' in self.kwargs
        only_hierarchy_root = self.request.query_params.get('onlyHierarchyRoot', False) in TRUTHY and is_source_nested
        if parent:
            if only_hierarchy_root:
                queryset = Concept.objects.filter(id=parent.hierarchy_root_id).filter()
            elif parent.is_head:
                queryset = Concept.apply_attribute_based_filters(
                    parent.concepts_set, self.params).filter(is_active=True)
            else:
                limit = to_int(self.params.get(LIMIT_PARAM), LIST_DEFAULT_LIMIT)
                page = to_int(self.params.get('page'), 1)
                offset = (page - 1) * limit
                through_qs = Concept.sources.through.objects.filter(source_id=parent.id)
                filters = Concept.get_filters_for_criterion(
                    {k: v for k, v in self.params.items() if k not in ['is_latest']}, 'concept')
                exclude_retired = 'concept__retired' in filters  # filters will have it false to exclude
                self.total_count = through_qs.filter(
                    concept__retired=False, concept__is_active=True).count() if exclude_retired else through_qs.count()
                queryset = Concept.objects.filter(
                    id__in=through_qs.filter(
                        **filters, concept__is_active=True
                    ).values_list('concept_id', flat=True).order_by('-concept_id')[offset:offset+limit]
                )
        else:
            queryset = super().get_queryset()

        if is_latest_version:
            queryset = queryset.filter(id=F('versioned_object_id'))
        if is_source_nested and self.request.query_params.get('onlyParentLess', False) in TRUTHY:
            queryset = queryset.filter(parent_concepts__isnull=True)

        if not self.is_brief():
            queryset = queryset.prefetch_related('names', 'descriptions')

        if not parent:
            user = self.request.user
            is_anonymous = get(user, 'is_anonymous')
            is_staff = get(user, 'is_staff')
            if is_anonymous:
                queryset = queryset.exclude(public_access=ACCESS_TYPE_NONE)
            elif not is_staff:
                queryset = Concept.apply_user_criteria(queryset, user)

        return queryset

    def _set_source_versions(self):
        from core.sources.models import Source
        source_versions = []
        for version_url in compact((self.request.query_params.dict().get('source_version', '')).split(',')):
            source_version, _ = Source.resolve_expression_to_version(version_url)
            if source_version.id:
                source_versions.append(source_version)
        self._source_versions = source_versions

    @swagger_auto_schema(
        manual_parameters=[
            q_param, limit_param, sort_desc_param, sort_asc_param, page_param, verbose_param,
            include_retired_param, include_inverse_mappings_param, updated_since_param,
            include_facets_header, compress_header, search_from_latest_repo_header
        ]
    )
    def get(self, request, *args, **kwargs):
        if self.is_fuzzy_search:
            self._set_source_versions()
            self._extra_filters = None
        self.set_parent_resource(False)
        if self.parent_resource:
            self.check_object_permissions(request, self.parent_resource)
        return self.list(request, *args, **kwargs)

    def post(self, request, **_):
        self.set_parent_resource()
        if not self.parent_resource or isinstance(request.data, list):
            raise Http404()
        concept_id = get(request.data, 'id') or generate_temp_version()
        serializer = self.get_serializer(
            data={**request.data, 'parent_id': self.parent_resource.id, 'id': concept_id, 'name': concept_id}
        )
        if serializer.is_valid():
            serializer.save()
            if serializer.is_valid():
                headers = self.get_success_headers(serializer.data)
                return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ConceptSummaryView(ConceptBaseView, RetrieveAPIView):
    serializer_class = ConceptSummarySerializer

    def get_object(self, queryset=None):
        if 'collection' in self.kwargs:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        queryset = self.get_queryset()

        if 'concept_version' not in self.kwargs:
            queryset = queryset.filter(id=F('versioned_object_id'))

        instance = queryset.first()

        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)

        return instance


class ConceptCollectionMembershipView(ConceptBaseView, ListWithHeadersMixin):
    document_model = CollectionDocument

    def get_serializer_class(self):
        from core.collections.serializers import CollectionVersionListSerializer
        return CollectionVersionListSerializer

    def get_object(self, queryset=None):
        queryset = Concept.get_base_queryset(self.params)
        if 'concept_version' not in self.kwargs:
            queryset = queryset.filter(id=F('versioned_object_id'))
        instance = queryset.first()

        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)

        return instance

    def get_queryset(self):
        instance = self.get_object()

        from core.collections.models import Collection
        return Collection.objects.filter(id__in=instance.expansion_set.filter(
            collection_version__organization_id=instance.parent.organization_id,
            collection_version__user_id=instance.parent.user_id
        ).values_list('collection_version_id', flat=True))

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class ConceptRetrieveUpdateDestroyView(ConceptBaseView, RetrieveAPIView, UpdateAPIView, DestroyAPIView):
    serializer_class = ConceptDetailSerializer

    def is_container_version_specified(self):
        return 'version' in self.kwargs

    def get_object(self, queryset=None):
        queryset = self.get_queryset()
        if not self.is_container_version_specified():
            queryset = queryset.filter(id=F('versioned_object_id'))
        instance = queryset.first()
        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)

        instance.get_checksums()
        for version in instance.versions:
            version.get_checksums()

        return instance

    def get_permissions(self):
        if self.request.method in ['GET']:
            return [CanViewParentDictionary(), ]

        if self.request.method == 'DELETE' and self.is_hard_delete_requested():
            return [IsAdminUser(), ]

        return [CanEditParentDictionary(), ]

    def update(self, request, *args, **kwargs):
        if self.is_container_version_specified():
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        self.object = self.get_object()
        real_partial = kwargs.get('partial', False)
        partial = kwargs.pop('partial', True)
        self.parent_resource = self.object.parent

        if self.parent_resource != self.parent_resource.head:
            return Response(
                {'non_field_errors': PARENT_VERSION_NOT_LATEST_CANNOT_UPDATE_CONCEPT},
                status=status.HTTP_400_BAD_REQUEST
            )
        self.object = self.object.clone()
        serializer = self.get_serializer(
            self.object, data=request.data, partial=partial, is_patch=real_partial)
        success_status_code = status.HTTP_200_OK

        if serializer.is_valid():
            self.object = serializer.save()
            if serializer.is_valid():
                return Response(serializer.data, status=success_status_code)

        if Concept.is_standard_checksum_error(serializer.errors):
            return Response(serializer.errors, status=status.HTTP_208_ALREADY_REPORTED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def is_db_delete_requested(self):
        return self.request.query_params.get('db', None) in TRUTHY

    def destroy(self, request, *args, **kwargs):
        if self.is_container_version_specified():
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        is_hard_delete_requested = self.is_hard_delete_requested()
        if self.is_db_delete_requested() and is_hard_delete_requested:
            parent_filters = Concept.get_parent_and_owner_filters_from_kwargs(self.kwargs)
            concepts = Concept.objects.filter(mnemonic=self.kwargs['concept'], **parent_filters)
            concept = concepts.filter(id=F('versioned_object_id')).first()
            parent = concept.parent
            result = concepts.delete()
            parent.update_concepts_count()
            return Response(result, status=status.HTTP_204_NO_CONTENT)

        concept = self.get_object()
        parent = concept.parent

        if is_hard_delete_requested:
            if self.is_async_requested():
                task = Task.new(queue='default', user=request.user, name=delete_concept.__name__)
                delete_concept.apply_async((concept.id,), queue=task.queue, task_id=task.id)
                return Response(status=status.HTTP_204_NO_CONTENT)
            concept.delete()
            parent.update_concepts_count()
            return Response(status=status.HTTP_204_NO_CONTENT)

        comment = request.data.get('update_comment', None) or request.data.get('comment', None)
        errors = concept.retire(request.user, comment)

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        parent.update_concepts_count()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConceptCascadeView(ConceptBaseView):
    serializer_class = BundleSerializer

    def get_object(self, queryset=None):
        queryset = self.get_queryset()
        if 'concept_version' not in self.kwargs and 'version' not in self.kwargs and 'collection' not in self.kwargs:
            queryset = queryset.filter(id=F('versioned_object_id'))
        uri_param = self.request.query_params.dict().get('uri')
        if uri_param:
            queryset = queryset.filter(**Concept.get_parent_and_owner_filters_from_uri(uri_param))

        if queryset.count() > 1 and not uri_param and 'collection' in self.kwargs:
            raise Http409()

        instance = queryset.first()

        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)
        return instance

    @swagger_auto_schema(
        manual_parameters=[
            cascade_method_param, cascade_map_types_param, cascade_exclude_map_types_param, return_map_types_param,
            cascade_hierarchy_param, cascade_mappings_param, cascade_levels_param,
            cascade_direction_param, cascade_view_hierarchy, include_retired_param,
            omit_if_exists_in_param, equivalency_map_types_param
        ]
    )
    def get(self, request, **kwargs):  # pylint: disable=unused-argument
        instance = self.get_object()
        self.set_parent_resource(False)
        bundle = Bundle(
            root=instance, params=self.request.query_params, verbose=self.is_verbose(),
            repo_version=self.parent_resource, requested_url=self.request.get_full_path()
        )
        bundle.cascade()
        return Response(BundleSerializer(bundle, context={'request': request}).data)


class ConceptCloneView(ConceptCascadeView):
    serializer_class = BundleSerializer

    def post(self, request, **kwargs):  # pylint: disable=unused-argument
        """
        body:
            {
                “source_uri”: “/orgs/MyOrg/sources/MySource/”, (cloneTo)
                “parameters”: { ….same as cascade… }
            }
        """
        clone_to_source = self.get_clone_to_source()
        self.set_parent_resource(False)
        bundle = Bundle.clone(
            self.get_object(), self.parent_resource, clone_to_source, request.user,
            self.request.get_full_path(), self.is_verbose(), **(request.data.get('parameters') or {})
        )
        return Response(BundleSerializer(bundle, context={'request': request}).data)

    def get_clone_to_source(self):
        source_uri = self.request.data.get('source_uri')
        if not source_uri:
            raise Http400()
        from core.sources.models import Source
        source = Source.objects.filter(uri=source_uri).first()
        if not source:
            raise Http404()
        if not source.has_edit_access(self.request.user):
            raise Http403()
        return source


class ConceptChildrenView(ConceptBaseView, ListWithHeadersMixin):
    serializer_class = ConceptChildrenSerializer
    default_qs_sort_attr = 'mnemonic'

    def get_queryset(self):
        instance = super().get_queryset().filter(id=F('versioned_object_id')).first()
        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)
        return instance.child_concept_queryset()

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class ConceptParentsView(ConceptBaseView, ListWithHeadersMixin):
    serializer_class = ConceptParentsSerializer

    def get_queryset(self):
        instance = super().get_queryset().filter(id=F('versioned_object_id')).first()
        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)
        return instance.parent_concept_queryset()

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class ConceptReactivateView(ConceptBaseView, UpdateAPIView):
    serializer_class = ConceptDetailSerializer
    permission_classes = (CanEditParentDictionary, )

    def get_object(self, queryset=None):
        instance = self.get_queryset().filter(id=F('versioned_object_id')).first()
        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)
        return instance

    def update(self, request, *args, **kwargs):
        concept = self.get_object()
        comment = request.data.get('update_comment', None) or request.data.get('comment', None)
        errors = concept.unretire(request.user, comment)

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        concept.parent.update_concepts_count()

        return Response(status=status.HTTP_204_NO_CONTENT)


class ConceptVersionsView(ConceptBaseView, ConceptDictionaryMixin, ListWithHeadersMixin):
    permission_classes = (CanViewParentDictionary,)

    def get_queryset(self):
        concept = super().get_queryset().filter(id=F('versioned_object_id')).first()
        if not concept:
            raise Http404()
        self.check_object_permissions(self.request, concept)
        return concept.versions

    def get_serializer_class(self):
        return ConceptVersionDetailSerializer if self.is_verbose() else ConceptVersionListSerializer

    @swagger_auto_schema(
        manual_parameters=[
            include_source_versions_param, include_collection_versions_param
        ]
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class ConceptMappingsView(ConceptBaseView, ListWithHeadersMixin):
    serializer_class = MappingListSerializer
    permission_classes = (CanViewParentDictionary,)
    default_qs_sort_attr = ['map_type', 'sort_weight']

    def get_queryset(self):
        concept = super().get_queryset().first()
        if not concept:
            raise Http404()
        self.check_object_permissions(self.request, concept)
        include_retired = self.request.query_params.get(INCLUDE_RETIRED_PARAM, False)
        include_indirect_mappings = self.request.query_params.get(INCLUDE_INVERSE_MAPPINGS_PARAM, 'false') in TRUTHY
        is_collection = 'collection' in self.kwargs
        collection_version = self.kwargs.get('version', HEAD) if is_collection else None
        parent_uri = to_parent_uri_from_kwargs(self.kwargs) if is_collection else None
        if include_indirect_mappings:
            mappings_queryset = concept.get_bidirectional_mappings_for_collection(
                parent_uri, collection_version
            ) if is_collection else concept.get_bidirectional_mappings()
        else:
            mappings_queryset = concept.get_unidirectional_mappings_for_collection(
                parent_uri, collection_version) if is_collection else concept.get_unidirectional_mappings()

        if not include_retired:
            mappings_queryset = mappings_queryset.exclude(retired=True)
        return mappings_queryset

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class ConceptVersionRetrieveView(ConceptBaseView, RetrieveAPIView, DestroyAPIView):
    serializer_class = ConceptVersionDetailSerializer

    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [IsAdminUser()]

        return [CanViewParentDictionary(), ]

    def get_object(self, queryset=None):
        instance = self.get_queryset().first()
        if not instance:
            raise Http404()
        self.check_object_permissions(self.request, instance)
        return instance

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if self.is_hard_delete_requested():
            obj.delete()
        else:
            obj.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConceptLabelListCreateView(ConceptBaseView, ListWithHeadersMixin, ListCreateAPIView):
    model = ConceptName
    parent_list_attribute = None
    default_qs_sort_attr = '-created_at'

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewParentDictionary()]

        return [CanEditParentDictionary()]

    def get_object(self, queryset=None):
        instance = super().get_queryset().first()
        if not instance:
            raise Http404()
        self.check_object_permissions(self.request, instance)
        return instance

    def get_queryset(self):
        if not self.parent_list_attribute:
            return None

        instance = self.get_object()
        return getattr(instance, self.parent_list_attribute).all()

    def create(self, request, **_):  # pylint: disable=arguments-differ
        name = request.data.get('name', None)
        description = request.data.get('description', None)
        locale = name or description
        serializer = self.get_serializer(data=request.data.copy())
        if locale and serializer.is_valid():
            serializer = self.get_serializer(data=request.data)
            if serializer.is_valid():
                new_version = self.get_object().clone()
                new_version.comment = f'Added to {self.parent_list_attribute}: {locale}.'
                if name:
                    new_version.cloned_names = [*new_version.cloned_names, request.data]
                elif description:
                    new_version.cloned_descriptions = [*new_version.cloned_descriptions, request.data]
                errors = new_version.save_as_new_version(request.user)
                if errors:
                    return Response(errors, status=status.HTTP_400_BAD_REQUEST)
                locales = new_version.names if name else new_version.descriptions
                instance = locales.order_by('-id').first()
                serializer = self.get_serializer(instance)
            headers = self.get_success_headers(serializer.data)
            return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ConceptLabelRetrieveUpdateDestroyView(ConceptBaseView, RetrieveUpdateDestroyAPIView):
    model = ConceptName
    parent_list_attribute = None
    permission_classes = (IsAuthenticatedOrReadOnly,)
    default_qs_sort_attr = '-created_at'

    def get_queryset(self):
        if not self.parent_list_attribute:
            return None

        instance = self.get_resource_object()
        return getattr(instance, self.parent_list_attribute).all()

    def get_resource_object(self):
        instance = super().get_queryset().first()
        if not instance:
            raise Http404()
        self.check_object_permissions(self.request, instance)
        return instance

    def get_object(self, queryset=None):
        instance = get(self.get_resource_object(), self.parent_list_attribute).filter(id=self.kwargs['uuid']).first()
        if not instance:
            raise Http404()
        return instance

    def update(self, request, **_):  # pylint: disable=arguments-differ
        partial = True
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)

        if serializer.is_valid():
            subject_label_attr = f"cloned_{self.parent_list_attribute}"
            resource_instance = self.get_resource_object()
            locales = get(resource_instance, self.parent_list_attribute).exclude(id=self.kwargs['uuid'])
            new_version = resource_instance.clone()
            saved_instance = serializer.save()
            setattr(new_version, subject_label_attr, [*[locale.clone() for locale in locales.all()], saved_instance])
            new_version.comment = f'Updated {saved_instance.name} in {self.parent_list_attribute}.'
            errors = new_version.save_as_new_version(request.user)
            if errors:
                return Response(errors, status=status.HTTP_400_BAD_REQUEST)
            return Response(serializer.data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, *args, **kwargs):
        instance = self.get_object()

        if instance:
            resource_instance = self.get_resource_object()
            new_version = resource_instance.clone()
            subject_label_attr = f"cloned_{self.parent_list_attribute}"
            labels = [name.clone() for name in resource_instance.names.exclude(id=instance.id)]
            setattr(new_version, subject_label_attr, labels)
            new_version.comment = f'Deleted {instance.name} in {self.parent_list_attribute}.'
            errors = new_version.save_as_new_version(request.user)
            if errors:
                return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConceptDescriptionListCreateView(ConceptLabelListCreateView):
    serializer_class = ConceptDescriptionSerializer
    parent_list_attribute = 'descriptions'


class ConceptNameListCreateView(ConceptLabelListCreateView):
    serializer_class = ConceptNameSerializer
    parent_list_attribute = 'names'


class ConceptNameRetrieveUpdateDestroyView(ConceptLabelRetrieveUpdateDestroyView):
    parent_list_attribute = 'names'
    serializer_class = ConceptNameSerializer


class ConceptDescriptionRetrieveUpdateDestroyView(ConceptLabelRetrieveUpdateDestroyView):
    parent_list_attribute = 'descriptions'
    serializer_class = ConceptDescriptionSerializer


class ConceptExtrasView(SourceChildExtrasView, ConceptBaseView):
    serializer_class = ConceptDetailSerializer


class ConceptExtraRetrieveUpdateDestroyView(SourceChildExtraRetrieveUpdateDestroyView, ConceptBaseView):
    serializer_class = ConceptDetailSerializer
    model = Concept


class ConceptsHierarchyAmendAdminView(APIView):  # pragma: no cover
    swagger_schema = None
    permission_classes = (IsAdminUser, )

    def get_throttles(self):
        return ThrottleUtil.get_throttles_by_user_plan(self.request.user)

    @staticmethod
    def post(request):
        concept_map = request.data
        if not concept_map:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        task = Task.new(queue='default', user=request.user, name=make_hierarchy.__name__)
        result = make_hierarchy.apply_async((concept_map,), queue=task.queue, task_id=task.id)

        return Response(
            {
                'state': result.state,
                'username': request.user.username,
                'task': result.task_id,
                'queue': 'default'
            },
            status=status.HTTP_202_ACCEPTED
        )


class MetadataToConceptsListView(BaseAPIView):  # pragma: no cover
    default_limit = 1
    score_threshold = 5
    score_threshold_semantic_very_high = 1.64
    serializer_class = ConceptListSerializer
    permission_classes = (IsAuthenticatedOrReadOnly,)
    es_fields = Concept.es_fields

    def get_serializer_class(self):
        if self.is_brief():
            return ConceptMinimalSerializer
        if self.is_verbose():
            return ConceptDetailSerializer

        return ConceptListSerializer

    def filter_queryset(self, _=None):  # pylint:disable=too-many-locals
        rows = self.request.data.get('rows')
        target_repo_url = self.request.data.get('target_repo_url')
        target_repo_params = self.request.data.get('target_repo')

        if not rows or (not target_repo_url and not target_repo_params):
            raise Http400()

        map_config = self.request.data.get('map_config', [])
        filters = self.request.data.get('filter', {})
        include_retired = self.request.query_params.get(INCLUDE_RETIRED_PARAM) in get_truthy_values()
        num_candidates = min(to_int(self.request.query_params.get('numCandidates', 0), 5000), 5000)
        k_nearest = min(to_int(self.request.query_params.get('kNearest', 0), 5), 10)
        offset = max(to_int(self.request.GET.get('offset'), 0), 0)
        limit = max(to_int(self.request.GET.get('limit'), 0), 0) or self.default_limit
        page = max(to_int(self.request.GET.get('page'), 1), 1)
        start = offset or (page - 1) * limit
        end = start + limit
        is_semantic = self.request.query_params.get('semantic', None) in get_truthy_values() and Toggle.get(
            'SEMANTIC_SEARCH_TOGGLE')
        best_match = self.request.query_params.get('bestMatch', None) in get_truthy_values()
        score_threshold = self.score_threshold_semantic_very_high if is_semantic else self.score_threshold
        repo_params = self.get_repo_params(is_semantic, target_repo_params, target_repo_url)
        faceted_criterion = self.get_faceted_criterion(False, filters, minimum_should_match=1) if filters else None

        results = []
        for row in rows:
            search = ConceptFuzzySearch.search(
                row, target_repo_url, repo_params, include_retired,
                is_semantic, num_candidates, k_nearest, map_config, faceted_criterion
            )
            search = search.params(min_score=score_threshold if best_match else 0)
            es_search = CustomESSearch(search[start:end], ConceptDocument)
            es_search.to_queryset(False, True)
            result = {'row': row, 'results': [], 'map_config': map_config, 'filter': filters}
            for concept in es_search.queryset:
                concept._highlight = es_search.highlights.get(concept.id, {})  # pylint:disable=protected-access
                score_info = es_search.scores.get(concept.id, {})
                concept._score = get(score_info, 'raw') or None
                concept._normalized_score = get(score_info, 'normalized') or None
                # Use normalized score for thresholding if available
                score_to_check = concept._normalized_score if concept._normalized_score is not None else concept._score
                concept._match_type = 'low'  # pylint:disable=protected-access
                if score_to_check is not None:
                    if is_semantic and score_to_check > 0.9:
                        concept._match_type = 'very_high'  # pylint:disable=protected-access
                    elif score_to_check > 0.6:
                        concept._match_type = 'high'  # pylint:disable=protected-access
                # Optionally, keep existing highlight logic
                if concept._highlight.get('name', None):  # pylint:disable=protected-access
                    concept._match_type = 'very_high'  # pylint:disable=protected-access
                if is_semantic and score_to_check > self.score_threshold_semantic_very_high:  # pylint:disable=protected-access,line-too-long
                    concept._match_type = 'very_high'  # pylint:disable=protected-access
                if not best_match or concept._match_type == 'very_high':  # pylint:disable=protected-access
                    serializer = ConceptDetailSerializer if self.is_verbose() else ConceptMinimalSerializer
                    data = serializer(concept, context={'request': self.request}).data
                    data['search_meta']['search_normalized_score'] = concept._normalized_score * 100
                    result['results'].append(data)
            if 'results' in result:
                result['results'] = sorted(
                    result['results'], key=lambda res: get(res, 'search_meta.search_normalized_score'), reverse=True)
            results.append(result)

        return results

    @staticmethod
    def get_repo_params(is_semantic, target_repo_params, target_repo_url):
        repo = ConceptFuzzySearch.get_target_repo(target_repo_url)
        if not repo:
            raise Http400(f'Unable to resolve "target_repo_url": "{target_repo_url}"')
        if is_semantic:
            if repo and not repo.has_semantic_match_algorithm:
                raise Http400('This repo version does not support semantic search')
        repo_params = target_repo_params or ConceptFuzzySearch.get_repo_params(repo)
        if not repo_params:
            raise Http400(f'Unable to resolve "target_repo_url": "{target_repo_url}"')
        return repo_params

    def post(self, request, **kwargs):  # pylint: disable=unused-argument
        return Response(self.filter_queryset())
