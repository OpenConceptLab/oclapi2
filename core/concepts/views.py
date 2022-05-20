from django.db.models import F, Q
from django.http import Http404
from drf_yasg.utils import swagger_auto_schema
from pydash import get
from rest_framework import status
from rest_framework.generics import RetrieveAPIView, DestroyAPIView, ListCreateAPIView, RetrieveUpdateDestroyAPIView, \
    UpdateAPIView, ListAPIView
from rest_framework.mixins import CreateModelMixin
from rest_framework.permissions import IsAuthenticatedOrReadOnly, IsAdminUser, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.bundles.models import Bundle
from core.bundles.serializers import BundleSerializer
from core.common.constants import (
    HEAD, INCLUDE_INVERSE_MAPPINGS_PARAM, INCLUDE_RETIRED_PARAM, ACCESS_TYPE_NONE)
from core.common.exceptions import Http405
from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryMixin
from core.common.swagger_parameters import (
    q_param, limit_param, sort_desc_param, page_param, exact_match_param, sort_asc_param, verbose_param,
    include_facets_header, updated_since_param, include_inverse_mappings_param, include_retired_param,
    compress_header, include_source_versions_param, include_collection_versions_param, cascade_method_param,
    cascade_map_types_params, cascade_exclude_map_types_params, cascade_hierarchy_param, cascade_mappings_param,
    include_mappings_param, cascade_levels_param, cascade_direction_param, cascade_view_hierarchy)
from core.common.tasks import delete_concept, make_hierarchy
from core.common.utils import to_parent_uri_from_kwargs
from core.common.views import SourceChildCommonBaseView, SourceChildExtrasView, \
    SourceChildExtraRetrieveUpdateDestroyView, BaseAPIView
from core.concepts.constants import PARENT_VERSION_NOT_LATEST_CANNOT_UPDATE_CONCEPT
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept, LocalizedText
from core.concepts.permissions import CanViewParentDictionary, CanEditParentDictionary
from core.concepts.search import ConceptSearch
from core.concepts.serializers import (
    ConceptDetailSerializer, ConceptListSerializer, ConceptDescriptionSerializer, ConceptNameSerializer,
    ConceptVersionDetailSerializer,
    ConceptVersionListSerializer, ConceptSummarySerializer, ConceptMinimalSerializer,
    ConceptChildrenSerializer, ConceptParentsSerializer, ConceptLookupListSerializer)
from core.mappings.serializers import MappingListSerializer


