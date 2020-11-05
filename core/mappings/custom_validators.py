from django.core.exceptions import ValidationError
from django.db.models import F

from .constants import OPENMRS_SINGLE_MAPPING_BETWEEN_TWO_CONCEPTS, OPENMRS_INVALID_MAPTYPE


class OpenMRSMappingValidator:
    def __init__(self, mapping):
        self.mapping = mapping

    def validate(self):
        self.pair_must_be_unique()
        self.lookup_attributes_should_be_valid()

    def pair_must_be_unique(self):
        from .models import Mapping
        queryset = Mapping.objects.filter(
            parent=self.mapping.parent, from_concept_id=self.mapping.from_concept_id, is_active=True, retired=False,
        )

        if self.mapping.versioned_object_id:
            queryset = queryset.exclude(versioned_object_id=self.mapping.versioned_object_id)
        if self.mapping.to_concept_id:
            queryset = queryset.filter(to_concept_id=self.mapping.to_concept_id)
        elif self.mapping.to_concept_code:
            queryset = queryset.filter(
                to_source_id=self.mapping.to_source_id, to_concept_code=self.mapping.to_concept_code
            )
        elif self.mapping.to_concept_name:
            queryset = queryset.filter(
                to_source_id=self.mapping.to_source_id, to_concept_name=self.mapping.to_concept_name
            )

        if queryset.exists():
            raise ValidationError(OPENMRS_SINGLE_MAPPING_BETWEEN_TWO_CONCEPTS)

    def lookup_attributes_should_be_valid(self):
        from core.concepts.models import Concept
        if not Concept.objects.filter(
                parent__mnemonic='MapTypes', parent__organization__mnemonic='OCL',
                id=F('versioned_object_id'), retired=False, is_active=True,
                concept_class='MapType', names__name=self.mapping.map_type or 'None',

        ).exists():
            raise ValidationError({'map_type': [OPENMRS_INVALID_MAPTYPE]})
