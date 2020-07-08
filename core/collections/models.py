from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import UniqueConstraint
from django.urls import resolve
from pydash import compact, get

from core.collections.constants import (
    COLLECTION_TYPE, EXPRESSION_INVALID, EXPRESSION_RESOURCE_URI_PARTS_COUNT,
    EXPRESSION_RESOURCE_VERSION_URI_PARTS_COUNT, CONCEPTS_EXPRESSIONS,
    MAPPINGS_EXPRESSIONS,
    REFERENCE_ALREADY_EXISTS, CONCEPT_FULLY_SPECIFIED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE,
    CONCEPT_PREFERRED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE, EXPRESSION_NUMBER_OF_PARTS_WITH_VERSION
)
from core.common.constants import DEFAULT_REPOSITORY_TYPE, CUSTOM_VALIDATION_SCHEMA_OPENMRS
from core.common.models import ConceptContainerModel
from core.common.utils import reverse_resource
from core.concepts.models import Concept
from core.mappings.models import Mapping


class Collection(ConceptContainerModel):
    OBJECT_TYPE = COLLECTION_TYPE

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

    collection_type = models.TextField(blank=True)
    preferred_source = models.TextField(blank=True)
    repository_type = models.TextField(default=DEFAULT_REPOSITORY_TYPE, blank=True)
    custom_resources_linked_source = models.TextField(blank=True)
    concepts = models.ManyToManyField('concepts.Concept')

    @property
    def collection(self):
        return self.mnemonic

    @staticmethod
    def get_resource_url_kwarg():
        return 'collection'

    @property
    def versions_url(self):
        return reverse_resource(self, 'collection-version-list')

    def update_version_data(self, obj=None):
        super().update_version_data(obj)

        if not obj:
            obj = self.get_latest_version()

        if obj:
            self.collection_type = obj.collection_type

    def add_concept(self, concept):
        self.concepts.add(concept)

    def get_concepts_count(self):
        return self.concepts.count()

    def get_concepts(self, start=None, end=None):
        """ Use for efficient iteration over paginated concepts. Note that any filter will be applied only to concepts
        from the given range. If you need to filter on all concepts, use get_concepts() without args.
        In order to get the total concepts count, please use get_concepts_count().
        """
        concepts = self.concepts.all()
        if start and end:
            concepts = concepts[start:end]

        return concepts

    def fill_data_from_reference(self, reference):
        self.concepts.add(*reference.concepts)
        self.save()  # update counts

    def current_references(self):
        return list(self.references.values_list('expression', flat=True))

    @staticmethod
    def get_concept_id_by_version_information(expression):
        return get(CollectionReference.get_concept_from_expression(expression), 'id')

    def validate(self, reference):
        reference.full_clean()

        if reference.without_version in [reference.without_version for reference in self.references.all()]:
            raise ValidationError({reference.expression: [REFERENCE_ALREADY_EXISTS]})

        if self.custom_validation_schema == CUSTOM_VALIDATION_SCHEMA_OPENMRS:
            if reference.concepts and reference.concepts.count() == 0:
                return

            concept = reference.concepts[0]
            self.check_concept_uniqueness_in_collection_and_locale_by_name_attribute(
                concept, attribute='is_fully_specified', value=True,
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
            if other_concepts_in_collection.filter(name=name.name, locale=name.locale).exists():
                raise ValidationError(validation_error)

    def add_references(self, expressions, user=None):
        errors = {}

        for expression in expressions:
            reference = CollectionReference(expression=expression, collection=self)
            try:
                self.validate(reference)
                reference.save()
            except Exception as ex:
                errors[expression] = ex.messages if hasattr(ex, 'messages') else ex
                continue

            head = self.head
            ref_hash = {'col_reference': reference}

            error = Collection.persist_changes(head, user, **ref_hash)
            if error:
                errors[expression] = error

        return errors

    @classmethod
    def persist_changes(cls, obj, updated_by, **kwargs):
        col_reference = kwargs.pop('col_reference', False)
        errors = super().persist_changes(obj, updated_by, **kwargs)
        if col_reference and not errors:
            obj.fill_data_from_reference(col_reference)
        return errors

    def seed_concepts(self):
        head = self.head
        if head:
            self.concepts.set(head.concepts.all())

    def seed_references(self):
        head = self.head
        if head:
            references = CollectionReference.objects.bulk_create(
                [CollectionReference(expression=ref.expression, collection=self) for ref in head.references.all()]
            )
            self.references.set(references)

    @staticmethod
    def is_validation_necessary():
        return False


class CollectionReference(models.Model):
    class Meta:
        db_table = 'collection_references'
        unique_together = ('expression', 'collection')

    concepts = None
    mappings = None
    original_expression = None

    expression = models.TextField()
    collection = models.ForeignKey(Collection, related_name='references', on_delete=models.CASCADE)

    @property
    def is_resource_expression(self):
        return len(compact(self.__expression_parts)) == EXPRESSION_RESOURCE_URI_PARTS_COUNT

    @property
    def is_valid_expression(self):
        return isinstance(self.expression, str) and len(compact(self.__expression_parts)) in [
            EXPRESSION_RESOURCE_URI_PARTS_COUNT, EXPRESSION_RESOURCE_VERSION_URI_PARTS_COUNT
        ]

    @property
    def reference_type(self):
        reference = None
        if self.__is_concept_type():
            reference = CONCEPTS_EXPRESSIONS
        if self.__is_mapping_type():
            reference = MAPPINGS_EXPRESSIONS

        return reference

    def __is_concept_type(self):
        return self.expression and "/concepts/" in self.expression

    def __is_mapping_type(self):
        return self.expression and "/mappings/" in self.expression

    @property
    def __expression_parts(self):
        return self.expression.split('/')

    @classmethod
    def version_specified(cls, expression):  # conflicting with is_resource_expression
        return len(expression.split('/')) == EXPRESSION_NUMBER_OF_PARTS_WITH_VERSION

    def get_concepts(self):
        return self.__get_concepts(self.expression)

    @staticmethod
    def __get_concepts(expression):
        kwargs = get(resolve(expression), 'kwargs')
        if kwargs:
            return Concept.get_queryset(kwargs)
        return Concept.objects.none()

    @staticmethod
    def get_concept_from_expression(expression):  # should it use __get_concepts?
        """Returns head"""
        concept_version = Concept.objects.filter(uri=expression).first()
        if concept_version:
            return concept_version.head

        return None

    def clean(self):
        self.original_expression = str(self.expression)

        if not self.is_valid_expression:
            raise ValidationError(dict(detail=[EXPRESSION_INVALID]))

        self.create_entities_from_expressions()

    def create_entities_from_expressions(self):
        self.concepts = self.get_concepts()
        if not self.concepts:
            self.mappings = Mapping.objects.filter(uri=self.expression)
            if not self.mappings:
                raise ValidationError({'detail': ['Expression specified is not valid.']})

    @staticmethod
    def diff(ctx, _from):
        prev_expressions = map(lambda r: r.expression, _from)
        return filter(lambda ref: ref.expression not in prev_expressions, ctx)

    @property
    def without_version(self):
        return '/'.join(self.__expression_parts[0:7]) + '/'
