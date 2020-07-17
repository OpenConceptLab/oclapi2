from django.db.models.query import QuerySet
from django.shortcuts import get_object_or_404
from pydash import get
from rest_framework import status
from rest_framework.generics import RetrieveAPIView, DestroyAPIView, ListCreateAPIView, RetrieveUpdateDestroyAPIView, \
    ListAPIView, UpdateAPIView
from rest_framework.mixins import CreateModelMixin
from rest_framework.response import Response

from core.common.constants import HEAD, INCLUDE_INVERSE_MAPPINGS_PARAM, INCLUDE_MAPPINGS_PARAM, INCLUDE_RETIRED_PARAM, \
    UPDATED_SINCE_PARAM, LIMIT_PARAM
from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryMixin
from core.common.utils import compact_dict_by_values
from core.common.views import BaseAPIView
from core.concepts.models import Concept, LocalizedText
from core.concepts.permissions import CanViewParentDictionary, CanEditParentDictionary
from core.concepts.serializers import ConceptDetailSerializer, ConceptListSerializer, ConceptDescriptionSerializer, \
    ConceptNameSerializer, ConceptVersionDetailSerializer


class ConceptBaseView(BaseAPIView):
    lookup_field = 'concept'
    pk_field = 'mnemonic'
    model = Concept
    permission_classes = (CanViewParentDictionary,)
    queryset = Concept.objects.filter(is_active=True)

    @staticmethod
    def get_detail_serializer(obj, data=None, files=None, partial=False):
        return ConceptDetailSerializer(obj, data, files, partial)

    def get_filter_params(self):
        params = dict()
        params['user'] = self.request.query_params.get('user', None) or self.kwargs.get('user', None)
        params['org'] = self.request.query_params.get('org', None) or self.kwargs.get('org', None)
        params['collection'] = self.request.query_params.get('collection', None) or self.kwargs.get('collection', None)
        params['source'] = self.request.query_params.get('source', None) or self.kwargs.get('source', None)
        params['version'] = self.request.query_params.get('version', None) or self.kwargs.get('version', None)
        params['concept'] = self.request.query_params.get('concept', None) or self.kwargs.get('concept', None)
        params['concept_version'] = self.request.query_params.get(
            'concept_version', None
        ) or self.kwargs.get('concept_version', None)
        params['is_latest'] = 'is_latest' in self.kwargs
        params[INCLUDE_RETIRED_PARAM] = self.request.query_params.get(
            INCLUDE_RETIRED_PARAM, None
        ) or self.kwargs.get(INCLUDE_RETIRED_PARAM, None)
        params[UPDATED_SINCE_PARAM] = self.request.query_params.get(
            UPDATED_SINCE_PARAM, None
        ) or self.kwargs.get(UPDATED_SINCE_PARAM, None)

        return compact_dict_by_values(params)

    def get_queryset(self):
        return Concept.get_base_queryset(self.get_filter_params())


