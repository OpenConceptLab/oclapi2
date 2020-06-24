from django.core.exceptions import ValidationError
from pydash import get

from core.common.constants import HEAD, LOOKUP_CONCEPT_CLASSES
from core.concepts.constants import (
    OPENMRS_MUST_HAVE_EXACTLY_ONE_PREFERRED_NAME,
    OPENMRS_AT_LEAST_ONE_FULLY_SPECIFIED_NAME, OPENMRS_PREFERRED_NAME_UNIQUE_PER_SOURCE_LOCALE,
    OPENMRS_FULLY_SPECIFIED_NAME_UNIQUE_PER_SOURCE_LOCALE, LOCALES_SHORT, LOCALES_SEARCH_INDEX_TERM,
    OPENMRS_SHORT_NAME_CANNOT_BE_PREFERRED, OPENMRS_NAMES_EXCEPT_SHORT_MUST_BE_UNIQUE,
    OPENMRS_ONE_FULLY_SPECIFIED_NAME_PER_LOCALE, OPENMRS_NO_MORE_THAN_ONE_SHORT_NAME_PER_LOCALE, OPENMRS_CONCEPT_CLASS,
    OPENMRS_DATATYPE, SHORT, FULLY_SPECIFIED, OPENMRS_NAME_TYPE, OPENMRS_DESCRIPTION_TYPE, OPENMRS_NAME_LOCALE,
    OPENMRS_DESCRIPTION_LOCALE
)
from core.concepts.validators import BaseConceptValidator, message_with_name_details


