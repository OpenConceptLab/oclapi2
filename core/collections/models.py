import time

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import UniqueConstraint, F, QuerySet
from django.utils import timezone
from django.utils.functional import cached_property
from pydash import get, compact

from core.collections.constants import (
    COLLECTION_TYPE, REFERENCE_ALREADY_EXISTS, CONCEPT_FULLY_SPECIFIED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE,
    CONCEPT_PREFERRED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE, COLLECTION_VERSION_TYPE,
    REFERENCE_TYPE_CHOICES, CONCEPT_REFERENCE_TYPE, MAPPING_REFERENCE_TYPE, SOURCE_MAPPINGS, SOURCE_TO_CONCEPTS,
    TRANSFORM_TO_RESOURCE_VERSIONS, COLLECTION_REFERENCE_TYPE)
from core.collections.parsers import CollectionReferenceParser
from core.collections.utils import is_concept, is_mapping
from core.common.constants import (
    DEFAULT_REPOSITORY_TYPE, ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT,
    ES_REQUEST_TIMEOUT, ES_REQUEST_TIMEOUT_ASYNC)
from core.common.models import ConceptContainerModel, BaseResourceModel
from core.common.tasks import seed_children_to_expansion, batch_index_resources, index_expansion_concepts, \
    index_expansion_mappings
from core.common.utils import drop_version, to_owner_uri, generate_temp_version, es_id_in, \
    es_wildcard_search, get_resource_class_from_resource_name, get_exact_search_fields, to_snake_case, \
    es_exact_search, es_to_pks, batch_qs, split_list_by_condition
from core.concepts.constants import LOCALES_FULLY_SPECIFIED
from core.concepts.models import Concept
from core.mappings.models import Mapping