class ConceptVersionListAllView(ConceptBaseView, ListWithHeadersMixin):
    permission_classes = (CanViewParentDictionary,)

    def get_serializer_class(self):
        if self.is_verbose(self.request):
            return ConceptDetailSerializer

        return ConceptListSerializer

    def get_serializer_context(self):
        context = {'request': self.request}
        if self.is_verbose(self.request):
            context.update({'verbose': True})
        if self.request.GET.get(INCLUDE_INVERSE_MAPPINGS_PARAM):
            context.update({'include_indirect_mappings': True})
        if self.request.GET.get(INCLUDE_MAPPINGS_PARAM):
            context.update({'include_direct_mappings': True})
        return context

    def get_queryset(self):
        queryset = Concept.global_listing_queryset(
            self.get_filter_params(), self.request.user
        ).select_related(
            'parent__organization', 'parent__user',
        ).prefetch_related('names', 'descriptions')
        limit = int(self.request.query_params.get(LIMIT_PARAM, 25))
        return queryset[0:limit]

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class ConceptListView(ConceptBaseView, ListWithHeadersMixin, CreateModelMixin):
    serializer_class = ConceptListSerializer

    def get_permissions(self):
        if self.request.method == 'POST':
            return [CanEditParentDictionary(), ]

        return [CanViewParentDictionary(), ]

    def get_serializer_class(self):
        if (self.request.method == 'GET' and self.is_verbose(self.request)) or self.request.method == 'POST':
            return ConceptDetailSerializer

        return ConceptListSerializer

    def get_queryset(self):
        return super().get_queryset().filter(version=HEAD).select_related(
            'parent__organization', 'parent__user',
        ).prefetch_related('names')

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def set_parent_resource(self):
        from core.sources.models import Source
        source = self.kwargs.pop('source', None)
        source_version = self.kwargs.pop('version', HEAD)
        parent_resource = None
        if source:
            parent_resource = Source.get_version(source, source_version)
        self.kwargs['parent_resource'] = self.parent_resource = parent_resource

    def post(self, request, **kwargs):  # pylint: disable=unused-argument
        self.set_parent_resource()
        serializer = self.get_serializer(data={
            **request.data, 'version': HEAD, 'parent_id': self.parent_resource.id, 'name': request.data.get('id', None)
        })
        if serializer.is_valid():
            self.object = serializer.save()
            if serializer.is_valid():
                headers = self.get_success_headers(serializer.data)
                serializer = ConceptDetailSerializer(self.object)
                return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ConceptRetrieveUpdateDestroyView(ConceptBaseView, RetrieveAPIView, UpdateAPIView, DestroyAPIView):
    serializer_class = ConceptDetailSerializer

    def get_object(self, queryset=None):
        return get_object_or_404(self.get_queryset(), version=HEAD)

    def get_permissions(self):
        if self.request.method in ['GET']:
            return [CanViewParentDictionary(), ]

        return [CanEditParentDictionary(), ]

    def update(self, request, *args, **kwargs):
        self.object = self.get_object()
        partial = kwargs.pop('partial', True)
        if self.object is None:
            return Response(
                {'non_field_errors': 'Could not find concept to update'}, status=status.HTTP_404_NOT_FOUND
            )

        self.parent_resource = self.object.parent

        if self.parent_resource != self.parent_resource.head:
            return Response(
                {'non_field_errors': 'Parent version is not the latest. Cannot update concept.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        self.object = self.object.get_latest_version().clone()
        serializer = self.get_serializer(self.object, data=request.data, partial=partial)
        success_status_code = status.HTTP_200_OK

        if serializer.is_valid():
            self.object = serializer.save()
            if serializer.is_valid():
                serializer = ConceptDetailSerializer(self.object, context={'request': request})
                return Response(serializer.data, status=success_status_code)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        concept = self.get_object()
        if not concept:
            return Response(
                dict(non_field_errors='Could not find concept to retire'),
                status=status.HTTP_404_NOT_FOUND
            )
        comment = request.data.get('update_comment', None) or request.data.get('comment', None)
        errors = concept.retire(request.user, comment)

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)


class ConceptVersionsView(ConceptBaseView, ConceptDictionaryMixin, ListWithHeadersMixin):
    serializer_class = ConceptListSerializer
    permission_classes = (CanViewParentDictionary,)

    def get(self, request, *args, **kwargs):
        self.serializer_class = ConceptDetailSerializer if self.is_verbose(request) else ConceptListSerializer
        return self.list(request, *args, **kwargs)


class ConceptVersionRetrieveView(ConceptBaseView, RetrieveAPIView):
    serializer_class = ConceptVersionDetailSerializer
    permission_classes = (CanViewParentDictionary,)

    def get_object(self, queryset=None):
        return self.get_queryset().first()


class ConceptLabelListCreateView(ConceptBaseView, ListWithHeadersMixin, ListCreateAPIView):
    model = LocalizedText
    parent_list_attribute = None

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewParentDictionary()]

        return [CanEditParentDictionary()]

    def get_object(self, queryset=None):
        return super().get_queryset().first()

    def get_queryset(self):
        if not self.parent_list_attribute:
            return None

        instance = self.get_object()
        return getattr(instance, self.parent_list_attribute).all()

    def create(self, request, **_):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            instance = self.get_object()
            new_version = instance.clone()
            subject_label_attr = "cloned_{}".format(self.parent_list_attribute)
            # get the current labels from the object
            labels = getattr(new_version, subject_label_attr, [])
            # If labels are None then we would want to initialize the labels in new_version
            saved_instance = serializer.save()
            labels.append(saved_instance)
            setattr(new_version, subject_label_attr, labels)
            new_version.comment = 'Added to %s: %s.' % (self.parent_list_attribute, saved_instance.name)
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
    permission_classes = (CanEditParentDictionary,)

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewParentDictionary()]

        return [CanEditParentDictionary()]

    def get_queryset(self):
        if not self.parent_list_attribute:
            return None

        instance = self.get_resource_object()
        return getattr(instance, self.parent_list_attribute).all()

    def get_resource_object(self):
        return super().get_queryset().first()

    def get_object(self, queryset=None):
        return get(self.get_resource_object(), self.parent_list_attribute).filter(id=self.kwargs['uuid']).first()

    def update(self, request, **_):
        partial = True
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)

        if serializer.is_valid():
            resource_instance = self.get_resource_object()
            new_version = resource_instance.clone()
            saved_instance = serializer.save()
            subject_label_attr = "cloned_{}".format(self.parent_list_attribute)
            labels = getattr(new_version, subject_label_attr, [])
            labels.append(saved_instance)
            setattr(new_version, subject_label_attr, labels)
            new_version.comment = 'Updated %s in %s.' % (saved_instance.name, self.parent_list_attribute)
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
            subject_label_attr = "cloned_{}".format(self.parent_list_attribute)
            labels = getattr(new_version, subject_label_attr, [])
            if isinstance(labels, QuerySet):
                labels = labels.exclude(id=instance.id)
            setattr(new_version, subject_label_attr, labels)
            new_version.comment = 'Deleted %s in %s.' % (instance.name, self.parent_list_attribute)
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