class ConceptBaseView(SourceChildCommonBaseView):
    lookup_field = 'concept'
    model = Concept
    queryset = Concept.objects.filter(is_active=True)
    document_model = ConceptDocument
    facet_class = ConceptSearch
    es_fields = Concept.es_fields
    default_filters = dict(is_active=True)

    def get_detail_serializer(self, obj, data=None, files=None, partial=False):
        return ConceptDetailSerializer(obj, data, files, partial, context=dict(request=self.request))

    def get_queryset(self):
        return Concept.get_base_queryset(self.params)

    def set_parent_resource(self, __pop=True):
        parent_resource = None
        source = self.kwargs.pop('source', None) if __pop else self.kwargs.get('source', None)
        collection = self.kwargs.pop('collection', None) if __pop else self.kwargs.get('collection', None)
        container_version = self.kwargs.pop('version', HEAD) if __pop else self.kwargs.get('version', HEAD)
        if 'org' in self.kwargs:
            filters = dict(organization__mnemonic=self.kwargs['org'])
        else:
            username = self.request.user.username if self.user_is_self else self.kwargs.get('user')
            filters = dict(user__username=username)
        if source:
            from core.sources.models import Source
            parent_resource = Source.get_version(source, container_version or HEAD, filters)
        if collection:
            from core.collections.models import Collection
            parent_resource = Collection.get_version(source, container_version or HEAD, filters)
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
            filters = dict(organization__mnemonic=self.kwargs['org'])
        else:
            username = self.request.user.username if self.user_is_self else self.kwargs.get('user')
            filters = dict(user__username=username)
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
            return ConceptMinimalSerializer
        if (is_get and self.is_verbose()) or method == 'POST':
            return ConceptDetailSerializer

        return ConceptListSerializer

    def get_queryset(self):
        is_latest_version = 'collection' not in self.kwargs and 'version' not in self.kwargs or get(
            self.kwargs, 'version') == HEAD
        if get(self, 'parent_resource'):
            parent = self.parent_resource
            queryset = parent.concepts_set if parent.is_head else parent.concepts
            queryset = Concept.apply_attribute_based_filters(queryset, self.params).filter(is_active=True)
        else:
            queryset = super().get_queryset()
        if is_latest_version:
            queryset = queryset.filter(id=F('versioned_object_id'))

        if 'source' in self.kwargs and self.request.query_params.get('onlyParentLess', False) in ['true', True]:
            queryset = queryset.filter(parent_concepts__isnull=True)

        user = self.request.user

        if get(user, 'is_anonymous'):
            queryset = queryset.exclude(public_access=ACCESS_TYPE_NONE)
        elif not get(user, 'is_staff'):
            public_queryset = queryset.exclude(public_access=ACCESS_TYPE_NONE)
            private_queryset = queryset.filter(public_access=ACCESS_TYPE_NONE)
            private_queryset = private_queryset.filter(
                Q(parent__user_id=user.id) | Q(parent__organization__members__id=user.id))
            queryset = public_queryset.union(private_queryset)

        return queryset

    @swagger_auto_schema(
        manual_parameters=[
            q_param, limit_param, sort_desc_param, sort_asc_param, exact_match_param, page_param, verbose_param,
            include_retired_param, include_inverse_mappings_param, updated_since_param,
            include_facets_header, compress_header
        ]
    )
    def get(self, request, *args, **kwargs):
        self.set_parent_resource(False)
        if self.parent_resource:
            self.check_object_permissions(request, self.parent_resource)
        return self.list(request, *args, **kwargs)

    def post(self, request, **_):
        self.set_parent_resource()
        if not self.parent_resource:
            raise Http404()
        serializer = self.get_serializer(data={
            **request.data, 'parent_id': self.parent_resource.id, 'name': request.data.get('id', None)
        })
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
    @staticmethod
    def get_serializer_class():
        from core.collections.serializers import CollectionVersionListSerializer
        return CollectionVersionListSerializer

    def get_object(self, queryset=None):
        queryset = Concept.get_base_queryset(self.params)
        if 'concept_version' in self.kwargs:
            instance = queryset.first()
        else:
            instance = queryset.filter(id=F('versioned_object_id')).first().get_latest_version()

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
        partial = kwargs.pop('partial', True)
        self.parent_resource = self.object.parent

        if self.parent_resource != self.parent_resource.head:
            return Response(
                {'non_field_errors': PARENT_VERSION_NOT_LATEST_CANNOT_UPDATE_CONCEPT},
                status=status.HTTP_400_BAD_REQUEST
            )
        self.object = self.object.clone()
        serializer = self.get_serializer(self.object, data=request.data, partial=partial)
        success_status_code = status.HTTP_200_OK

        if serializer.is_valid():
            self.object = serializer.save()
            if serializer.is_valid():
                return Response(serializer.data, status=success_status_code)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def is_db_delete_requested(self):
        return self.request.query_params.get('db', None) in ['true', True, 'True']

    def destroy(self, request, *args, **kwargs):
        if self.is_container_version_specified():
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        is_hard_delete_requested = self.is_hard_delete_requested()
        if self.is_db_delete_requested() and is_hard_delete_requested:
            parent_filters = Concept.get_parent_and_owner_filters_from_kwargs(self.kwargs)
            result = Concept.objects.filter(mnemonic=self.kwargs['concept'], **parent_filters).delete()
            return Response(result, status=status.HTTP_204_NO_CONTENT)

        concept = self.get_object()
        parent = concept.parent

        if is_hard_delete_requested:
            if self.is_async_requested():
                delete_concept.delay(concept.id)
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
        if 'collection' in self.kwargs:
            raise Http405()

        queryset = self.get_queryset()
        if 'concept_version' not in self.kwargs and 'version' not in self.kwargs:
            queryset = queryset.filter(id=F('versioned_object_id'))

        instance = queryset.first()

        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)
        return instance

    @swagger_auto_schema(
        manual_parameters=[
            cascade_method_param, cascade_map_types_params, cascade_exclude_map_types_params,
            cascade_hierarchy_param, cascade_mappings_param, include_mappings_param, cascade_levels_param,
            cascade_direction_param, cascade_view_hierarchy
        ]
    )
    def get(self, request, **kwargs):  # pylint: disable=unused-argument
        instance = self.get_object()
        bundle = Bundle(
            root=instance, params=self.request.query_params, verbose=self.is_verbose(),
            source_version=self.kwargs.get('version', None)
        )
        bundle.cascade()
        return Response(BundleSerializer(bundle, context=dict(request=request)).data)


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

    def get_queryset(self):
        concept = super().get_queryset().first()
        if not concept:
            raise Http404()
        self.check_object_permissions(self.request, concept)
        include_retired = self.request.query_params.get(INCLUDE_RETIRED_PARAM, False)
        include_indirect_mappings = self.request.query_params.get(INCLUDE_INVERSE_MAPPINGS_PARAM, 'false') == 'true'
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
    model = LocalizedText
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
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            instance = self.get_object()
            new_version = instance.clone()
            subject_label_attr = f"cloned_{self.parent_list_attribute}"
            # get the current labels from the object
            labels = getattr(new_version, subject_label_attr, [])
            # If labels are None then we would want to initialize the labels in new_version
            saved_instance = serializer.save()
            labels.append(saved_instance)
            setattr(new_version, subject_label_attr, labels)
            new_version.comment = f'Added to {self.parent_list_attribute}: {saved_instance.name}.'
            # save updated ConceptVersion into database
            errors = Concept.persist_clone(new_version, request.user)
            if errors:
                return Response(errors, status=status.HTTP_400_BAD_REQUEST)
            headers = self.get_success_headers(serializer.data)
            return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ConceptLabelRetrieveUpdateDestroyView(ConceptBaseView, RetrieveUpdateDestroyAPIView):
    model = LocalizedText
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
            resource_instance = self.get_resource_object()
            new_version = resource_instance.clone()
            saved_instance = serializer.save()
            subject_label_attr = f"cloned_{self.parent_list_attribute}"
            labels = getattr(new_version, subject_label_attr, [])
            labels.append(saved_instance)
            setattr(new_version, subject_label_attr, labels)
            new_version.comment = f'Updated {saved_instance.name} in {self.parent_list_attribute}.'
            errors = Concept.persist_clone(new_version, request.user)
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
            errors = Concept.persist_clone(new_version, request.user)
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

    @staticmethod
    def post(request):
        concept_map = request.data
        if not concept_map:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        result = make_hierarchy.delay(concept_map)

        return Response(
            dict(state=result.state, username=request.user.username, task=result.task_id, queue='default'),
            status=status.HTTP_202_ACCEPTED
        )


class ConceptDebugView(RetrieveAPIView, UpdateAPIView):  # pragma: no cover
    permission_classes = (IsAdminUser, )
    serializer_class = ConceptVersionDetailSerializer
    lookup_field = 'id'
    swagger_schema = None

    def get_queryset(self):
        return Concept.objects.filter(id=self.kwargs['id'])

    def patch(self, request, *args, **kwargs):
        mark_versioned = self.request.data.get('mark_versioned', False) in ['true', True]
        connect_parent_version = self.request.data.get('connect_parent_version', False) in ['true', True]

        concept = self.get_object()
        versioned_object = None
        try:
            versioned_object = concept.versioned_object
            if versioned_object:
                print("Versioned Object Found: ", versioned_object.id)
        except:  # pylint: disable=bare-except
            pass
        if mark_versioned:
            if not versioned_object:
                concept.id = concept.versioned_object_id
                concept.save()
        if connect_parent_version and versioned_object:
            print("Existing Source versions:", versioned_object.sources.values_list('uri', flat=True))
            print("Connecting Source Version...")
            parent = versioned_object.parent
            versioned_object.sources.add(parent)
            concept.sources.add(parent)
        return Response(status=status.HTTP_204_NO_CONTENT)