class Collection(ConceptContainerModel):
    OBJECT_TYPE = COLLECTION_TYPE
    OBJECT_VERSION_TYPE = COLLECTION_VERSION_TYPE
    es_fields = {
        'collection_type': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'mnemonic': {'sortable': True, 'filterable': True, 'exact': True},
        'name': {'sortable': True, 'filterable': True, 'exact': True},
        'last_update': {'sortable': True, 'filterable': False, 'default': 'desc'},
        'locale': {'sortable': False, 'filterable': True, 'facet': True},
        'owner': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'custom_validation_schema': {'sortable': False, 'filterable': True, 'facet': True},
        'canonical_url': {'sortable': True, 'filterable': True},
        'experimental': {'sortable': False, 'filterable': False, 'facet': False},
        'external_id': {'sortable': False, 'filterable': True, 'facet': False, 'exact': False},
    }

    class Meta:
        db_table = 'collections'
        constraints = [
            UniqueConstraint(
                fields=['mnemonic', 'version', 'organization'],
                name="org_collection_unique",
                condition=models.Q(user=None),
            ),
            UniqueConstraint(
                fields=['mnemonic', 'version', 'user'],
                name="user_collection_unique",
                condition=models.Q(organization=None),
            )
        ]
        indexes = [] + ConceptContainerModel.Meta.indexes

    collection_type = models.TextField(blank=True)
    preferred_source = models.TextField(blank=True)
    repository_type = models.TextField(default=DEFAULT_REPOSITORY_TYPE, blank=True)
    custom_resources_linked_source = models.TextField(blank=True)
    immutable = models.BooleanField(null=True, blank=True, default=None)
    locked_date = models.DateTimeField(null=True, blank=True)
    autoexpand_head = models.BooleanField(default=True, null=True)
    autoexpand = models.BooleanField(default=True, null=True)
    expansion_uri = models.TextField(null=True, blank=True)

    def set_active_concepts(self):
        if self.expansion_uri:
            self.active_concepts = self.expansion.concepts.filter(retired=False, is_active=True).count()

    def set_active_mappings(self):
        if self.expansion_uri:
            self.active_mappings = self.expansion.mappings.filter(retired=False, is_active=True).count()

    @staticmethod
    def get_search_document():
        from core.collections.documents import CollectionDocument
        return CollectionDocument

    @classmethod
    def get_base_queryset(cls, params):
        collection = params.pop('collection', None)
        contains_uri = params.pop('contains', None)
        include_references = params.pop('include_references', None) in [True, 'true']
        queryset = super().get_base_queryset(params)
        if collection:
            queryset = queryset.filter(cls.get_exact_or_criteria('mnemonic', collection))
        if contains_uri:
            queryset = queryset.filter(
                references__expression=contains_uri, public_access__in=[ACCESS_TYPE_EDIT, ACCESS_TYPE_VIEW]
            )
        if include_references:
            queryset = queryset.prefetch_related('references')

        return queryset

    @property
    def collection(self):
        return self.mnemonic

    @staticmethod
    def get_resource_url_kwarg():
        return 'collection'

    def update_version_data(self, head):
        super().update_version_data(head)
        self.collection_type = head.collection_type
        self.preferred_source = head.preferred_source
        self.repository_type = head.repository_type
        self.custom_resources_linked_source = head.custom_resources_linked_source
        self.immutable = head.immutable
        self.locked_date = head.locked_date

    @property
    def should_auto_expand(self):
        if self.is_head:
            return self.autoexpand_head

        return self.autoexpand

    def validate(self, reference):
        if self.should_auto_expand:
            reference.full_clean()
        else:
            reference.last_resolved_at = None
        if reference.expression and self.references.filter(
                expression=reference.expression, include=reference.include).exists():
            raise ValidationError({reference.expression: [REFERENCE_ALREADY_EXISTS]})

        if self.is_openmrs_schema and self.expansion_uri:
            if reference._concepts is None or reference._concepts.count() == 0:  # pylint: disable=protected-access
                return
            for concept in reference._concepts:   # pylint: disable=protected-access
                self.check_concept_uniqueness_in_collection_and_locale_by_name_attribute(
                    concept, attribute='type__in', value=LOCALES_FULLY_SPECIFIED,
                    error_message=CONCEPT_FULLY_SPECIFIED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE
                )
                self.check_concept_uniqueness_in_collection_and_locale_by_name_attribute(
                    concept, attribute='locale_preferred', value=True,
                    error_message=CONCEPT_PREFERRED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE
                )

    def check_concept_uniqueness_in_collection_and_locale_by_name_attribute(
            self, concept, attribute, value, error_message
    ):
        other_concepts_in_collection = self.expansion.concepts
        if not other_concepts_in_collection.exists():
            return

        matching_names_in_concept = {}
        names = concept.names.filter(**{attribute: value})

        for name in names:
            validation_error = dict(names=[error_message])
            # making sure names in the submitted concept meet the same rule
            name_key = name.locale + name.name
            if name_key in matching_names_in_concept:
                raise ValidationError(validation_error)

            matching_names_in_concept[name_key] = True
            if other_concepts_in_collection.filter(
                    names__name=name.name, names__locale=name.locale, **{f"names__{attribute}": value}
            ).exists():
                raise ValidationError(validation_error)

    @transaction.atomic
    def add_expressions(
            self, data, user, cascade=False, transform=False):
        parser = CollectionReferenceParser(data, transform, cascade, user)
        parser.parse()
        parser.to_reference_structure()
        references = parser.to_objects()

        return self.add_references(references, user)

    def add_references(self, references, user=None):
        errors = {}
        added_references = []
        for reference in references:
            reference.expression = reference.build_expression()
            reference.collection = self
            reference.created_by = user
            try:
                self.validate(reference)
                reference.save()
            except Exception as ex:
                errors[reference.expression] = ex.messages if hasattr(ex, 'messages') else ex
                continue
            if reference.id:
                added_references.append(reference)

        if self.expansion_uri:
            self.expansion.add_references(added_references)
        if user and user.is_authenticated:
            self.updated_by = user
        self.save()
        self.update_children_counts()
        return added_references, errors

    def seed_references(self):
        head = self.head
        if head:
            for reference in head.references.all():
                new_reference = reference.clone(last_resolved_at=timezone.now(), collection=self)
                new_reference.save()
                new_reference.concepts.set(reference.concepts.all())
                new_reference.mappings.set(reference.mappings.all())

    @staticmethod
    def is_validation_necessary():
        return False

    @property
    def expansions_count(self):
        return self.expansions.count()

    def delete_references(self, expressions):
        if expressions == '*':  # Deprecated: Old way
            references_to_be_deleted = self.references
            if self.expansion_uri:
                self.expansion.delete_expressions(expressions)
        else:
            references_to_be_deleted = self.references.filter(expression__in=expressions)
            if self.expansion_uri:
                self.expansion.delete_references(references_to_be_deleted)

        references_to_be_deleted.all().delete()

    def get_cascaded_mapping_uris_from_concept_expressions(self, expressions):
        mapping_uris = []

        if not self.expansion_uri:
            return mapping_uris

        for expression in expressions:
            if is_concept(expression):
                mapping_uris += list(
                    self.expansion.mappings.filter(
                        from_concept__uri__icontains=drop_version(expression)).values_list('uri', flat=True)
                )

        return mapping_uris

    def cascade_children_to_expansion(self, expansion_data=None, index=True, sync=False):  # pylint: disable=arguments-differ
        if not expansion_data:
            expansion_data = {}
        should_auto_expand = self.should_auto_expand

        if should_auto_expand and not self.expansions.exists() and not get(expansion_data, 'mnemonic'):
            expansion_data['mnemonic'] = Expansion.get_auto_expand_mnemonic(self.version)
            expansion_data['is_processing'] = True
        expansion = Expansion.persist(index=index, **expansion_data, collection_version=self, sync=sync)

        if should_auto_expand and not self.expansion_uri:
            self.expansion_uri = expansion.uri
            self.save()

        return expansion

    def index_children(self):
        if self.expansion_uri:
            from core.concepts.documents import ConceptDocument
            from core.mappings.documents import MappingDocument

            self.batch_index(self.expansion.concepts, ConceptDocument)
            self.batch_index(self.expansion.mappings, MappingDocument)

    @property
    def expansion(self):
        if self.expansion_uri:
            return self.expansions.filter(uri=self.expansion_uri).first()

        return None

    @property
    def active_references(self):
        return self.references.count()

    @property
    def last_concept_update(self):
        updated_at = None
        if self.expansion_uri and self.expansion.concepts.exists():
            updated_at = self.expansion.concepts.latest('updated_at').updated_at
        return updated_at

    @property
    def last_mapping_update(self):
        updated_at = None
        if self.expansion_uri and self.expansion.mappings.exists():
            updated_at = self.expansion.mappings.latest('updated_at').updated_at
        return updated_at

    @property
    def expansions_url(self):
        return self.uri + 'expansions/'


