from django.db import IntegrityError
from django.http import Http404
from rest_framework import status, mixins
from rest_framework.generics import RetrieveAPIView, DestroyAPIView
from rest_framework.response import Response

from core.collections.constants import INCLUDE_REFERENCES_PARAM, HEAD_OF_CONCEPT_ADDED_TO_COLLECTION, \
    HEAD_OF_MAPPING_ADDED_TO_COLLECTION, CONCEPT_ADDED_TO_COLLECTION_FMT, MAPPING_ADDED_TO_COLLECTION_FMT
from core.collections.models import Collection, CollectionReference
from core.collections.serializers import CollectionDetailSerializer, CollectionListSerializer, \
    CollectionCreateSerializer, CollectionReferenceSerializer, CollectionVersionDetailSerializer
from core.collections.utils import is_concept, is_version_specified
from core.common.constants import HEAD, RELEASED_PARAM, PROCESSING_PARAM
from core.common.mixins import ConceptDictionaryCreateMixin, ListWithHeadersMixin, ConceptDictionaryUpdateMixin
from core.common.permissions import CanViewConceptDictionary, CanEditConceptDictionary
from core.common.utils import compact_dict_by_values, parse_boolean_query_param
from core.common.views import BaseAPIView


class CollectionBaseView(BaseAPIView):
    lookup_field = 'collection'
    pk_field = 'mnemonic'
    model = Collection
    permission_classes = (CanViewConceptDictionary,)
    queryset = Collection.objects.filter(is_active=True)

    def set_parent_resource(self):
        from core.orgs.models import Organization
        from core.users.models import UserProfile
        org = self.kwargs.get('org', None)
        user = self.kwargs.get('user', None)
        parent_resource = None
        if org:
            parent_resource = Organization.objects.filter(mnemonic=org).first()
        if user:
            parent_resource = UserProfile.objects.filter(username=user).first()

        self.kwargs['parent_resource'] = self.parent_resource = parent_resource

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context.update({'request': self.request, INCLUDE_REFERENCES_PARAM: self.should_include_references()})
        return context

    @staticmethod
    def get_detail_serializer(obj):
        return CollectionDetailSerializer(obj)

    def get_filter_params(self, default_version_to_head=True):
        query_params = self.request.query_params
        params = dict()

        version = query_params.get('version', None) or self.kwargs.get('version', None)
        if not version and default_version_to_head:
            version = HEAD
        params['version'] = version
        params['user'] = query_params.get('user', None) or self.kwargs.get('user', None)
        params['org'] = query_params.get('org', None) or self.kwargs.get('org', None)
        params['collection'] = query_params.get('collection', None) or self.kwargs.get('collection', None)
        params['is_latest'] = self.kwargs.get('is_latest', None)
        params['contains'] = query_params.get('contains', None) or self.kwargs.get('contains', None)
        params['include_references'] = self.should_include_references()
        return params

    def get_queryset(self):
        return Collection.get_base_queryset(compact_dict_by_values(self.get_filter_params()))

    def should_include_references(self):
        return self.request.query_params.get(INCLUDE_REFERENCES_PARAM, 'false').lower() == 'true'


class CollectionVersionBaseView(CollectionBaseView):
    def get_filter_params(self, default_version_to_head=False):
        return super().get_filter_params(default_version_to_head)


class CollectionListView(CollectionBaseView, ConceptDictionaryCreateMixin, ListWithHeadersMixin):
    serializer_class = CollectionListSerializer

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return CollectionDetailSerializer if self.is_verbose(self.request) else CollectionListSerializer
        if self.request.method == 'POST':
            return CollectionCreateSerializer

        return CollectionListSerializer

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def get_csv_rows(self, queryset=None):
        if not queryset:
            queryset = self.get_queryset()

        values = queryset.values('mnemonic', 'name', 'full_name', 'collection_type', 'description', 'default_locale',
                                 'supported_locales', 'website', 'external_id', 'updated_at', 'updated_by', 'uri')

        for value in values:
            value['Owner'] = Collection.objects.get(uri=value['uri']).parent.mnemonic
            value['Collection ID'] = value.pop('mnemonic')
            value['Collection Name'] = value.pop('name')
            value['Collection Full Name'] = value.pop('full_name')
            value['Collection Type'] = value.pop('collection_type')
            value['Description'] = value.pop('description')
            value['Default Locale'] = value.pop('default_locale')
            value['Supported Locales'] = ",".join(value.pop('supported_locales'))
            value['Website'] = value.pop('website')
            value['External ID'] = value.pop('external_id')
            value['Last Updated'] = value.pop('updated_at')
            value['Updated By'] = value.pop('updated_by')
            value['URI'] = value.pop('uri')

        values.field_names.extend([
            'Owner', 'Collection ID', 'Collection Name', 'Collection Full Name', 'Collection Type', 'Description',
            'Default Locale', 'Supported Locales', 'Website', 'External ID', 'Last Updated', 'Updated By', 'URI'
        ])
        del values.field_names[0:12]
        return values


