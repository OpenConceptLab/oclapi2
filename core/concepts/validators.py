from django.core.cache import cache
from django.core.exceptions import ValidationError

from core.common.constants import (
    NA, YES, NO, CUSTOM_VALIDATION_SCHEMA_OPENMRS, FIVE_MINS, HEAD, REFERENCE_VALUE_SOURCE_MNEMONICS
)
from core.orgs.models import Organization
from .constants import BASIC_DESCRIPTION_CANNOT_BE_EMPTY, BASIC_NAMES_CANNOT_BE_EMPTY


def message_with_name_details(message, name):
    if name is None:
        return message

    name_str = name.name or NA
    locale = name.locale or NA
    preferred = YES if name.locale_preferred else NO
    return "{}: {} (locale: {}, preferred: {})".format(message, name_str, locale, preferred)


class ValidatorSpecifier:
    def __init__(self):
        from core.concepts.custom_validators import OpenMRSConceptValidator
        self.validator_map = {
            CUSTOM_VALIDATION_SCHEMA_OPENMRS: OpenMRSConceptValidator
        }
        self.reference_values = dict()
        self.repo = None
        self.validation_schema = None

    def with_validation_schema(self, schema):
        self.validation_schema = schema
        return self

    def with_repo(self, repo):
        self.repo = repo

        return self

    def with_reference_values(self):
        ocl_org_filter = Organization.objects.get(mnemonic='OCL')
        if 'reference_sources' not in cache:
            cache.set(
                'reference_sources',
                ocl_org_filter.source_set.filter(mnemonic__in=REFERENCE_VALUE_SOURCE_MNEMONICS, version=HEAD),
                FIVE_MINS
            )

        sources = cache.get('reference_sources')

        self.reference_values = dict()
        for source in sources:
            if source.mnemonic not in cache:
                cache.set(source.mnemonic, self._get_reference_values(source), FIVE_MINS)
            reference_values = cache.get(source.mnemonic)
            self.reference_values[source.mnemonic] = reference_values

        return self

    @staticmethod
    def _get_reference_values(reference_value_source):
        return list(reference_value_source.get_concept_name_locales().values_list('name', flat=True))

    def get(self):
        validator_class = self.validator_map.get(self.validation_schema, BasicConceptValidator)
        return validator_class(repo=self.repo, reference_values=self.reference_values)


class BaseConceptValidator:
    def __init__(self, **kwargs):
        pass

    def validate(self, concept):
        self.validate_concept_based(concept)
        self.validate_source_based(concept)

    def validate_concept_based(self, concept):
        pass

    def validate_source_based(self, concept):
        pass


class BasicConceptValidator(BaseConceptValidator):
    def validate_concept_based(self, concept):
        self.description_cannot_be_null(concept)
        self.must_have_at_least_one_name(concept)

    def validate_source_based(self, concept):
        pass

    @staticmethod
    def must_have_at_least_one_name(concept):
        if not concept.saved_unsaved_names.exists():
            raise ValidationError({'names': [BASIC_NAMES_CANNOT_BE_EMPTY]})

    @staticmethod
    def description_cannot_be_null(concept):
        descriptions = concept.saved_unsaved_descriptions
        if not descriptions.exists():
            return

        if descriptions.filter(name__isnull=True).exists():
            raise ValidationError({'descriptions': [BASIC_DESCRIPTION_CANNOT_BE_EMPTY]})