class ReferencedConcept(models.Model):
    reference = models.ForeignKey('collections.CollectionReference', on_delete=models.CASCADE)
    concept = models.ForeignKey('concepts.Concept', on_delete=models.CASCADE)


class ReferencedMapping(models.Model):
    reference = models.ForeignKey('collections.CollectionReference', on_delete=models.CASCADE)
    mapping = models.ForeignKey('mappings.Mapping', on_delete=models.CASCADE)


class CollectionReference(models.Model):
    class Meta:
        db_table = 'collection_references'

    OPERATOR_EQUAL = '='
    OPERATOR_IN = 'in'
    ALLOWED_FILTER_OPS = [OPERATOR_EQUAL, OPERATOR_IN]

    _concepts = None
    _mappings = None
    original_expression = None

    # core
    id = models.BigAutoField(primary_key=True)
    expression = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_resolved_at = models.DateTimeField(default=timezone.now, null=True)
    reference_type = models.CharField(choices=REFERENCE_TYPE_CHOICES, default=CONCEPT_REFERENCE_TYPE, max_length=10)
    created_by = models.ForeignKey('users.UserProfile', null=True, blank=True, on_delete=models.SET_NULL)

    # FHIR specs
    filter = models.JSONField(null=True, blank=True)
    display = models.TextField(null=True, blank=True)
    namespace = models.TextField(null=True, blank=True)
    code = models.CharField(null=True, blank=True, max_length=255)
    system = models.TextField(null=True, blank=True)
    version = models.CharField(null=True, blank=True, max_length=255)
    valueset = ArrayField(models.TextField(), null=True, blank=True)
    cascade = models.JSONField(null=True, blank=True)
    transform = models.CharField(null=True, blank=True, max_length=255)
    include = models.BooleanField(default=True)

    resource_version = models.CharField(null=True, blank=True, max_length=255)

    # associations
    concepts = models.ManyToManyField('concepts.Concept', related_name='references', through=ReferencedConcept)
    mappings = models.ManyToManyField('mappings.Mapping', related_name='references', through=ReferencedMapping)
    collection = models.ForeignKey('collections.Collection', related_name='references', on_delete=models.CASCADE)

    @property
    def resource_type(self):
        return COLLECTION_REFERENCE_TYPE

    def clone(self, **kwargs):
        return CollectionReference(
            expression=self.expression,
            reference_type=self.reference_type,
            created_by=self.created_by,
            filter=self.filter,
            display=self.display,
            namespace=self.namespace,
            code=self.code,
            system=self.system,
            version=self.version,
            valueset=self.valueset,
            cascade=self.cascade,
            transform=self.transform,
            include=self.include,
            resource_version=self.resource_version,
            **kwargs,
        )

    def build_expression(self):
        if self.expression:
            return self.expression

        expression = ''
        if self.system:
            is_canonical = self.system.startswith('http://') or self.system.startswith('https://')
            expression = self.system
            if self.version:
                expression += '|' + self.version if is_canonical else self.version
        elif self.valueset and isinstance(self.valueset, list):
            expression = self.valueset[0]  # pylint: disable=unsubscriptable-object

        is_canonical = expression.startswith('https://') or expression.startswith('http://')
        if self.code or self.filter or not is_canonical:
            if self.is_concept:
                expression += 'concepts/' if expression.endswith('/') else '/concepts/'
            elif self.is_mapping:
                expression += 'mappings/' if expression.endswith('/') else '/mappings/'

            if expression:
                if self.code:
                    expression += self.code + '/'
                    if self.resource_version:
                        expression += self.resource_version + '/'
                elif self.filter:
                    expression += '?' + self.filter_to_querystring()
        return expression

    @property
    def concepts_count(self):
        return self.concepts.count()

    @property
    def mappings_count(self):
        return self.mappings.count()

    @cached_property
    def uri(self):
        return self.calculate_uri(self.collection)

    def calculate_uri(self, collection):
        if collection:
            return f'{collection.uri}references/{self.id}/'
        return None

    @property
    def without_version(self):
        return drop_version(self.expression)

    def fetch_concepts(self, refetch=False):
        if not get(self, '_fetched') or refetch:
            self._concepts, self._mappings = self.get_concepts()
            self._fetched = True

    def fetch_mappings(self, refetch=False):
        if not get(self, '_fetched') or refetch:
            self._mappings = self.get_mappings()
            self._fetched = True

    def get_concepts(self):
        queryset = self.get_resource_queryset_from_system_and_valueset(Concept, 'concepts')
        mapping_queryset = Mapping.objects.none()

        if self.code:
            queryset = queryset.filter(mnemonic=self.code)
            if self.resource_version:
                queryset = queryset.filter(version=self.resource_version)
        if self.cascade:
            cascade_params = self.get_concept_cascade_params()
            for concept in queryset:
                result = concept.cascade(**cascade_params)
                queryset |= result['concepts']
                mapping_queryset |= result['mappings']

        if self.should_apply_filter():
            queryset = self.apply_filters(queryset, Concept)

        if self.should_transform_to_latest_version():
            queryset = self.transform_to_latest_version(queryset, Concept)
            if self.code:
                concept = queryset.first()
                self.resource_version = concept.version
                self.expression = concept.uri
            if mapping_queryset.exists():
                mapping_queryset = self.transform_to_latest_version(mapping_queryset, Mapping)
        return queryset, mapping_queryset

    def get_mappings(self):
        queryset = self.get_resource_queryset_from_system_and_valueset(Mapping, 'mappings')

        if self.code:
            queryset = queryset.filter(mnemonic=self.code)
            if self.resource_version:
                queryset = queryset.filter(version=self.resource_version)

        if self.should_apply_filter():
            queryset = self.apply_filters(queryset, Mapping)

        if self.should_transform_to_latest_version():
            queryset = self.transform_to_latest_version(queryset, Mapping)
            if self.code:
                mapping = queryset.first()
                self.resource_version = mapping.version
                self.expression = mapping.uri
        return queryset

    @staticmethod
    def transform_to_latest_version(queryset, klass):
        ids = []
        resources = klass.objects.filter(id__in=queryset.values_list('id', flat=True))
        for resource in resources.filter(id=F('versioned_object_id')):
            ids.append(resource.get_latest_version().id)
        if ids:
            queryset = resources.exclude(id=F('versioned_object_id')) | klass.objects.filter(id__in=ids)

        return queryset

    def should_transform_to_latest_version(self):
        return not self.resource_version and self.transform

    def should_apply_filter(self):
        return not self.code and self.filter

    def get_concept_cascade_params(self):
        if isinstance(self.cascade, dict) and self.cascade and 'method' in self.cascade:
            method = self.cascade.pop('method')
        else:
            method = self.cascade

        cascade_params = {
            'source_mappings': method == SOURCE_MAPPINGS,
            'source_to_concepts': method == SOURCE_TO_CONCEPTS,
            'cascade_levels': 1 if self.cascade == method else get(self.cascade, 'level', '*')
        }
        if isinstance(self.cascade, dict):
            cascade_params = {**cascade_params, **self.cascade}
        return cascade_params

    def __is_exact_search_filter(self):
        return bool(next(
            (filter_def for filter_def in self.filter if  # pylint:disable=not-an-iterable
             filter_def['property'] == 'exact_match' and filter_def['value'] == 'on'),
            False
        ))

    def apply_filters(self, queryset, resource_klass):
        if self.filter:
            pks = []
            document = resource_klass.get_search_document()
            search = document.search()
            is_exact_search = self.__is_exact_search_filter()
            for filter_def in self.filter:  # pylint: disable=not-an-iterable
                if to_snake_case(filter_def['property']) == 'exact_match':
                    continue
                val = filter_def['value']
                if filter_def['property'] == 'q':
                    exact_search_fields = get_exact_search_fields(resource_klass)
                    if is_exact_search:
                        search = es_exact_search(search, val, exact_search_fields)
                    else:
                        name_attr = '_name' if self.is_concept else 'name'
                        search = es_wildcard_search(search, val, exact_search_fields, name_attr)
                else:
                    search = search.filter("match", **{to_snake_case(filter_def["property"]): filter_def["value"]})

            for _queryset in batch_qs(queryset.order_by('id'), 500):
                # iterating on queryset because ES has max_clause limit default to 1024
                search_within_queryset = es_id_in(search, list(_queryset.values_list('id', flat=True)))
                pks += es_to_pks(search_within_queryset.params(request_timeout=ES_REQUEST_TIMEOUT_ASYNC))
            return resource_klass.objects.filter(id__in=set(pks)) if pks else resource_klass.objects.none()

        return queryset

    # returns intersection of system and valueset resources considering creator permissions
    def get_resource_queryset_from_system_and_valueset(self, resource_klass, resource_relation):
        system_version = self.resolve_system_version()
        valueset_versions = self.resolve_valueset_versions()
        queryset = None
        if system_version and system_version.can_view_all_content(self.created_by):
            queryset = get(system_version, resource_relation).filter()
            if system_version.is_head and not self.resource_version:
                queryset = queryset.filter(
                    is_latest_version=True
                ) if self.transform else queryset.filter(id=F('versioned_object_id'))

        if valueset_versions:
            for valueset in valueset_versions:
                if valueset.expansion_uri and valueset.can_view_all_content(self.created_by):
                    rel = get(valueset.expansion, resource_relation)
                    if queryset is None:
                        queryset = rel.all()
                    else:
                        queryset &= rel.all()
        if not system_version and not valueset_versions and self.expression:
            queryset = resource_klass.from_uri_queryset(self.expression)

        return queryset

    def resolve_system_version(self):
        if self.system:
            from core.sources.models import Source
            version = Source.resolve_reference_expression(self.system, self.namespace, self.version)
            if version.id:
                return version
        return None

    def resolve_valueset_versions(self):
        versions = []
        if isinstance(self.valueset, list):
            for valueset in self.valueset:  # pylint: disable=not-an-iterable
                if valueset:
                    version = Collection.resolve_reference_expression(valueset, self.namespace)
                    if version.id:
                        versions.append(version)
        return versions

    def clean(self):
        if not self.is_valid_filter():
            raise ValidationError(dict(filter=['Invalid filter schema.']))

        self.original_expression = str(self.expression)
        if self.transform and self.transform.lower() != TRANSFORM_TO_RESOURCE_VERSIONS:
            self.transform = None

        self.evaluate()
        is_resolved = bool((self._mappings and self._mappings.exists()) or (self._concepts and self._concepts.exists()))
        if not is_resolved:
            self.last_resolved_at = None
        if not self.reference_type:
            self.reference_type = MAPPING_REFERENCE_TYPE if self.is_mapping else CONCEPT_REFERENCE_TYPE

        if self.expression is None:
            self.expression = self.build_expression()

    def filter_to_querystring(self):
        if self.filter:
            queries = []
            for filter_def in self.filter:  # pylint: disable=not-an-iterable
                value = ','.join(filter_def['value']) if isinstance(filter_def['value'], list) else filter_def['value']
                queries.append(f'{filter_def["property"]}={value}')
            return '&'.join(queries)
        return None

    @cached_property
    def expression_with_filters(self):
        querystring = self.filter_to_querystring()
        if querystring:
            joiner = '&' if '?' in self.expression else '&'
            return f"{self.expression}{joiner}{querystring}"
        return self.expression

    def is_valid_filter(self):
        if not self.filter:
            return True

        if not isinstance(self.filter, list):
            return False
        if len(self.filter) != len(compact(self.filter)):
            return False

        return all(map(self.__is_valid_filter_schema, self.filter))

    def get_allowed_filter_properties(self):
        common = ['q', 'exact_match']
        if self.is_concept:
            return [*Concept.es_fields.keys(), *common]
        if self.is_mapping:
            return [*Mapping.es_fields.keys(), *common]
        return common

    def __is_valid_filter_schema(self, filter_def):
        return isinstance(filter_def, dict) and \
               sorted(filter_def.keys()) == sorted(['property', 'op', 'value']) and \
               {type(val) for val in filter_def.values()} == {str} and \
               filter_def['op'] in self.ALLOWED_FILTER_OPS and \
               to_snake_case(filter_def['property']) in self.get_allowed_filter_properties()

    def save(self, force_insert=False, force_update=False, using=None,
             update_fields=None):
        super().save(force_insert=force_insert, force_update=force_update, using=using, update_fields=update_fields)

        if self.id and get(self, '_fetched'):
            if self._concepts is not None and self._concepts.exists():
                self.concepts.set(self._concepts)
            if self._mappings is not None and self._mappings.exists():
                self.mappings.set(self._mappings)

    @property
    def is_concept(self):
        return self.reference_type == 'concepts' or is_concept(self.expression)

    @property
    def is_mapping(self):
        return self.reference_type == 'mappings' or is_mapping(self.expression)

    def evaluate(self):
        if self.is_concept:
            self.fetch_concepts()
        elif self.is_mapping:
            self.fetch_mappings()

        self._fetched = True

    def get_related_uris(self):
        self.fetch_concepts()
        return [*set(self._concepts.values_list('uri', flat=True)), *set(self._mappings.values_list('uri', flat=True))]


