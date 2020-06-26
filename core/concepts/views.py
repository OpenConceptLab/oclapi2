from django.db.models.query import QuerySet
from pydash import get
from rest_framework import status
from rest_framework.generics import RetrieveAPIView, DestroyAPIView, ListCreateAPIView, RetrieveUpdateDestroyAPIView, \
    ListAPIView
from rest_framework.response import Response

from core.common.constants import HEAD
from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryMixin
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

    def get_queryset(self):
        queryset = self.queryset
        source = self.request.query_params.get('source', None) or self.kwargs.get('source', None)
        concept = self.request.query_params.get('concept', None) or self.kwargs.get('concept', None)
        concept_version = self.request.query_params.get(
            'concept_version', None
        ) or self.kwargs.get('concept_version', None)

        if source:
            queryset = queryset.filter(parent__mnemonic=source)
        if concept:
            queryset = queryset.filter(mnemonic=concept)
        if concept_version:
            queryset = queryset.filter(version=concept_version)

        return queryset


class ConceptListView(ConceptBaseView, ListWithHeadersMixin):
    serializer_class = ConceptListSerializer

    def get_queryset(self):
        return super().get_queryset().filter(version=HEAD)

    def get(self, request, *args, **kwargs):
        self.serializer_class = ConceptDetailSerializer if self.is_verbose(request) else ConceptListSerializer
        return self.list(request, *args, **kwargs)


class ConceptRetrieveUpdateDestroyView(ConceptBaseView, RetrieveAPIView, DestroyAPIView):
    serializer_class = ConceptDetailSerializer

    def get_queryset(self):
        return super().get_queryset().filter(version=HEAD)

    def destroy(self, request, *args, **kwargs):
        concept = self.get_object()
        try:
            concept.delete()
        except Exception as ex:
            return Response({'detail': ex.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'detail': 'Successfully deleted concept.'}, status=status.HTTP_204_NO_CONTENT)


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
