from django.core.exceptions import ValidationError
from django.db.models import F
from pydash import get

from .constants import OPENMRS_SINGLE_MAPPING_BETWEEN_TWO_CONCEPTS, OPENMRS_INVALID_MAPTYPE,\
    OPENMRS_EXTERNAL_ID_LENGTH, OPENMRS_MAPPING_EXTERNAL_ID_ERROR


class OpenMRSMappingValidator:
    def __init__(self, mapping):
        self.mapping = mapping

    def validate(self):
        self.should_have_valid_external_id()
        self.pair_must_be_unique()
        self.lookup_attributes_should_be_valid()

    def should_have_valid_external_id(self):
        if len(get(self.mapping, 'external_id') or '') > OPENMRS_EXTERNAL_ID_LENGTH:
            raise ValidationError({'external_id': [OPENMRS_MAPPING_EXTERNAL_ID_ERROR]})

    def pair_must_be_unique(self):
        from .models import Mapping
        queryset = Mapping.objects.filter(
            parent=self.mapping.parent, is_active=True, retired=False,
            from_source_url=self.mapping.from_source_url, from_concept_code=self.mapping.from_concept_code,
            to_source_url=self.mapping.to_source_url, to_concept_code=self.mapping.to_concept_code,
        )

        if self.mapping.versioned_object_id:
            queryset = queryset.exclude(versioned_object_id=self.mapping.versioned_object_id)

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