def default_expansion_parameters():
    return {
        "filter": "",
        "date": "",
        "count": 0,
        "offset": 0,
        "includeDesignations": True,
        "activeOnly": False,
        "includeDefinition": False,
        "excludeNested": True,
        "excludeNotForUI": True,
        "excludePostCoordinated": True,
        "exclude-system": "",
        "system-version": "",
        "check-system-version": "",
        "force-system-version": ""
    }


class Expansion(BaseResourceModel):
    class Meta:
        db_table = 'collection_expansions'
        indexes = [] + BaseResourceModel.Meta.indexes

    parameters = models.JSONField(default=default_expansion_parameters)
    canonical_url = models.URLField(null=True, blank=True)
    text = models.TextField(null=True, blank=True)
    concepts = models.ManyToManyField('concepts.Concept', blank=True, related_name='expansion_set')
    mappings = models.ManyToManyField('mappings.Mapping', blank=True, related_name='expansion_set')
    collection_version = models.ForeignKey(
        'collections.Collection', related_name='expansions', on_delete=models.CASCADE)
    is_processing = models.BooleanField(default=False)

    @property
    def is_auto_generated(self):
        return self.mnemonic == self.auto_generated_mnemonic

    @property
    def auto_generated_mnemonic(self):
        return self.get_auto_expand_mnemonic(get(self, 'collection_version.version'))

    @staticmethod
    def get_auto_expand_mnemonic(version):
        return f"autoexpand-{version}"

    @staticmethod
    def get_resource_url_kwarg():
        return 'expansion'

    @staticmethod
    def get_url_kwarg():
        return 'expansion'

    @property
    def is_default(self):
        return self.uri == self.collection_version.expansion_uri

    @property
    def expansion(self):
        return self.mnemonic

    @property
    def active_concepts(self):
        return self.concepts.count()

    @property
    def active_mappings(self):
        return self.mappings.count()

    @property
    def owner_url(self):
        return to_owner_uri(self.uri)

    def apply_parameters(self, queryset, is_concept_queryset):
        parameters = ExpansionParameters(self.parameters, is_concept_queryset)
        return parameters.apply(queryset)

    def index_concepts(self):
        if self.concepts.exists():
            if get(settings, 'TEST_MODE', False):
                index_expansion_concepts(self.id)
            else:
                index_expansion_concepts.apply_async((self.id, ), queue='indexing')

    def index_mappings(self):
        if self.mappings.exists():
            if get(settings, 'TEST_MODE', False):
                index_expansion_mappings(self.id)
            else:
                index_expansion_mappings.apply_async((self.id, ), queue='indexing')

    @staticmethod
    def to_ref_list(references):
        if isinstance(references, CollectionReference):
            return [references]
        if isinstance(references, list):
            return references
        return references.all()

    @classmethod
    def to_ref_list_separated(cls, references):
        refs = cls.to_ref_list(references)
        if isinstance(refs, QuerySet):
            return refs.filter(include=True), refs.exclude(include=True)
        return split_list_by_condition(refs, lambda ref: ref.include)

    def delete_references(self, references):
        refs, _ = self.to_ref_list_separated(references)

        index_concepts = False
        index_mappings = False
        for reference in refs:
            concepts = reference.concepts
            if concepts.exists():
                index_concepts = True
                self.concepts.set(self.concepts.exclude(id__in=concepts.values_list('id', flat=True)))
            mappings = reference.mappings
            if mappings.exists():
                index_mappings = True
                self.mappings.set(self.mappings.exclude(id__in=mappings.values_list('id', flat=True)))
        if index_concepts:
            self.index_concepts()
        if index_mappings:
            self.index_mappings()

        references_to_readd = self.collection_version.references.exclude(
            id__in=[ref.id for ref in self.to_ref_list(references)])
        self.add_references(references_to_readd, True, True, False)

    def delete_expressions(self, expressions):  # Deprecated: Old way, must use delete_references instead
        concepts_filters = None
        mappings_filters = None
        if expressions == '*':
            if self.concepts.exists():
                concepts_filters = dict(id__in=list(self.concepts.values_list('id', flat=True)))
                self.concepts.clear()
            if self.mappings.exists():
                mappings_filters = dict(id__in=list(self.mappings.values_list('id', flat=True)))
                self.mappings.clear()
        else:
            concepts_filters = dict(uri__in=expressions)
            mappings_filters = dict(uri__in=expressions)
            self.concepts.set(self.concepts.exclude(**concepts_filters))
            self.mappings.set(self.mappings.exclude(**mappings_filters))

        if not get(settings, 'TEST_MODE', False):
            batch_index_resources.apply_async(('concept', concepts_filters), queue='indexing')
            batch_index_resources.apply_async(('mapping', mappings_filters), queue='indexing')

    def add_references(self, references, index=True, is_adding_all_references=False, attempt_reevaluate=True):
        include_refs, exclude_refs = self.to_ref_list_separated(references)

        if not is_adding_all_references:
            existing_exclude_refs = self.collection_version.references.exclude(include=True)
            if isinstance(exclude_refs, QuerySet):
                exclude_refs |= existing_exclude_refs
            else:
                exclude_refs += [*existing_exclude_refs.all()]

        index_concepts = False
        index_mappings = False
        is_auto_generated = self.is_auto_generated

        def get_ref_results(ref):
            if attempt_reevaluate and not is_auto_generated:
                _concepts, _mappings = ref.get_concepts()
                _mappings |= ref.get_mappings()
            else:
                _concepts = ref.concepts.all()
                _mappings = ref.mappings.all()
            return _concepts, _mappings

        for reference in include_refs:
            concepts, mappings = get_ref_results(reference)
            if concepts.exists():
                self.concepts.add(*self.apply_parameters(concepts, True))
                index_concepts = True
            if mappings.exists():
                self.mappings.add(*self.apply_parameters(mappings, False))
                index_mappings = True

        for reference in exclude_refs:
            concepts, mappings = get_ref_results(reference)
            if concepts.exists():
                self.concepts.remove(*concepts)
                index_concepts = True
            if mappings.exists():
                self.mappings.remove(*mappings)
                index_mappings = True

        if index:
            if index_concepts:
                self.index_concepts()
            if index_mappings:
                self.index_mappings()

    def seed_children(self, index=True):
        return self.add_references(self.collection_version.references, index, True)

    def wait_until_processed(self):  # pragma: no cover
        processing = self.is_processing
        while processing:
            print("Expansion is still processing, sleeping for 5 secs...")
            time.sleep(5)
            self.refresh_from_db()
            processing = self.is_processing
            if not processing:
                print("Expansion processed, waking up...")

    def calculate_uri(self):
        version = self.collection_version
        if version.is_head:
            return self.collection_version.uri + f'HEAD/expansions/{self.mnemonic}/'
        return self.collection_version.uri + f'expansions/{self.mnemonic}/'

    def clean(self):
        if not self.parameters:
            self.parameters = default_expansion_parameters()

        super().clean()

    @classmethod
    def persist(cls, index, **kwargs):
        sync = kwargs.pop('sync', False)
        expansion = cls(**kwargs)
        temp_version = not bool(expansion.mnemonic)
        if temp_version:
            expansion.mnemonic = generate_temp_version()
        expansion.clean()
        expansion.full_clean()
        expansion.save()
        if temp_version and expansion.id:
            expansion.mnemonic = expansion.id
            expansion.save()

        if get(settings, 'TEST_MODE', False) or sync:
            seed_children_to_expansion(expansion.id, index)
        else:
            seed_children_to_expansion.delay(expansion.id, index)

        return expansion

    def get_mappings_for_concept(self, concept, include_indirect=False):
        concept_criteria = [concept.uri, drop_version(concept.uri)]
        criteria = models.Q(from_concept__uri__in=concept_criteria)
        if include_indirect:
            criteria |= models.Q(to_concept__uri__in=concept_criteria)
        return self.mappings.filter(criteria)


