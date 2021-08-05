import json

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import UniqueConstraint
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from core.collections.constants import (
    COLLECTION_TYPE, CONCEPTS_EXPRESSIONS,
    MAPPINGS_EXPRESSIONS,
    REFERENCE_ALREADY_EXISTS, CONCEPT_FULLY_SPECIFIED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE,
    CONCEPT_PREFERRED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE, ALL_SYMBOL, COLLECTION_VERSION_TYPE)
from core.collections.utils import is_concept, is_mapping
from core.common.constants import (
    DEFAULT_REPOSITORY_TYPE, ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT
)
from core.common.models import ConceptContainerModel
from core.common.utils import is_valid_uri, drop_version
from core.concepts.constants import FULLY_SPECIFIED
from core.concepts.models import Concept
from core.concepts.views import ConceptListView
from core.mappings.models import Mapping
from core.mappings.views import MappingListView


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
    concepts = models.ManyToManyField('concepts.Concept', blank=True, related_name='collection_set')
    mappings = models.ManyToManyField('mappings.Mapping', blank=True, related_name='collection_set')
    references = models.ManyToManyField('collections.CollectionReference', blank=True, related_name='collections')
    immutable = models.BooleanField(null=True, blank=True, default=None)
    locked_date = models.DateTimeField(null=True, blank=True)

    @classmethod
    def get_base_queryset(cls, params):
        collection = params.pop('collection', None)
        contains_uri = params.pop('contains', None)
        include_references = params.pop('include_references', None) in [True, 'true']
        queryset = super().get_base_queryset(params)
        if collection:
            queryset = queryset.filter(cls.get_iexact_or_criteria('mnemonic', collection))
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

    def update_version_data(self, obj=None):
        super().update_version_data(obj)

        if not obj:
            obj = self.get_latest_version()

        if obj:
            self.collection_type = obj.collection_type
            self.custom_resources_linked_source = obj.custom_resources_linked_source
            self.repository_type = obj.repository_type
            self.preferred_source = obj.preferred_source

    def add_concept(self, concept):
        self.concepts.add(concept)

    def add_mapping(self, mapping):
        self.mappings.add(mapping)

    def get_concepts(self, start=None, end=None):
        """ Use for efficient iteration over paginated concepts. Note that any filter will be applied only to concepts
        from the given range. If you need to filter on all concepts, use get_concepts() without args.
        """
        concepts = self.concepts.all()
        if start and end:
            concepts = concepts[start:end]

        return concepts

    def fill_data_from_reference(self, reference):
        self.references.add(reference)
        if reference.concepts:
            self.concepts.add(*reference.concepts)
        if reference.mappings:
            self.mappings.add(*reference.mappings)
        self.save()  # update counts

    def validate(self, reference):
        reference.full_clean()
        if reference.without_version in [reference.without_version for reference in self.references.all()]:
            raise ValidationError({reference.expression: [REFERENCE_ALREADY_EXISTS]})

        if self.is_openmrs_schema:
            if reference.concepts and reference.concepts.count() == 0:
                return

            concept = reference.concepts[0]
            self.check_concept_uniqueness_in_collection_and_locale_by_name_attribute(
                concept, attribute='type', value=FULLY_SPECIFIED,
                error_message=CONCEPT_FULLY_SPECIFIED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE
            )
            self.check_concept_uniqueness_in_collection_and_locale_by_name_attribute(
                concept, attribute='locale_preferred', value=True,
                error_message=CONCEPT_PREFERRED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE
            )

    def check_concept_uniqueness_in_collection_and_locale_by_name_attribute(
            self, concept, attribute, value, error_message
    ):
        other_concepts_in_collection = self.concepts
        if not other_concepts_in_collection.exists():
            return

        matching_names_in_concept = dict()
        names = concept.names.filter(**{attribute: value})

        for name in names:
            validation_error = dict(names=[error_message])
            # making sure names in the submitted concept meet the same rule
            name_key = name.locale + name.name
            if name_key in matching_names_in_concept:
                raise ValidationError(validation_error)

            matching_names_in_concept[name_key] = True
            if other_concepts_in_collection.filter(names__name=name.name, names__locale=name.locale).exists():
                raise ValidationError(validation_error)

    @staticmethod
    def __get_children_uris(url, view_klass):
        view = view_klass.as_view()
        request = APIRequestFactory().get(url)
        response = view(request)
        response.render()
        data = json.loads(response.content)
        return [child['url'] for child in data]

    def __get_expressions_from(self, expressions, url, view_klass):
        if expressions == ALL_SYMBOL:
            return self.__get_children_uris(url, view_klass)

        return expressions

    @staticmethod
    def __get_children_list_url(child_type, host_url, data):
        return "{host_url}{uri}{child_type}?q={search_term}&limit=0".format(
            host_url=host_url, uri=data.get('uri'), child_type=child_type, search_term=data.get('search_term', '')
        )

    @transaction.atomic
    def add_expressions(self, data, host_url, user, cascade_mappings=False):
        expressions = data.get('expressions', [])
        concept_expressions = data.get('concepts', [])
        mapping_expressions = data.get('mappings', [])

        expressions.extend(
            self.__get_expressions_from(
                concept_expressions, self.__get_children_list_url('concepts', host_url, data), ConceptListView
            )
        )
        expressions.extend(
            self.__get_expressions_from(
                mapping_expressions, self.__get_children_list_url('mappings', host_url, data), MappingListView
            )
        )
        if cascade_mappings:
            all_related_mappings = self.get_all_related_mappings(expressions)
            expressions += all_related_mappings

        return self.add_references_in_bulk(expressions, user)

    def add_references_in_bulk(self, expressions, user=None):  # pylint: disable=too-many-locals,too-many-branches  # Fixme: Sny
        errors = {}
        collection_version = self.head

        new_expressions = set(expressions)
        new_versionless_expressions = {drop_version(expression): expression for expression in new_expressions}
        for reference in collection_version.references.all():
            existing_versionless_expression = reference.without_version
            if existing_versionless_expression in new_versionless_expressions:
                existing_expression = new_versionless_expressions[existing_versionless_expression]
                new_expressions.discard(existing_expression)
                errors[existing_expression] = [REFERENCE_ALREADY_EXISTS]

        added_references = list()
        for expression in new_expressions:
            ref = CollectionReference(expression=expression)
            try:
                ref.clean()
                ref.save()
            except Exception as ex:
                errors[expression] = ex.messages if hasattr(ex, 'messages') else ex
                continue

            added = False
            if ref.concepts:
                if self.is_openmrs_schema:
                    for concept in ref.concepts:
                        try:
                            self.check_concept_uniqueness_in_collection_and_locale_by_name_attribute(
                                concept, attribute='type', value=FULLY_SPECIFIED,
                                error_message=CONCEPT_FULLY_SPECIFIED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE
                            )
                            self.check_concept_uniqueness_in_collection_and_locale_by_name_attribute(
                                concept, attribute='locale_preferred', value=True,
                                error_message=CONCEPT_PREFERRED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE
                            )
                        except Exception as ex:
                            errors[expression] = ex.messages if hasattr(ex, 'messages') else ex
                            continue
                        collection_version.add_concept(concept)
                        added = True
                else:
                    collection_version.concepts.add(*ref.concepts.all())
                    added = True
            if ref.mappings:
                collection_version.mappings.add(*ref.mappings.all())
                added = True
            if not added and ref.id:
                added = True

            if added:
                collection_version.references.add(ref)
                self.references.add(ref)
                added_references.append(ref)

        if user and user.is_authenticated:
            collection_version.updated_by = user
            self.updated_by = user
        collection_version.save()
        self.save()
        return added_references, errors

    def add_references(self, expressions, user=None):
        errors = {}

        for expression in expressions:
            reference = CollectionReference(expression=expression)
            try:
                self.validate(reference)
                reference.save()
            except Exception as ex:
                errors[expression] = ex.messages if hasattr(ex, 'messages') else ex
                continue

            head = self.head
            ref_hash = {'col_reference': reference}

            error = Collection.persist_changes(head, user, None, **ref_hash)
            if error:
                errors[expression] = error

        return errors

    @classmethod
    def persist_changes(cls, obj, updated_by, original_schema, **kwargs):
        col_reference = kwargs.pop('col_reference', False)
        errors = super().persist_changes(obj, updated_by, original_schema, **kwargs)
        if col_reference and not errors:
            obj.fill_data_from_reference(col_reference)
        return errors

    def seed_references(self):
        head = self.head
        if head:
            references = CollectionReference.objects.bulk_create(
                [CollectionReference(expression=ref.expression) for ref in head.references.all()]
            )
            self.references.set(references)

    @staticmethod
    def is_validation_necessary():
        return False

    def delete_references(self, expressions):
        head = self.head
        head.concepts.set(head.concepts.exclude(uri__in=expressions))
        head.mappings.set(head.mappings.exclude(uri__in=expressions))
        head.references.set(head.references.exclude(expression__in=expressions))

        from core.concepts.documents import ConceptDocument
        from core.mappings.documents import MappingDocument
        self.batch_index(Concept.objects.filter(uri__in=expressions), ConceptDocument)
        self.batch_index(Mapping.objects.filter(uri__in=expressions), MappingDocument)

    @staticmethod
    def __get_children_from_expressions(expressions):
        concepts = Concept.objects.filter(uri__in=expressions)
        mappings = Mapping.objects.filter(uri__in=expressions)
        return concepts, mappings

    def get_all_related_mappings(self, expressions):
        all_related_mappings = []
        unversioned_mappings = []
        concept_expressions = []

        for expression in expressions:
            if is_mapping(expression):
                unversioned_mappings.append(drop_version(expression))
            elif is_concept(expression):
                concept_expressions.append(expression)

        for concept_expression in concept_expressions:
            ref = CollectionReference(expression=concept_expression)
            try:
                self.validate(ref)
                all_related_mappings += ref.get_related_mappings(unversioned_mappings)
            except:  # pylint: disable=bare-except
                continue

        return all_related_mappings

    def get_cascaded_mapping_uris_from_concept_expressions(self, expressions):
        mapping_uris = []

        for expression in expressions:
            if is_concept(expression):
                mapping_uris += list(
                    self.mappings.filter(
                        from_concept__uri__icontains=drop_version(expression)).values_list('uri', flat=True)
                )

        return mapping_uris