class ConceptExtrasBaseView(ConceptBaseView):
    def get_object(self, queryset=None):
        return self.get_queryset().filter(version=HEAD).first()


class ConceptExtrasView(ConceptExtrasBaseView, ListAPIView):
    permission_classes = (CanViewParentDictionary,)

    def list(self, request, *args, **kwargs):
        return Response(get(self.get_object(), 'extras', {}))


class ConceptExtraRetrieveUpdateDestroyView(ConceptExtrasBaseView, RetrieveUpdateDestroyAPIView):
    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewParentDictionary()]

        return [CanEditParentDictionary()]

    def retrieve(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        instance = self.get_object()
        extras = get(instance, 'extras', {})
        if key in extras:
            return Response({key: extras[key]})
        return Response(dict(detail='Not found.'), status=status.HTTP_404_NOT_FOUND)

    def update(self, request, **kwargs):
        key = kwargs.get('extra')
        value = request.data.get(key)
        if not value:
            return Response(
                ['Must specify %s param in body.' % key],
                status=status.HTTP_400_BAD_REQUEST
            )

        new_version = self.get_object().clone()
        new_version.extras[key] = value
        new_version.comment = 'Updated extras: %s=%s.' % (key, value)
        errors = Concept.persist_clone(new_version, request.user)
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        return Response({key: value})

    def delete(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        new_version = self.get_object().clone()
        if key in new_version.extras:
            del new_version.extras[key]
            new_version.comment = 'Deleted extra %s.' % key
            errors = Concept.persist_clone(new_version, request.user)
            if errors:
                return Response(errors, status=status.HTTP_400_BAD_REQUEST)
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(dict(detail='Not found.'), status=status.HTTP_404_NOT_FOUND)