class ExpansionParameters:
    ACTIVE = 'activeOnly'
    TEXT_FILTER = 'filter'
    EXCLUDE_SYSTEM = 'exclude-system'
    INCLUDE_SYSTEM = 'system-version'
    DATE = 'date'

    def __init__(self, parameters, is_concept_queryset=True):
        self.parameters = parameters
        self.parameter_classes = {}
        self.before_filters = {}
        self.is_concept_queryset = is_concept_queryset
        self.to_parameter_classes()

    def to_parameter_classes(self):
        for parameter, value in self.parameters.items():
            parameter = parameter.replace(' ', '')
            if parameter == self.ACTIVE:
                self.parameter_classes[parameter] = ExpansionActiveParameter(value=value)
            elif parameter == self.TEXT_FILTER:
                self.parameter_classes[parameter] = ExpansionTextFilterParameter(value=value)
            elif parameter == self.INCLUDE_SYSTEM:
                self.parameter_classes[parameter] = ExpansionIncludeSystemParameter(value=value)
            elif parameter == self.EXCLUDE_SYSTEM:
                self.parameter_classes[parameter] = ExpansionExcludeSystemParameter(value=value)
            elif parameter == self.DATE:
                self.parameter_classes[parameter] = ExpansionDateParameter(value=value)

    def apply(self, queryset):
        queryset = self.apply_before_filters(queryset)
        queryset = self.apply_after_filters(queryset)
        return queryset

    def apply_before_filters(self, queryset):
        self.make_before_filters()
        return queryset.filter(**self.before_filters)

    def apply_after_filters(self, queryset):
        # order of parameters matters here
        for parameter in [self.INCLUDE_SYSTEM, self.DATE, self.EXCLUDE_SYSTEM, self.TEXT_FILTER]:
            klass = get(self.parameter_classes, parameter)
            if klass and klass.after_filter:
                queryset = klass.apply(queryset, self.is_concept_queryset)
        return queryset

    def make_before_filters(self):
        for _, klass in self.parameter_classes.items():
            if klass.before_filter:
                self.before_filters = {**self.before_filters, **klass.filters}


