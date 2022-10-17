from django.conf import settings
from django.core.exceptions import ValidationError

from core.sources.models import Source
from .constants import (
    MUST_SPECIFY_TO_CONCEPT_OR_TO_SOURCE,
    MUST_SPECIFY_FROM_CONCEPT, TO_SOURCE_UNIQUE_ATTRIBUTES_ERROR_MESSAGE
)
from .custom_validators import OpenMRSMappingValidator


class MappingValidationMixin:
    def clean(self):
        from .models import Mapping
        errors = []
        if not self.from_concept_code:
            errors.append(MUST_SPECIFY_FROM_CONCEPT)
        if not self.to_concept_code:
            errors.append(MUST_SPECIFY_TO_CONCEPT_OR_TO_SOURCE)
        if Mapping.objects.exclude(
                versioned_object_id=self.versioned_object_id
        ).filter(
            parent_id=self.parent_id, map_type=self.map_type,
            from_concept_code=self.from_concept_code, to_concept_code=self.to_concept_code,
            to_source_url=self.to_source_url, from_source_url=self.from_source_url,
            is_latest_version=True, retired=False
        ).exists():
            errors.append(TO_SOURCE_UNIQUE_ATTRIBUTES_ERROR_MESSAGE)

        if errors:
            raise ValidationError(' '.join(errors))

        if settings.DISABLE_VALIDATION:
            return
        try:
            if self.parent.is_openmrs_schema:
                custom_validator = OpenMRSMappingValidator(self)
                custom_validator.validate()
        except Source.DoesNotExist as ex:
            raise ValidationError("There's no Source") from ex
