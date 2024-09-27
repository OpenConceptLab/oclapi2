from django.core.exceptions import ValidationError
from pydash import get

from core.common.constants import LOOKUP_CONCEPT_CLASSES
from core.common.utils import clean_term
from core.concepts.constants import (
    OPENMRS_MUST_HAVE_EXACTLY_ONE_PREFERRED_NAME,
    OPENMRS_AT_LEAST_ONE_FULLY_SPECIFIED_NAME, OPENMRS_PREFERRED_NAME_UNIQUE_PER_SOURCE_LOCALE,
    OPENMRS_FULLY_SPECIFIED_NAME_UNIQUE_PER_SOURCE_LOCALE, LOCALES_SHORT, OPENMRS_SHORT_NAME_CANNOT_BE_PREFERRED,
    OPENMRS_NAMES_EXCEPT_SHORT_MUST_BE_UNIQUE,
    OPENMRS_ONE_FULLY_SPECIFIED_NAME_PER_LOCALE, OPENMRS_NO_MORE_THAN_ONE_SHORT_NAME_PER_LOCALE, OPENMRS_CONCEPT_CLASS,
    OPENMRS_DATATYPE, OPENMRS_NAME_TYPE, OPENMRS_DESCRIPTION_TYPE, OPENMRS_NAME_LOCALE,
    OPENMRS_DESCRIPTION_LOCALE,
    LOCALES_SEARCH_INDEX_TERM, INDEX_TERM, FULLY_SPECIFIED, SHORT,
    OPENMRS_CONCEPT_EXTERNAL_ID_ERROR, OPENMRS_EXTERNAL_ID_LENGTH, OPENMRS_NAME_EXTERNAL_ID_ERROR,
    OPENMRS_DESCRIPTION_EXTERNAL_ID_ERROR, SYNONYM)
from core.concepts.validators import BaseConceptValidator, message_with_name_details


