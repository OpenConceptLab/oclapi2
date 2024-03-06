import time

from celery_once import AlreadyQueued
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import UniqueConstraint, F, QuerySet, Max
from django.utils import timezone
from django.utils.functional import cached_property
from pydash import get, compact

from core.collections.constants import (
    COLLECTION_TYPE, REFERENCE_ALREADY_EXISTS, CONCEPT_FULLY_SPECIFIED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE,
    CONCEPT_PREFERRED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE, COLLECTION_VERSION_TYPE,
    REFERENCE_TYPE_CHOICES, CONCEPT_REFERENCE_TYPE, MAPPING_REFERENCE_TYPE, SOURCE_MAPPINGS, SOURCE_TO_CONCEPTS,
    TRANSFORM_TO_RESOURCE_VERSIONS, COLLECTION_REFERENCE_TYPE)
from core.collections.parsers import CollectionReferenceParser
from core.collections.translators import CollectionReferenceTranslator
from core.collections.utils import is_concept, is_mapping
from core.common.constants import (
    ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT,
    ES_REQUEST_TIMEOUT, ES_REQUEST_TIMEOUT_ASYNC, HEAD, ALL, EXCLUDE_WILDCARD_SEARCH_PARAM, EXCLUDE_FUZZY_SEARCH_PARAM,
    SEARCH_MAP_CODES_PARAM, INCLUDE_SEARCH_META_PARAM)
from core.common.models import ConceptContainerModel, BaseResourceModel
from core.common.search import CustomESSearch
from core.common.tasks import seed_children_to_expansion, batch_index_resources, index_expansion_concepts, \
    index_expansion_mappings, readd_references_to_expansion_on_references_removal
from core.common.utils import drop_version, to_owner_uri, generate_temp_version, es_id_in, \
    get_resource_class_from_resource_name, to_snake_case, \
    es_to_pks, batch_qs, split_list_by_condition, decode_string, is_canonical_uri, encode_string, \
    get_truthy_values, get_falsy_values
from core.concepts.constants import LOCALES_FULLY_SPECIFIED
from core.concepts.models import Concept
from core.mappings.models import Mapping

TRUTHY = get_truthy_values()
FALSEY = get_falsy_values()


