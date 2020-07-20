from django.conf import settings
from django.core.exceptions import ValidationError

from core.common.constants import CUSTOM_VALIDATION_SCHEMA_OPENMRS
from core.sources.models import Source
from .constants import (
    MUST_SPECIFY_EITHER_TO_CONCEPT_OR_TO_SOURCE,
    CANNOT_SPECIFY_BOTH_TO_CONCEPT_OR_TO_SOURCE,
    CANNOT_MAP_CONCEPT_TO_SELF, MUST_SPECIFY_FROM_CONCEPT, TO_CONCEPT_UNIQUE_ATTRIBUTES_ERROR_MESSAGE,
    TO_SOURCE_UNIQUE_ATTRIBUTES_ERROR_MESSAGE
)
from .custom_validators import OpenMRSMappingValidator


class MappingValidationMixin:
    def clean(self):
        basic_errors = []
        if not self.from_concept_id:
            basic_errors.append(MUST_SPECIFY_FROM_CONCEPT)
        if self.from_concept_id == self.to_concept_id:
            basic_errors.append(CANNOT_MAP_CONCEPT_TO_SELF)

        if self.to_concept_id and (self.to_source_id or self.to_concept_code):
            basic_errors.append(MUST_SPECIFY_EITHER_TO_CONCEPT_OR_TO_SOURCE)
        elif not (self.to_concept_id or (self.to_source_id and self.to_concept_code)):
            basic_errors.append(CANNOT_SPECIFY_BOTH_TO_CONCEPT_OR_TO_SOURCE)
        elif self.from_concept_id:
            from .models import Mapping
            queryset = Mapping.objects.exclude(id=self.id).filter(
                parent_id=self.parent_id, map_type=self.map_type, from_concept_id=self.from_concept_id,
                version=self.version
            )
            if self.to_source_id:
                if queryset.filter(
                        to_source_id=self.to_source_id, to_concept_code=self.to_concept_code
                ).exists():
                    basic_errors.append(TO_SOURCE_UNIQUE_ATTRIBUTES_ERROR_MESSAGE)
            else:
                if queryset.filter(to_concept_id=self.to_concept_id).exists():
                    basic_errors.append(TO_CONCEPT_UNIQUE_ATTRIBUTES_ERROR_MESSAGE)

        if basic_errors:
            raise ValidationError(' '.join(basic_errors))

        if settings.DISABLE_VALIDATION:
            return
        try:
            if self.parent.custom_validation_schema == CUSTOM_VALIDATION_SCHEMA_OPENMRS:
                custom_validator = OpenMRSMappingValidator(self)
                custom_validator.validate()
        except Source.DoesNotExist:
            raise ValidationError("There's no Source")