class OpenMRSConceptValidator(BaseConceptValidator):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.repo = kwargs.pop('repo')
        self.reference_values = kwargs.pop('reference_values')

    def validate_concept_based(self, concept):
        if concept.retired:
            return
        self.validate_external_id(concept)
        self.must_have_exactly_one_preferred_name(concept)
        self.all_non_short_names_must_be_unique(concept)
        self.no_more_than_one_short_name_per_locale(concept)
        self.short_name_cannot_be_marked_as_locale_preferred(concept)
        self.only_one_fully_specified_name_per_locale(concept)
        self.requires_at_least_one_fully_specified_name(concept)
        self.lookup_attributes_should_be_valid(concept)

    def validate_external_id(self, concept):
        if self.is_invalid_external_id(concept):
            raise ValidationError({'external_id': [OPENMRS_CONCEPT_EXTERNAL_ID_ERROR]})

    @staticmethod
    def is_invalid_external_id(instance):
        return len(get(instance, 'external_id') or '') > OPENMRS_EXTERNAL_ID_LENGTH

    def validate_source_based(self, concept):
        if concept.retired:
            return
        self.fully_specified_name_should_be_unique_for_source_and_locale(concept)
        self.preferred_name_should_be_unique_for_source_and_locale(concept)

    @staticmethod
    def must_have_exactly_one_preferred_name(concept):
        preferred_name_locales_in_concept = {}

        for name in concept.saved_unsaved_names:
            if not name.locale_preferred:
                continue

            if name.locale in preferred_name_locales_in_concept:
                raise ValidationError({
                    'names': [message_with_name_details(OPENMRS_MUST_HAVE_EXACTLY_ONE_PREFERRED_NAME, name)]
                })

            preferred_name_locales_in_concept[name.locale] = True

    @staticmethod
    def requires_at_least_one_fully_specified_name(concept):
        # A concept must have at least one fully specified name (across all locales)
        fully_specified_name_count = len(list(filter(lambda n: n.is_fully_specified, concept.saved_unsaved_names)))
        if fully_specified_name_count < 1:
            raise ValidationError({'names': [OPENMRS_AT_LEAST_ONE_FULLY_SPECIFIED_NAME]})

    def preferred_name_should_be_unique_for_source_and_locale(self, concept):
        self.attribute_should_be_unique_for_source_and_locale(
            concept=concept,
            attribute='locale_preferred',
            error_message=OPENMRS_PREFERRED_NAME_UNIQUE_PER_SOURCE_LOCALE,
            filters={'names__locale_preferred': True}
        )

    def fully_specified_name_should_be_unique_for_source_and_locale(self, concept):
        self.attribute_should_be_unique_for_source_and_locale(
            concept,
            attribute='is_fully_specified',
            error_message=OPENMRS_FULLY_SPECIFIED_NAME_UNIQUE_PER_SOURCE_LOCALE
        )

    def attribute_should_be_unique_for_source_and_locale(self, concept, attribute, error_message, filters=None):
        versioned_object_id = concept.versioned_object_id or get(concept, 'head.id')

        names = [name for name in concept.saved_unsaved_names if getattr(name, attribute)]
        for name in names:
            if self.no_other_record_has_same_name(name, versioned_object_id, filters):
                continue

            raise ValidationError({'names': [message_with_name_details(error_message, name)]})

    def no_other_record_has_same_name(self, name, versioned_object_id, filters=None):
        if not self.repo:
            return True

        if not filters:
            filters = {}

        return not self.repo.concepts_set.exclude(
            versioned_object_id=versioned_object_id
        ).exclude(names__type__in=(*LOCALES_SHORT, *LOCALES_SEARCH_INDEX_TERM, *SYNONYM)).filter(
            is_active=True, retired=False, is_latest_version=True, names__locale=name.locale, names__name=name.name,
            **filters
        ).exists()

    @staticmethod
    def short_name_cannot_be_marked_as_locale_preferred(concept):
        short_preferred_names_in_concept = list(filter(
            lambda name: (name.is_short or name.is_search_index_term) and name.locale_preferred,
            concept.saved_unsaved_names
        ))

        if short_preferred_names_in_concept:
            raise ValidationError({
                'names': [message_with_name_details(OPENMRS_SHORT_NAME_CANNOT_BE_PREFERRED,
                                                    short_preferred_names_in_concept[0])]
            })

    @staticmethod
    def all_non_short_names_must_be_unique(concept):
        def name_id(name):
            return name.locale + name.name

        non_short_names_in_concept = list(
            map(name_id, filter(lambda name: not name.is_short, concept.saved_unsaved_names))
        )
        name_set = set(non_short_names_in_concept)

        if len(name_set) != len(non_short_names_in_concept):
            raise ValidationError(
                {'names': [OPENMRS_NAMES_EXCEPT_SHORT_MUST_BE_UNIQUE]})

    @staticmethod
    def only_one_fully_specified_name_per_locale(concept):
        fully_specified_names_per_locale = {}

        for name in concept.saved_unsaved_names:
            if not name.is_fully_specified:
                continue

            if name.locale in fully_specified_names_per_locale:
                raise ValidationError(
                    {'names': [message_with_name_details(OPENMRS_ONE_FULLY_SPECIFIED_NAME_PER_LOCALE, name)]})

            fully_specified_names_per_locale[name.locale] = True

    @staticmethod
    def no_more_than_one_short_name_per_locale(concept):
        short_names_per_locale = {}

        for name in concept.saved_unsaved_names:
            if not name.is_short:
                continue

            if name.locale in short_names_per_locale:
                raise ValidationError(
                    {'names': [message_with_name_details(OPENMRS_NO_MORE_THAN_ONE_SHORT_NAME_PER_LOCALE, name)]})

            short_names_per_locale[name.locale] = True

    def concept_class_should_be_valid_attribute(self, concept):
        if concept.concept_class not in self.reference_values['Classes']:
            raise ValidationError({'concept_class': [OPENMRS_CONCEPT_CLASS]})

    def data_type_should_be_valid_attribute(self, concept):
        if (concept.datatype or 'None') not in self.reference_values['Datatypes']:
            raise ValidationError({'data_type': [OPENMRS_DATATYPE]})

    def name_type_should_be_valid_attribute(self, concept):
        names = concept.saved_unsaved_names
        if not names:
            return

        for name in names:
            name_type = clean_term(name.type or '')
            if name_type in [clean_term(FULLY_SPECIFIED), clean_term(SHORT), clean_term(INDEX_TERM)]:
                continue

            if (name.type or 'None') in self.reference_values['NameTypes']:
                continue

            raise ValidationError({'names': [message_with_name_details(OPENMRS_NAME_TYPE, name)]})

    def description_type_should_be_valid_attribute(self, concept):
        descriptions = concept.saved_unsaved_descriptions
        if not descriptions:
            return

        for description in descriptions:
            if (description.type or 'None') not in self.reference_values['DescriptionTypes']:
                raise ValidationError({'descriptions': [OPENMRS_DESCRIPTION_TYPE]})

    def locale_should_be_valid_attribute(self, concept):
        descriptions = concept.saved_unsaved_descriptions
        names = concept.saved_unsaved_names
        if not names or not descriptions:
            return

        for name in names:
            if name.locale not in self.reference_values['Locales']:
                raise ValidationError({'names': [OPENMRS_NAME_LOCALE]})

        for description in descriptions:
            if description.locale not in self.reference_values['Locales']:
                raise ValidationError({'descriptions': [OPENMRS_DESCRIPTION_LOCALE]})

    def local_external_id_should_be_valid(self, concept):
        names = concept.saved_unsaved_names or []
        for name in names:
            if self.is_invalid_external_id(name):
                raise ValidationError({'names': [message_with_name_details(OPENMRS_NAME_EXTERNAL_ID_ERROR, name)]})

        descriptions = concept.saved_unsaved_descriptions or []
        for description in descriptions:
            if self.is_invalid_external_id(description):
                raise ValidationError({
                    'descriptions': [message_with_name_details(OPENMRS_DESCRIPTION_EXTERNAL_ID_ERROR, description)]})

    def lookup_attributes_should_be_valid(self, concept):
        self.local_external_id_should_be_valid(concept)
        if concept.concept_class in LOOKUP_CONCEPT_CLASSES:
            return

        self.concept_class_should_be_valid_attribute(concept)
        self.data_type_should_be_valid_attribute(concept)
        self.name_type_should_be_valid_attribute(concept)
        self.description_type_should_be_valid_attribute(concept)
        self.locale_should_be_valid_attribute(concept)