class OpenMRSConceptValidator(BaseConceptValidator):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.repo = kwargs.pop('repo')
        self.reference_values = kwargs.pop('reference_values')

    def validate_concept_based(self, concept):
        self.must_have_exactly_one_preferred_name(concept)
        self.all_non_short_names_must_be_unique(concept)
        self.no_more_than_one_short_name_per_locale(concept)
        self.short_name_cannot_be_marked_as_locale_preferred(concept)
        self.only_one_fully_specified_name_per_locale(concept)
        self.requires_at_least_one_fully_specified_name(concept)
        self.lookup_attributes_should_be_valid(concept)

    def validate_source_based(self, concept):
        self.fully_specified_name_should_be_unique_for_source_and_locale(concept)
        self.preferred_name_should_be_unique_for_source_and_locale(concept)

    @staticmethod
    def must_have_exactly_one_preferred_name(concept):
        preferred_name_locales_in_concept = dict()

        for name in concept.preferred_name_locales:
            if name.locale in preferred_name_locales_in_concept:
                raise ValidationError({
                    'names': [message_with_name_details(OPENMRS_MUST_HAVE_EXACTLY_ONE_PREFERRED_NAME, name)]
                })

            preferred_name_locales_in_concept[name.locale] = True

    @staticmethod
    def requires_at_least_one_fully_specified_name(concept):
        # A concept must have at least one fully specified name (across all locales)
        if not concept.fully_specified_names.exists():
            raise ValidationError({'names': [OPENMRS_AT_LEAST_ONE_FULLY_SPECIFIED_NAME]})

    def preferred_name_should_be_unique_for_source_and_locale(self, concept):
        self.attribute_should_be_unique_for_source_and_locale(
            concept,
            attribute='locale_preferred',
            error_message=OPENMRS_PREFERRED_NAME_UNIQUE_PER_SOURCE_LOCALE
        )

    def fully_specified_name_should_be_unique_for_source_and_locale(self, concept):
        self.attribute_should_be_unique_for_source_and_locale(
            concept,
            attribute='is_fully_specified',
            error_message=OPENMRS_FULLY_SPECIFIED_NAME_UNIQUE_PER_SOURCE_LOCALE
        )

    def attribute_should_be_unique_for_source_and_locale(self, concept, attribute, error_message):
        from core.concepts.models import LocalizedText

        self_id = get(concept, 'head.id', get(concept, 'id'))
        names = concept.saved_unsaved_names.filter(**LocalizedText.get_filter_criteria_for_attribute(attribute))
        for name in names:
            if self.no_other_record_has_same_name(name, self_id):
                continue

            raise ValidationError({
                'names': [message_with_name_details(error_message, name)]
            })

    def no_other_record_has_same_name(self, name, self_id):
        if not self.repo:
            return True

        return not self.repo.concepts_set.exclude(
            id=self_id
        ).exclude(names__type__in=LOCALES_SHORT).filter(
            is_active=True, retired=False, version=HEAD, names__locale=name.locale, names__name=name.name
        ).exists()

    def no_other_record_has_same_names(self, names, self_id):  # can use this if ValidationError can be refactored
        if not self.repo:
            return True

        return not self.repo.concepts_set.exclude(
            id=self_id
        ).exclude(names__type__in=LOCALES_SHORT).filter(
            is_active=True, retired=False, version=HEAD,
            names__locale__in=names.values_list('locale', flat=True),
            names__name__in=names.values_list('name', flat=True),
        ).exists()

    @staticmethod
    def short_name_cannot_be_marked_as_locale_preferred(concept):
        preferred_short_names = concept.preferred_name_locales.filter(
            type__in=[*LOCALES_SHORT, *LOCALES_SEARCH_INDEX_TERM]
        )
        if preferred_short_names.exists():
            raise ValidationError({
                'names': [
                    message_with_name_details(OPENMRS_SHORT_NAME_CANNOT_BE_PREFERRED, preferred_short_names.first())
                ]
            })

    @staticmethod
    def all_non_short_names_must_be_unique(concept):
        non_short_names = concept.non_short_names
        if non_short_names.exists():
            def name_id(name):
                return name.locale + name.name

            if non_short_names.count() != len(set(map(name_id, non_short_names))):
                raise ValidationError(
                    {'names': [OPENMRS_NAMES_EXCEPT_SHORT_MUST_BE_UNIQUE]}
                )

    @staticmethod
    def only_one_fully_specified_name_per_locale(concept):
        fully_specified_names_per_locale = dict()

        for name in concept.fully_specified_names:
            if name.locale in fully_specified_names_per_locale:
                raise ValidationError(
                    {'names': [message_with_name_details(OPENMRS_ONE_FULLY_SPECIFIED_NAME_PER_LOCALE, name)]})

            fully_specified_names_per_locale[name.locale] = True

    @staticmethod
    def no_more_than_one_short_name_per_locale(concept):
        short_names_per_locale = dict()

        for name in concept.short_names:
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
        invalid_names = concept.saved_unsaved_names.exclude(
            type__in=[FULLY_SPECIFIED, SHORT, *self.reference_values['NameTypes']]
        )
        if invalid_names.exists():
            raise ValidationError({'names': [message_with_name_details(OPENMRS_NAME_TYPE, invalid_names.first())]})

    def description_type_should_be_valid_attribute(self, concept):
        invalid_descriptions = concept.saved_unsaved_descriptions.exclude(
            type__in=self.reference_values['DescriptionTypes']
        )
        if invalid_descriptions.exists():
            raise ValidationError({'descriptions': [OPENMRS_DESCRIPTION_TYPE]})

    def locale_should_be_valid_attribute(self, concept):
        if not concept.names or not concept.descriptions:
            return

        invalid_names = concept.saved_unsaved_names.exclude(locale__in=self.reference_values['Locales'])
        if invalid_names.exists():
            raise ValidationError({'names': [OPENMRS_NAME_LOCALE]})

        invalid_descriptions = concept.saved_unsaved_descriptions.exclude(locale__in=self.reference_values['Locales'])
        if invalid_descriptions.exists():
            raise ValidationError({'descriptions': [OPENMRS_DESCRIPTION_LOCALE]})

    def lookup_attributes_should_be_valid(self, concept):
        if concept.concept_class in LOOKUP_CONCEPT_CLASSES:
            return

        self.concept_class_should_be_valid_attribute(concept)
        self.data_type_should_be_valid_attribute(concept)
        self.name_type_should_be_valid_attribute(concept)
        self.description_type_should_be_valid_attribute(concept)
        self.locale_should_be_valid_attribute(concept)