class CollectionRetrieveUpdateDestroyView(
        CollectionBaseView, ConceptDictionaryUpdateMixin, RetrieveAPIView, DestroyAPIView
):
    serializer_class = CollectionDetailSerializer

    def get_object(self, queryset=None):
        return self.get_queryset().filter(is_active=True).order_by('-created_at').first()

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewConceptDictionary()]

        return [CanEditConceptDictionary()]

    def destroy(self, request, *args, **kwargs):
        collection = self.get_object()
        try:
            collection.delete()
        except Exception as ex:
            return Response({'detail': ex.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'detail': 'Successfully deleted collection.'}, status=status.HTTP_204_NO_CONTENT)


class CollectionReferencesView(
        CollectionBaseView, ConceptDictionaryUpdateMixin, RetrieveAPIView, DestroyAPIView, ListWithHeadersMixin
):
    serializer_class = CollectionDetailSerializer

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewConceptDictionary()]

        return [CanEditConceptDictionary()]

    def get_object(self, queryset=None):
        instance = super().get_queryset().filter(is_active=True).order_by('-created_at').first()

        if not instance:
            raise Http404('No Collection matches the given query.')

        return instance

    def get_queryset(self):
        search_query = self.request.query_params.get('q', '')
        sort = self.request.query_params.get('search_sort', 'ASC')
        if sort == 'ASC':
            sort = ''
        else:
            sort = '-'

        instance = self.get_object()
        queryset = instance.references

        if search_query:
            queryset = queryset.filter(expression__iexact=search_query).order_by(sort + 'expression')

        return queryset.all()

    def retrieve(self, request, *args, **kwargs):
        self.serializer_class = CollectionReferenceSerializer
        return self.list(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        expressions = request.data.get("references") or request.data.get("expressions")
        cascade_mappings_flag = request.data.get('cascade', 'none')

        if not expressions:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if expressions == '*':
            expressions = list(instance.references.values_list('expression', flat=True))
        if self.cascade_mapping_resolver(cascade_mappings_flag):
            expressions += self.get_related_mappings_with_version_information(instance, expressions)

        instance.delete_references(expressions)
        return Response({'message': 'ok!'}, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):  # pylint: disable=too-many-locals,unused-argument # Fixme: Sny
        print("*******", request.data)
        collection = self.get_object()

        cascade_mappings_flag = request.query_params.get('cascade', 'none')
        data = request.data.get('data')
        concept_expressions = data.get('concepts', [])
        mapping_expressions = data.get('mappings', [])
        expressions = data.get('expressions', [])
        cascade_mappings = self.cascade_mapping_resolver(cascade_mappings_flag)

        (added_references, errors) = collection.add_expressions(
            data, self.get_host_url(), request.user, cascade_mappings
        )

        all_expressions = expressions + concept_expressions + mapping_expressions

        added_expressions = [reference.expression for reference in added_references]
        added_original_expressions = set(
            [reference.original_expression for reference in added_references] + all_expressions
        )

        response = []

        for expression in added_original_expressions:
            response_item = self.create_response_item(added_expressions, errors, expression)
            if response_item:
                response.append(response_item)

        return Response(response, status=status.HTTP_200_OK)

    def create_response_item(self, added_expressions, errors, expression):
        adding_expression_failed = len(errors) > 0 and expression in errors
        if adding_expression_failed:
            return self.create_error_message(errors, expression)
        return self.create_success_message(added_expressions, expression)

    def create_success_message(self, added_expressions, expression):
        message = self.select_update_message(expression)

        references = list(filter(lambda expr: expr.startswith(expression), added_expressions))
        if len(references) < 1:
            return None

        return {
            'added': True,
            'expression': references[0],
            'message': message
        }

    @staticmethod
    def create_error_message(errors, expression):
        error_message = errors.get(expression, {})
        return {
            'added': False,
            'expression': expression,
            'message': error_message
        }

    def select_update_message(self, expression):
        adding_head_version = not is_version_specified(expression)

        expression_parts = expression.split('/')
        resource_type = expression_parts[5]

        if adding_head_version:
            return self.adding_to_head_message_by_type(resource_type)

        resource_name = expression_parts[6]
        return self.version_added_message_by_type(resource_name, self.parent_resource.name, resource_type)

    @staticmethod
    def adding_to_head_message_by_type(resource_type):
        if resource_type == 'concepts':
            return HEAD_OF_CONCEPT_ADDED_TO_COLLECTION
        return HEAD_OF_MAPPING_ADDED_TO_COLLECTION

    @staticmethod
    def version_added_message_by_type(resource_name, collection_name, resource_type):
        if resource_type == 'concepts':
            return CONCEPT_ADDED_TO_COLLECTION_FMT.format(resource_name, collection_name)
        return MAPPING_ADDED_TO_COLLECTION_FMT.format(resource_name, collection_name)

    @staticmethod
    def cascade_mapping_resolver(cascade_mappings_flag):
        cascade_mappings_flag_resolver = {
            'none': False,
            'sourcemappings': True
        }

        return cascade_mappings_flag_resolver.get(cascade_mappings_flag.lower(), False)

    def get_related_mappings_with_version_information(self, instance, expressions):
        related_mappings = []

        for expression in expressions:
            if is_concept(expression):
                concept = CollectionReference.get_concept_head_from_expression(expression)
                related_mappings += concept.get_unidirectional_mappings()

        return self.get_version_information_of_related_mappings(instance, related_mappings)

    @staticmethod
    def get_version_information_of_related_mappings(instance, related_mappings):
        return list(
            instance.references.filter(
                expression__in=[mapping.url for mapping in related_mappings]
            ).values_list('expression', flat=True)
        )


class CollectionVersionListView(CollectionVersionBaseView, mixins.CreateModelMixin, ListWithHeadersMixin):
    released_filter = None
    processing_filter = None

    def get_serializer_class(self):
        if self.request.method in ['GET', 'HEAD']:
            return CollectionVersionDetailSerializer if self.is_verbose(self.request) else CollectionListSerializer
        if self.request.method == 'POST':
            return CollectionCreateSerializer

        return CollectionListSerializer

    def get(self, request, *args, **kwargs):
        self.released_filter = parse_boolean_query_param(request, RELEASED_PARAM, self.released_filter)
        self.processing_filter = parse_boolean_query_param(request, PROCESSING_PARAM, self.processing_filter)
        return self.list(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.create(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        head_object = self.get_queryset().first()
        payload = {
            "mnemonic": head_object.mnemonic, "id": head_object.mnemonic, "name": head_object.name, **request.data,
            "organization_id": head_object.organization_id, "user_id": head_object.user_id,
            'version': request.data.pop('id', None)
        }
        serializer = self.get_serializer(data=payload)
        if serializer.is_valid():
            try:
                instance = serializer.create_version(payload)
                if serializer.is_valid():
                    serializer = CollectionDetailSerializer(instance, context={'request': request})
                    return Response(serializer.data, status=status.HTTP_201_CREATED)
            except IntegrityError as ex:
                return Response(
                    dict(
                        error=str(ex), detail='Collection version  \'%s\' already exist. ' % serializer.data.get('id')
                    ),
                    status=status.HTTP_409_CONFLICT
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.released_filter is not None:
            queryset = queryset.filter(released=self.released_filter)
        return queryset.order_by('-created_at')


class CollectionVersionRetrieveUpdateDestroyView(CollectionBaseView):
    pass
