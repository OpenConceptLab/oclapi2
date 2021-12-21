from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import UniqueConstraint
from django.utils import timezone

from core.collections.constants import (
    COLLECTION_TYPE, CONCEPTS_EXPRESSIONS,
    MAPPINGS_EXPRESSIONS,
    REFERENCE_ALREADY_EXISTS, CONCEPT_FULLY_SPECIFIED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE,
    CONCEPT_PREFERRED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE, ALL_SYMBOL, COLLECTION_VERSION_TYPE)
from core.collections.utils import is_concept, is_mapping
from core.common.constants import (
    DEFAULT_REPOSITORY_TYPE, ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT,
    ACCESS_TYPE_NONE)
from core.common.models import ConceptContainerModel
from core.common.utils import is_valid_uri, drop_version
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
    concepts = models.ManyToManyField('concepts.Concept', blank=True, related_name='collection_set')
    mappings = models.ManyToManyField('mappings.Mapping', blank=True, related_name='collection_set')
    references = models.ManyToManyField('collections.CollectionReference', blank=True, related_name='collections')
    immutable = models.BooleanField(null=True, blank=True, default=None)
    locked_date = models.DateTimeField(null=True, blank=True)

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
        other_concepts_in_collection = self.concepts
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
                    names__name=name.name, names__locale=name.locale, **{"names__{}".format(attribute): value}
            ).exists():
                raise ValidationError(validation_error)

    @staticmethod
    def get_source_from_uri(uri):
        from core.sources.models import Source
        return Source.objects.filter(uri=uri).first()

    @transaction.atomic
    def add_expressions(self, data, user, cascade_mappings=False, cascade_to_concepts=False):
        expressions = data.get('expressions', [])
        concept_expressions = data.get('concepts', [])
        mapping_expressions = data.get('mappings', [])
        source = None
        source_uri = data.get('uri')
        if source_uri:
            source = self.get_source_from_uri(source_uri)

        if source:
            can_view_all_content = source.can_view_all_content(user)

            def get_child_expressions(queryset):
                if source.is_head:
                    queryset = queryset.filter(is_latest_version=True)
                if not can_view_all_content:
                    queryset = queryset.filter(public_access=ACCESS_TYPE_NONE)
                return list(queryset.values_list('uri', flat=True))

            if concept_expressions == ALL_SYMBOL:
                expressions.extend(get_child_expressions(source.concepts))
            if mapping_expressions == ALL_SYMBOL:
                expressions.extend(get_child_expressions(source.mappings))
        else:
            expressions.extend(concept_expressions)
            expressions.extend(mapping_expressions)

        if cascade_mappings or cascade_to_concepts:
            expressions += self.get_all_related_uris(expressions, cascade_to_concepts)

        return self.add_references(expressions, user)

    def add_references(self, expressions, user=None):  # pylint: disable=too-many-locals,too-many-branches  # Fixme: Sny
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

        added_references = []
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
                                concept, attribute='type__in', value=LOCALES_FULLY_SPECIFIED,
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
        collection_version.update_children_counts()
        if collection_version.id != self.id:
            self.save()
        return added_references, errors

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
        head.update_children_counts()
        self.batch_index(Concept.objects.filter(uri__in=expressions), ConceptDocument)
        self.batch_index(Mapping.objects.filter(uri__in=expressions), MappingDocument)

    @staticmethod
    def __get_children_from_expressions(expressions):
        concepts = Concept.objects.filter(uri__in=expressions)
        mappings = Mapping.objects.filter(uri__in=expressions)
        return concepts, mappings

    def get_all_related_uris(self, expressions, cascade_to_concepts=False):
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
                all_related_mappings += ref.get_related_uris(unversioned_mappings, cascade_to_concepts)
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

    def get_related_uris(self, exclude_mapping_uris, cascade_to_concepts=False):
        uris = []
        concepts = self.get_concepts()
        if concepts.exists():
            for concept in concepts:
                mapping_queryset = concept.get_unidirectional_mappings().exclude(uri__in=exclude_mapping_uris)
                uris = list(mapping_queryset.values_list('uri', flat=True))
                if cascade_to_concepts:
                    to_concepts_queryset = mapping_queryset.filter(to_concept__parent_id=concept.parent_id)
                    uris += list(to_concepts_queryset.values_list('to_concept__uri', flat=True))

        return uris