class ExpansionParameter:
    before_filter = False   # returns db criterion, they can only be applied on DB queries
    after_filter = False   # takes queryset to run custom code or anything after

    def __init__(self, value):
        self.value = value

    def is_valid_string(self):
        return self.value and isinstance(self.value, str)


class ExpansionActiveParameter(ExpansionParameter):
    before_filter = True
    default_filters = dict(is_active=True, retired=False)

    @property
    def filters(self):
        if self.value is True:
            return self.default_filters
        return {}


class ExpansionTextFilterParameter(ExpansionParameter):
    after_filter = True

    def apply(self, queryset, is_concept_queryset):
        if self.is_valid_string():
            pks = []
            klass = get_resource_class_from_resource_name('concept' if is_concept_queryset else 'mapping')
            document = klass.get_search_document()
            search = document.search()
            search = es_wildcard_search(
                search, self.value, get_exact_search_fields(klass),
                name_attr='_name' if is_concept_queryset else 'name'
            )
            for _queryset in batch_qs(queryset.order_by('id'), 500):
                new_search = es_id_in(search, list(_queryset.values_list('id', flat=True)))
                pks += es_to_pks(new_search.params(request_timeout=ES_REQUEST_TIMEOUT))
            queryset = klass.objects.filter(id__in=set(pks)) if pks else klass.objects.none()

        return queryset


