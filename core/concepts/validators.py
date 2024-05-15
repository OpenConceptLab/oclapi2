from django.core.exceptions import ValidationError

from core.common.constants import (
    NA, YES, NO, OPENMRS_VALIDATION_SCHEMA, HEAD, REFERENCE_VALUE_SOURCE_MNEMONICS, DEFAULT_VALIDATION_SCHEMA
)
from core.orgs.models import Organization
from .constants import BASIC_DESCRIPTION_CANNOT_BE_EMPTY, BASIC_NAMES_CANNOT_BE_EMPTY


def message_with_name_details(message, name):
    if name is None:
        return message  # pragma: no cover

    name_str = name.name or NA
    locale = name.locale or NA
    preferred = YES if name.locale_preferred else NO
    return f"{message}: {name_str} (locale: {locale}, preferred: {preferred})"


class ValidatorSpecifier:
    def __init__(self):
        from core.concepts.custom_validators import OpenMRSConceptValidator
        self.validator_map = {
            OPENMRS_VALIDATION_SCHEMA: OpenMRSConceptValidator
        }
        self.reference_values = {}
        self.repo = None
        self.validation_schema = None

    def with_validation_schema(self, schema):
        self.validation_schema = schema
        return self

    def with_repo(self, repo):
        self.repo = repo

        return self

    def with_reference_values(self):
        self.reference_values = {}
        if self.validation_schema and self.validation_schema != DEFAULT_VALIDATION_SCHEMA:
            ocl = Organization.objects.get(mnemonic='OCL')
            sources = ocl.source_set.filter(mnemonic__in=REFERENCE_VALUE_SOURCE_MNEMONICS, version=HEAD)
            for source in sources:
                self.reference_values[source.mnemonic] = self._get_reference_values(source)

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
        pass  # pragma: no cover

    def validate_source_based(self, concept):
        pass  # pragma: no cover


class BasicConceptValidator(BaseConceptValidator):
    def validate_concept_based(self, concept):
        if concept.retired:
            return
        self.description_cannot_be_null(concept)
        self.must_have_at_least_one_name(concept)

    def validate_source_based(self, concept):
        pass

    @staticmethod
    def must_have_at_least_one_name(concept):
        if concept.saved_unsaved_names:
            return

        raise ValidationError({'names': [BASIC_NAMES_CANNOT_BE_EMPTY]})  # pragma: no cover

    @staticmethod
    def description_cannot_be_null(concept):
        descriptions = concept.saved_unsaved_descriptions
        if not descriptions:
            return

        empty_descriptions = list(filter(lambda description: not description.name, descriptions))

        if empty_descriptions:
            raise ValidationError({'descriptions': [BASIC_DESCRIPTION_CANNOT_BE_EMPTY]})  # pragma: no cover