class Collection(ConceptContainerModel):
    OBJECT_TYPE = COLLECTION_TYPE
    OBJECT_VERSION_TYPE = COLLECTION_VERSION_TYPE
    CHECKSUM_INCLUSIONS = ConceptContainerModel.CHECKSUM_INCLUSIONS + [
        'collection_type'
    ]

    es_fields = {
        'collection_type': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'mnemonic': {'sortable': False, 'filterable': True, 'exact': True},
        '_mnemonic': {'sortable': True, 'filterable': False, 'exact': False},
        'name': {'sortable': False, 'filterable': True, 'exact': True},
        '_name': {'sortable': True, 'filterable': False, 'exact': False},
        'last_update': {'sortable': True, 'filterable': False, 'default': 'desc'},
        'updated_by': {'sortable': False, 'filterable': False, 'facet': True},
        'locale': {'sortable': False, 'filterable': True, 'facet': True},
        'owner': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'custom_validation_schema': {'sortable': False, 'filterable': True, 'facet': True},
        'canonical_url': {'sortable': False, 'filterable': True, 'exact': True},
        'experimental': {'sortable': False, 'filterable': False, 'facet': False},
        'external_id': {'sortable': False, 'filterable': True, 'facet': False, 'exact': True},
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
        indexes = [
                      models.Index(fields=['uri']),
                      models.Index(name="coll_mnemonic_like", fields=["mnemonic"], opclasses=["text_pattern_ops"]),
                      models.Index(fields=['public_access']),
                      models.Index(
                          name='coll_org_released',
                          fields=['mnemonic', 'organization', '-created_at'],
                          condition=(models.Q(user__isnull=True, is_active=True, released=True))
                      ),
                      models.Index(
                          name='coll_user_released',
                          fields=['mnemonic', 'user', '-created_at'],
                          condition=(models.Q(organization__isnull=True, is_active=True, released=True))
                      ),
                  ] + ConceptContainerModel.Meta.indexes

    collection_type = models.TextField(blank=True)
    preferred_source = models.TextField(blank=True)
    custom_resources_linked_source = models.TextField(blank=True)
    immutable = models.BooleanField(null=True, blank=True, default=None)
    locked_date = models.DateTimeField(null=True, blank=True)
    autoexpand_head = models.BooleanField(default=True, null=True)
    autoexpand = models.BooleanField(default=True, null=True)
    expansion_uri = models.TextField(null=True, blank=True)

    def get_standard_checksum_fields(self):
        return self.get_standard_checksum_fields_for_resource(self)

    def get_smart_checksum_fields(self):
        return self.get_smart_checksum_fields_for_resource(self)

    @staticmethod
    def get_standard_checksum_fields_for_resource(data):
        return {
            'collection_type': get(data, 'collection_type'),
            'canonical_url': get(data, 'canonical_url'),
            'custom_validation_schema': get(data, 'custom_validation_schema'),
            'default_locale': get(data, 'default_locale'),
            'supported_locales': get(data, 'supported_locales'),
            'website': get(data, 'website'),
            'extras': get(data, 'extras'),
        }

    @staticmethod
    def get_smart_checksum_fields_for_resource(data):
        return {
            'collection_type': get(data, 'collection_type'),
            'canonical_url': get(data, 'canonical_url'),
            'custom_validation_schema': get(data, 'custom_validation_schema'),
            'default_locale': get(data, 'default_locale'),
            'released': get(data, 'released'),
            'retired': get(data, 'retired'),
        }

    def set_active_concepts(self):
        expansion = self.expansion
        if expansion:
            self.active_concepts = expansion.concepts.filter(retired=False, is_active=True).count()

    def set_active_mappings(self):
        expansion = self.expansion
        if expansion:
            self.active_mappings = expansion.mappings.filter(retired=False, is_active=True).count()

    @staticmethod
    def get_search_document():
        from core.collections.documents import CollectionDocument
        return CollectionDocument

    @classmethod
    def get_base_queryset(cls, params):
        collection = params.pop('collection', None)
        contains_uri = params.pop('contains', None)
        include_references = params.pop('include_references', None) in TRUTHY
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
        self.custom_resources_linked_source = head.custom_resources_linked_source
        self.immutable = head.immutable
        self.locked_date = head.locked_date

    @property
    def should_auto_expand(self):
        if self.is_head:
            return self.autoexpand_head

        return self.autoexpand

    def post_create_actions(self):
        if self.should_auto_expand:
            self.cascade_children_to_expansion(index=False)

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
            validation_error = {'names': [error_message]}
            # making sure names in the submitted concept meet the same rule
            name_key = name.locale + name.name
            if name_key in matching_names_in_concept:
                print("***NAME_KEY that dint match**", name_key)
                raise ValidationError(validation_error)

            matching_names_in_concept[name_key] = True
            other = other_concepts_in_collection.filter(
                names__name=name.name, names__locale=name.locale, **{f"names__{attribute}": value})
            if other.exists():
                print(f"***Conflicting Name {name_key} of Expansion**", list(other.values_list('uri', flat=True)))
                raise ValidationError(validation_error)

    @transaction.atomic
    def add_expressions(
            self, data, user, cascade=False, transform=False, _async=False):
        parser = CollectionReferenceParser(data, transform, cascade, user)
        parser.parse()
        parser.to_reference_structure()
        references = parser.to_objects()
        return self.add_references(references, user, _async)

    def add_references(self, references, user=None, _async=False):
        errors = {}
        added_references = []
        total_references = []
        for reference in references:
            reference.expression = reference.build_expression()
            total_references += reference.generate_references()
        total_references = CollectionReference.dedupe_by_expression(total_references)
        for reference in total_references:
            reference.collection = self
            reference.created_by = user
            reference._async = _async  # pylint: disable=protected-access
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
        if expressions == ALL:  # Deprecated: Old way
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
        if 'created_by_id' not in expansion_data:
            expansion_data['created_by_id'] = self.created_by_id
            expansion_data['updated_by_id'] = self.created_by_id
        expansion = Expansion.persist(index=index, collection_version=self, sync=sync, **expansion_data)

        if should_auto_expand and not self.expansion_uri:
            self.expansion_uri = expansion.uri
            self.save()

        return expansion

    def index_children(self):
        if self.expansion_uri:
            expansion = self.expansion
            if not expansion:
                return
            from core.concepts.documents import ConceptDocument
            from core.mappings.documents import MappingDocument

            self.batch_index(expansion.concepts, ConceptDocument)
            self.batch_index(expansion.mappings, MappingDocument)

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
        if self.expansion_uri:
            updated_at = get(
                self.expansion.concepts.aggregate(max_updated_at=Max('updated_at')), 'max_updated_at', None)
        return updated_at

    @property
    def last_mapping_update(self):
        updated_at = None
        if self.expansion_uri:
            updated_at = get(
                self.expansion.mappings.aggregate(max_updated_at=Max('updated_at')), 'max_updated_at', None)
        return updated_at

    @property
    def expansions_url(self):
        return self.uri + 'expansions/'

    def get_concepts_queryset(self):
        expansion = self.expansion
        return expansion.concepts.filter() if expansion else Concept.objects.none()

    def get_mappings_queryset(self):
        expansion = self.expansion
        return expansion.mappings.filter() if expansion else Mapping.objects.none()

    @property
    def references_distribution(self):
        return {
            'include': self.references.filter(include=True).count(),
            'exclude': self.references.filter(include=False).count(),
            'concepts': self.references.filter(reference_type=CONCEPT_REFERENCE_TYPE).count(),
            'mappings': self.references.filter(reference_type=MAPPING_REFERENCE_TYPE).count(),
            'total': self.active_references,
        }

    @property
    def referenced_sources_distribution(self):
        from core.sources.serializers import SourceVersionMinimalSerializer
        result = {}
        for reference in self.references.filter(include=True):
            version = reference.resolve_system_version
            if version:
                _result = get(result, version.uri)
                if not _result:
                    _result = {
                        **SourceVersionMinimalSerializer(version).data,
                        'distribution': {'include_reference': True, 'concepts': 0, 'mappings': 0, 'references': 0},
                    }
                _result['distribution']['concepts'] += reference.concepts.count()
                _result['distribution']['mappings'] += reference.mappings.count()
                _result['distribution']['references'] += 1
                result[version.uri] = _result

        return sorted(result.values(), key=lambda summary: get(summary, 'distribution.references'), reverse=True)

    @property
    def referenced_collections_distribution(self):
        from core.collections.serializers import CollectionVersionMinimalSerializer
        result = {}
        for reference in self.references.filter(include=True):
            versions = reference.resolve_valueset_versions
            for version in versions:
                if version:
                    _result = get(result, version.uri)
                    if not _result:
                        _result = {
                            **CollectionVersionMinimalSerializer(version).data,
                            'distribution': {'include_reference': True, 'concepts': 0, 'mappings': 0, 'references': 0},
                        }
                    _result['distribution']['concepts'] += reference.concepts.count()
                    _result['distribution']['mappings'] += reference.mappings.count()
                    _result['distribution']['references'] += 1
                    result[version.uri] = _result

        return sorted(result.values(), key=lambda summary: get(summary, 'distribution.references'), reverse=True)

    def _get_resource_facet_filters(self, filters=None):
        _filters = {
            'collection': self.mnemonic,
            'collection_owner_url': to_owner_uri(self.uri),
            'expansion': self.expansion.mnemonic if self.expansion_uri else '_NON_EXISTING_EXPANSION_',
            'retired': False,
            'collection_version': self.version
        }

        return {**_filters, **(filters or {})}


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
    # Use transform set to TRANSFORM_TO_RESOURCE_VERSIONS to indicate using the latest version instead of HEAD
    # Applies to old style expression and FHIR ValueSets
    transform = models.CharField(null=True, blank=True, max_length=255)
    include = models.BooleanField(default=True)

    resource_version = models.CharField(null=True, blank=True, max_length=255)

    # associations
    concepts = models.ManyToManyField('concepts.Concept', related_name='references', through=ReferencedConcept)
    mappings = models.ManyToManyField('mappings.Mapping', related_name='references', through=ReferencedMapping)
    collection = models.ForeignKey('collections.Collection', related_name='references', on_delete=models.CASCADE)

    @staticmethod
    def get_static_references_criteria():
        return models.Q(
            models.Q(resource_version__isnull=False) |
            models.Q(resource_version__isnull=True, transform__isnull=False) |
            models.Q(resource_version__isnull=True, transform__isnull=True, code__isnull=False, version__isnull=False)
        )

    @property
    def resource_type(self):
        return COLLECTION_REFERENCE_TYPE

    @property
    def can_compute_against_other_system_version(self):
        return (not self.system or not self.version) and not self.resource_version

    def can_compute_against_system_version(self, system_version):
        result = False
        if self.can_compute_against_other_system_version and system_version:
            self_system_version = self.resolve_system_version
            if self_system_version:
                result = drop_version(self_system_version.uri) == drop_version(system_version.uri)
        return result

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
            is_canonical = is_canonical_uri(self.system)
            expression = self.system
            if self.version:
                expression += '|' + self.version if is_canonical else self.version
        elif self.valueset and isinstance(self.valueset, list):
            expression = self.valueset[0]  # pylint: disable=unsubscriptable-object

        is_canonical = is_canonical_uri(expression)
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
                    querystring = self.filter_to_querystring()
                    if querystring:
                        expression += '?' + querystring
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
        uri = None
        if collection:
            uri = f'{collection.uri}references/{self.id}/'
        return uri

    def fetch_concepts(self, refetch=False):
        if not get(self, '_fetched') or refetch:
            self._concepts, self._mappings = self.get_concepts()
            self._fetched = True

    def fetch_mappings(self, refetch=False):
        if not get(self, '_fetched') or refetch:
            self._mappings = self.get_mappings()
            self._fetched = True

    def get_concepts(self, system_version=None):
        system_version = system_version or self.resolve_system_version
        queryset = self.get_resource_queryset_from_system_and_valueset('concepts', system_version)
        valueset_versions = self.resolve_valueset_versions
        mapping_queryset = Mapping.objects.none()
        if queryset is None:
            return Concept.objects.none(), mapping_queryset

        if self.code:
            queryset = queryset.filter(mnemonic=decode_string(self.code))
            if self.resource_version:
                queryset = queryset.filter(version=self.resource_version)
        if self.cascade:
            cascade_params = self.get_concept_cascade_params(system_version or valueset_versions[0])
            for concept in queryset:
                result = concept.cascade(**cascade_params)
                queryset = Concept.objects.filter(
                    id__in=list(queryset.union(result['concepts']).values_list('id', flat=True)))
                mapping_queryset = Mapping.objects.filter(
                    id__in=list(mapping_queryset.union(result['mappings']).values_list('id', flat=True)))

        if self.should_apply_filter():
            queryset = self.apply_filters(queryset, Concept)
        if self.should_transform_to_latest_version():
            queryset = self.transform_to_latest_version(queryset, Concept)
            if self.code:
                concept = queryset.first()
                if concept:
                    self.resource_version = concept.version
                    self.expression = concept.uri
            if mapping_queryset.exists():
                mapping_queryset = self.transform_to_latest_version(mapping_queryset, Mapping)
        return queryset, mapping_queryset

    def get_mappings(self, system_version=None):
        queryset = self.get_resource_queryset_from_system_and_valueset('mappings', system_version)
        if queryset is None:
            return Mapping.objects.none()

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
    def dedupe_by_expression(references):
        return list({reference.expression: reference for reference in references}.values())

    def generate_references(self):
        references = []
        if self.should_generate_multiple_references():
            concepts, mappings = self.get_concepts()
            for concept in concepts:
                references.append(CollectionReference(
                    expression=concept.uri,
                    reference_type='concepts',
                    code=encode_string(concept.mnemonic),
                    collection_id=self.collection_id,
                    created_by=self.created_by,
                    resource_version=concept.version,
                    system=self.system,
                    version=self.version
                ))
            for mapping in mappings:
                references.append(CollectionReference(
                    expression=mapping.uri,
                    reference_type='mappings',
                    code=mapping.mnemonic,
                    collection_id=self.collection_id,
                    created_by=self.created_by,
                    resource_version=mapping.version,
                    system=self.system,
                    version=self.version
                ))
        else:
            references.append(self)
        return references

    def should_generate_multiple_references(self):
        return self.include and self.cascade and self.transform

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

    def get_concept_cascade_params(self, system_version=None):
        is_dict = isinstance(self.cascade, dict)
        method = get(self.cascade, 'method') if is_dict and 'method' in self.cascade else self.cascade or ''
        cascade_params = {
            'repo_version': get(self.cascade, 'source_version') or system_version or self.version or HEAD,
            'source_mappings': method.lower() == SOURCE_MAPPINGS,
            'source_to_concepts': method.lower() == SOURCE_TO_CONCEPTS,
            'cascade_levels': 1 if self.cascade == method else get(self.cascade, 'cascade_levels', ALL),
        }
        if is_dict:
            for attr in ['cascade_mappings', 'cascade_hierarchy', 'reverse', 'max_results', 'include_retired',
                         'reverse', 'omit_if_exists_in']:
                if attr in self.cascade:
                    cascade_params[attr] = get(self.cascade, attr)
            map_types = get(self.cascade, 'map_types', None)
            exclude_map_types = get(self.cascade, 'exclude_map_types', None)
            return_map_types = get(self.cascade, 'return_map_types', None)
            cascade_params['map_types'] = map_types
            cascade_params['exclude_map_types'] = exclude_map_types
            cascade_params['return_map_types'] = return_map_types
        if get(self, '_async'):
            cascade_params['max_results'] = None
        return cascade_params

    def has_param_in_filter(self, param, expected_values):
        return bool(next(
            (filter_def for filter_def in self.filter if  # pylint:disable=not-an-iterable
             filter_def['property'].lower() == param and filter_def['value'] in expected_values),
            False
        ))

    def _apply_search(self, search, val, document):
        include_wildcard = self.has_param_in_filter(EXCLUDE_WILDCARD_SEARCH_PARAM.lower(), FALSEY)
        include_fuzzy = self.has_param_in_filter(EXCLUDE_FUZZY_SEARCH_PARAM.lower(), FALSEY)
        exclude_search_map_codes = self.has_param_in_filter(SEARCH_MAP_CODES_PARAM.lower(), FALSEY)

        def clean_fields(_fields):
            if exclude_search_map_codes:
                return {key: value for key, value in _fields.items() if not key.endswith('map_codes')}
            return _fields

        search = search.query(CustomESSearch.get_exact_match_criterion(
            val, document.get_match_phrase_attrs(), clean_fields(document.get_exact_match_attrs())
        ))
        if include_wildcard or include_fuzzy:
            if include_wildcard:
                search = search.query(
                    CustomESSearch.get_wildcard_match_criterion(val, clean_fields(document.get_wildcard_search_attrs()))
                )
            if include_fuzzy:
                search = search.query(
                    CustomESSearch.get_fuzzy_match_criterion(val, document.get_fuzzy_search_attrs(), 10000, 2)
                )
        return search

    def apply_filters(self, queryset, resource_klass):
        if self.filter:
            pks = []
            document = resource_klass.get_search_document()
            search = document.search()
            for filter_def in self.filter:  # pylint: disable=not-an-iterable
                if to_snake_case(filter_def['property']) == 'exact_match' or filter_def['property'] in [
                    EXCLUDE_WILDCARD_SEARCH_PARAM, EXCLUDE_FUZZY_SEARCH_PARAM, SEARCH_MAP_CODES_PARAM,
                    INCLUDE_SEARCH_META_PARAM
                ]:
                    continue
                val = filter_def['value']
                if filter_def['property'] == 'q':
                    self._apply_search(search, val, document)
                else:
                    search = search.filter("match", **{to_snake_case(filter_def["property"]): filter_def["value"]})
            for _queryset in batch_qs(queryset.order_by('id'), 500):
                # iterating on queryset because ES has max_clause limit default to 1024
                search_within_queryset = es_id_in(search, list(_queryset.values_list('id', flat=True)))
                pks += es_to_pks(search_within_queryset.params(request_timeout=ES_REQUEST_TIMEOUT_ASYNC))
            if pks:
                resource_versions = resource_klass.objects.filter(id__in=set(pks))
                if self.version or self.valueset or self.transform:
                    return resource_versions
                return resource_klass.objects.filter(
                    id__in=resource_versions.values_list('versioned_object_id', flat=True))
            return resource_klass.objects.none()

        return queryset

    # returns intersection of system and valueset resources considering creator permissions
    def get_resource_queryset_from_system_and_valueset(self, resource_relation, system_version=None):
        system_version = system_version or self.resolve_system_version
        valueset_versions = self.resolve_valueset_versions
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

        return queryset

    @cached_property
    def resolve_system_version(self):
        if self.system:
            from core.sources.models import Source
            version = Source.resolve_reference_expression(self.system, self.namespace, self.version)
            if version.id:
                return version
        return None

    @cached_property
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
            raise ValidationError({'filter': ['Invalid filter schema.']})

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
        if not self.is_valid_filter():
            return None
        filters = compact(self.filter)
        if filters:
            queries = []
            for filter_def in filters:  # pylint: disable=not-an-iterable
                value = ','.join(filter_def['value']) if isinstance(filter_def['value'], list) else filter_def['value']
                queries.append(f'{filter_def["property"]}={value}')
            return '&'.join(queries)
        return None

    def is_valid_filter(self):
        if not self.filter:
            return True

        if not isinstance(self.filter, list):
            return False
        if len(self.filter) != len(compact(self.filter)):
            return False

        return all(map(self.__is_valid_filter_schema, self.filter))

    def get_allowed_filter_properties(self):
        common = ['q', 'exact_match', 'exclude_wildcard', 'exclude_fuzzy', 'search_map_codes', 'include_search_meta']
        if self.is_concept:
            common = [*Concept.es_fields.keys(), *common]
        elif self.is_mapping:
            common = [*Mapping.es_fields.keys(), *common]
        return common

    def __is_valid_filter_schema(self, filter_def):
        return isinstance(filter_def, dict) and \
               sorted(filter_def.keys()) == sorted(['property', 'op', 'value']) and \
               {type(val) for val in filter_def.values()} == {str} and \
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

    @property
    def translation(self):
        return CollectionReferenceTranslator(self).translate()


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
        indexes = [
                      models.Index(fields=['uri']),
                      models.Index(name="expansion_mnemonic_like", fields=["mnemonic"], opclasses=["text_pattern_ops"])
                  ] + BaseResourceModel.Meta.indexes

    parameters = models.JSONField(default=default_expansion_parameters)
    canonical_url = models.URLField(null=True, blank=True)
    text = models.TextField(null=True, blank=True)
    concepts = models.ManyToManyField('concepts.Concept', blank=True, related_name='expansion_set')
    mappings = models.ManyToManyField('mappings.Mapping', blank=True, related_name='expansion_set')
    collection_version = models.ForeignKey(
        'collections.Collection', related_name='expansions', on_delete=models.CASCADE)
    is_processing = models.BooleanField(default=False)
    resolved_collection_versions = models.ManyToManyField(
        'collections.Collection', related_name='expansions_resolved_collection_versions_set')
    resolved_source_versions = models.ManyToManyField(
        'sources.Source', related_name='expansions_resolved_source_versions_set')

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
        return Expansion.get_url_kwarg()

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
                try:
                    index_expansion_concepts.apply_async((self.id, ), queue='indexing')
                except AlreadyQueued:
                    pass

    def index_mappings(self):
        if self.mappings.exists():
            if get(settings, 'TEST_MODE', False):
                index_expansion_mappings(self.id)
            else:
                try:
                    index_expansion_mappings.apply_async((self.id, ), queue='indexing')
                except AlreadyQueued:
                    pass

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
        any_ref_with_resources = False
        for reference in refs:
            concepts = reference.concepts
            if concepts.exists():
                any_ref_with_resources = True
                index_concepts = True
                filters = {'versioned_object_id__in': list(concepts.values_list('versioned_object_id', flat=True))}
                self.concepts.set(self.concepts.exclude(**filters))
                batch_index_resources.apply_async(('concept', filters), queue='indexing')
            mappings = reference.mappings
            if mappings.exists():
                any_ref_with_resources = True
                index_mappings = True
                filters = {'versioned_object_id__in': list(mappings.values_list('versioned_object_id', flat=True))}
                self.mappings.set(self.mappings.exclude(**filters))
                batch_index_resources.apply_async(('mapping', filters), queue='indexing')

        if index_concepts:
            self.index_concepts()
        if index_mappings:
            self.index_mappings()

        if any_ref_with_resources:
            removed_reference_ids = [ref.id for ref in self.to_ref_list(references)]
            readd_task = readd_references_to_expansion_on_references_removal
            if get(settings, 'TEST_MODE', False):
                readd_task(self.id, removed_reference_ids)
            else:
                readd_task.apply_async((self.id, removed_reference_ids), queue='default')

    def delete_expressions(self, expressions):  # Deprecated: Old way, must use delete_references instead
        concepts_filters = None
        mappings_filters = None
        if expressions == ALL:
            if self.concepts.exists():
                concepts_filters = {'id__in': list(self.concepts.values_list('id', flat=True))}
                self.concepts.clear()
            if self.mappings.exists():
                mappings_filters = {'id__in': list(self.mappings.values_list('id', flat=True))}
                self.mappings.clear()
        else:
            concepts_filters = mappings_filters = {'uri__in': expressions}
            self.concepts.set(self.concepts.exclude(**concepts_filters))
            self.mappings.set(self.mappings.exclude(**mappings_filters))

        if not get(settings, 'TEST_MODE', False):
            batch_index_resources.apply_async(('concept', concepts_filters), queue='indexing')
            batch_index_resources.apply_async(('mapping', mappings_filters), queue='indexing')

    def add_references(self, references, index=True, is_adding_all=False, attempt_reevaluate=True):  # pylint: disable=too-many-locals,too-many-statements
        include_refs, exclude_refs = self.to_ref_list_separated(references)
        resolved_valueset_versions = []
        resolved_system_versions = []
        _system_version_cache = {}

        if not is_adding_all:
            existing_exclude_refs = self.collection_version.references.exclude(
                include=True).exclude(id__in=[ref.id for ref in exclude_refs])
            if isinstance(exclude_refs, QuerySet):
                exclude_refs |= existing_exclude_refs
            else:
                exclude_refs += [*existing_exclude_refs.all()]

        index_concepts = index_mappings = False

        # attempt_reevaluate is False for delete reference(s)
        should_reevaluate = attempt_reevaluate and not self.is_auto_generated

        include_system_versions = []
        system_versions = self.parameters.get(ExpansionParameters.INCLUDE_SYSTEM)
        if should_reevaluate and system_versions:
            for system_version in compact(system_versions.split(',')):
                _system = system_version.strip()
                _cache_key = f"NONE-{_system}-NONE"
                version = ConceptContainerModel.resolve_reference_expression(_system)
                _system_version_cache[_cache_key] = version
                if version.id:
                    include_system_versions.append(version)

        def get_ref_system_version(ref):
            if ref.system:
                __cache_key = f"{ref.namespace or 'NONE'}-{ref.system}-{ref.version or 'NONE'}"
                if __cache_key not in _system_version_cache:
                    _system_version_cache[__cache_key] = ref.resolve_system_version
                return _system_version_cache[__cache_key]
            return None

        def get_ref_results(ref):
            nonlocal resolved_valueset_versions
            nonlocal resolved_system_versions
            resolved_valueset_versions += ref.resolve_valueset_versions
            ref_system_versions = []
            should_use_ref_system_version = True
            for _system_version in include_system_versions:
                if ref.can_compute_against_system_version(_system_version):
                    ref_system_versions.append(_system_version)
                    should_use_ref_system_version = False

            if should_use_ref_system_version:
                _system_version = get_ref_system_version(ref)
                if _system_version:
                    ref_system_versions.append(_system_version)
            if ref_system_versions:
                ref_system_versions = list(set(ref_system_versions))

            _concepts = Concept.objects.none()
            _mappings = Mapping.objects.none()

            if should_reevaluate:
                for _system_version in ref_system_versions:
                    if ref.is_mapping:
                        _mappings = _mappings.union(ref.get_mappings(_system_version))
                    else:
                        __concepts, __mappings = ref.get_concepts(_system_version)
                        _concepts = Concept.objects.filter(
                            id__in=_concepts.union(__concepts).values_list('id', flat=True)
                        )
                        _mappings = _mappings.union(__mappings)
                    _mappings = Mapping.objects.filter(id__in=_mappings.values_list('id', flat=True))
            else:
                _concepts = ref.concepts.filter()
                _mappings = ref.mappings.filter()
            resolved_system_versions += ref_system_versions
            return _concepts, _mappings

        for reference in include_refs:
            concepts, mappings = get_ref_results(reference)
            index_concepts = self.__include_resources(self.concepts, concepts, True)
            index_mappings = self.__include_resources(self.mappings, mappings, False)

        for reference in exclude_refs:
            concepts, mappings = get_ref_results(reference)
            index_concepts = self.__exclude_resources(reference, self.concepts, concepts)
            index_mappings = self.__exclude_resources(reference, self.mappings, mappings)

        self.resolved_collection_versions.add(*compact(resolved_valueset_versions))
        self.resolved_source_versions.add(*compact(resolved_system_versions))
        self.dedupe_resources()
        if index:
            self.index_resources(index_concepts, index_mappings)

    def dedupe_resources(self):
        self.__dedupe_concepts()
        self.__dedupe_mappings()

    def __dedupe_concepts(self):
        self.concepts.set(self.concepts.distinct('versioned_object_id'))

    def __dedupe_mappings(self):
        self.mappings.set(self.mappings.distinct('versioned_object_id'))

    def __include_resources(self, rel, resources, is_concept_queryset):
        should_index = resources.exists()
        if should_index:
            rel.add(*self.apply_parameters(resources, is_concept_queryset))
        return should_index

    @staticmethod
    def __exclude_resources(ref, rel, resources):
        should_index = resources.exists()
        if should_index:
            if ref.resource_version:
                rel.remove(*resources)
            else:
                rel.set(rel.exclude(versioned_object_id__in=resources.values_list('versioned_object_id', flat=True)))
        return should_index

    def index_resources(self, concepts, mappings):
        if concepts:
            self.index_concepts()
        if mappings:
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

    @staticmethod
    def get_should_link_repo_versions_criteria():
        return models.Q(
            resolved_source_versions__isnull=True, resolved_collection_versions__isnull=True,
        ) & models.Q(models.Q(concepts__isnull=False) | models.Q(mappings__isnull=False))

    def link_repo_versions(self):
        from core.sources.models import Source
        concept_source_ids = set(self.concepts.values_list('parent_id', flat=True))
        mapping_source_ids = set(self.mappings.values_list('parent_id', flat=True))
        sources = Source.objects.filter(id__in=[*concept_source_ids, *mapping_source_ids])
        for source in sources:
            version = source.get_latest_released_version() or source.get_latest_version() or source
            if version:
                self.resolved_source_versions.add(version)


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
        for parameter in [self.DATE, self.EXCLUDE_SYSTEM, self.TEXT_FILTER]:
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
    default_filters = {'is_active': True, 'retired': False}

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
            search = search.query(
                CustomESSearch.get_exact_match_criterion(
                    self.value, document.get_match_phrase_attrs(), document.get_exact_match_attrs())
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
            filters = {'canonical_url': canonical_url}
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
            return {'revision_date': date}
        parts = date.split('-')
        filters = {'revision_date__year': parts[0]}
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