class ExpansionSystemParameter(ExpansionParameter):
    after_filter = True

    def get_criterion(self):
        criterion = models.Q()
        for system in self.value.split(','):
            canonical_url = system
            version = None
            if '|' in system:
                canonical_url, version = system.split('|')
            filters = dict(canonical_url=canonical_url)
            if version:
                filters['version'] = version
            criterion |= models.Q(**filters)

        return criterion

    def get_code_systems(self):
        from core.sources.models import Source
        return Source.objects.filter(self.get_criterion())

    def get_value_sets(self):
        return Collection.objects.filter(self.get_criterion())

    @staticmethod
    def filter_queryset(queryset, code_systems, value_sets):
        raise NotImplementedError

    def apply(self, queryset, _=None):
        if self.is_valid_string():
            queryset = self.filter_queryset(queryset, self.get_code_systems(), self.get_value_sets())
        return queryset


class ExpansionExcludeSystemParameter(ExpansionSystemParameter):
    @staticmethod
    def filter_queryset(queryset, code_systems, value_sets):
        criteria = models.Q()

        if code_systems.exists():
            criteria |= models.Q(sources__in=code_systems)
        if value_sets.exists():
            criteria |= models.Q(expansion_set__collection_version__in=value_sets)
        return queryset.exclude(criteria)


class ExpansionIncludeSystemParameter(ExpansionSystemParameter):
    @staticmethod
    def filter_queryset(queryset, code_systems, value_sets):
        return queryset.filter(
            models.Q(sources__in=code_systems) |
            models.Q(expansion_set__collection_version__in=value_sets)
        )


class ExpansionDateParameter(ExpansionIncludeSystemParameter):
    @staticmethod
    def get_date_filter(date):
        if date.count('-') >= 3 or date.count(":"):
            return dict(revision_date=date)
        parts = date.split('-')
        filters = dict(revision_date__year=parts[0])
        if len(parts) > 1:
            filters['revision_date__month'] = parts[1]
        if len(parts) > 2:
            filters['revision_date__day'] = parts[2]

        return filters

    def get_criterion(self):
        criterion = models.Q()
        for date in self.value.split(','):
            criterion |= models.Q(**self.get_date_filter(date))

        return criterion