class CollectionReference(models.Model):
    class Meta:
        db_table = 'collection_references'

    concepts = None
    mappings = None
    original_expression = None

    id = models.BigAutoField(primary_key=True)
    internal_reference_id = models.CharField(max_length=255, null=True, blank=True)
    expression = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_resolved_at = models.DateTimeField(default=timezone.now, null=True)

    @staticmethod
    def get_concept_heads_from_expression(expression):
        return Concept.get_latest_versions_for_queryset(Concept.from_uri_queryset(expression))

    @staticmethod
    def diff(ctx, _from):
        prev_expressions = map(lambda r: r.expression, _from)
        return filter(lambda ref: ref.expression not in prev_expressions, ctx)

    @property
    def without_version(self):
        return drop_version(self.expression)

    @property
    def is_valid_expression(self):
        return is_valid_uri(self.expression) and self.expression.count('/') >= 7

    @property
    def reference_type(self):
        reference = None
        if is_concept(self.expression):
            reference = CONCEPTS_EXPRESSIONS
        if is_mapping(self.expression):
            reference = MAPPINGS_EXPRESSIONS

        return reference

    def get_concepts(self):
        return Concept.from_uri_queryset(self.expression)

    def get_mappings(self):
        return Mapping.from_uri_queryset(self.expression)

    def clean(self):
        self.original_expression = str(self.expression)

        self.create_entities_from_expressions()
        is_resolved = bool((self.mappings and self.mappings.count()) or (self.concepts and self.concepts.count()))
        if not is_resolved:
            self.last_resolved_at = None

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        if not self.internal_reference_id and self.id:
            self.internal_reference_id = str(self.id)
        super().save(force_insert, force_update, using, update_fields)

    def create_entities_from_expressions(self):
        __is_concept = is_concept(self.expression)
        __is_mapping = is_mapping(self.expression)
        if __is_concept:
            self.concepts = self.get_concepts()
        elif __is_mapping:
            self.mappings = self.get_mappings()

        if self.concepts and self.concepts.exists():
            self.expression = self.concepts.first().uri
        elif self.mappings and self.mappings.exists():
            self.expression = self.mappings.first().uri

    def get_related_mappings(self, exclude_mapping_uris):
        mappings = []
        concepts = self.get_concepts()
        if concepts.exists():
            for concept in concepts:
                mappings = list(
                    concept.get_unidirectional_mappings().exclude(
                        uri__in=exclude_mapping_uris
                    ).values_list('uri', flat=True)
                )

        return mappings
