from django.core.exceptions import ValidationError

from core.common.constants import LOOKUP_ATTRIBUTES_MUST_BE_IMPORTED
from core.mappings.constants import OPENMRS_SINGLE_MAPPING_BETWEEN_TWO_CONCEPTS, OPENMRS_INVALID_MAPTYPE


class OpenMRSMappingValidator:
    def __init__(self, mapping):
        self.mapping = mapping

    def validate(self):
        self.pair_must_be_unique()
        self.lookup_attributes_should_be_valid()

    def pair_must_be_unique(self):
        from .models import Mapping
        intersection = Mapping.objects.none()
        base_queryset = Mapping.objects.exclude(id=self.mapping.id).filter(
            parent=self.mapping.parent_source, from_concept=self.mapping.from_concept, is_active=True, retired=False,
            version=self.version
        )

        if self.mapping.to_concept:
            intersection = base_queryset.filter(to_concept=self.mapping.to_concept)
        elif self.mapping.to_concept_code:
            intersection = base_queryset.filter(
                to_source=self.mapping.to_source, to_concept_code=self.mapping.to_concept_code
            )
        elif self.mapping.to_concept_name:
            intersection = base_queryset.filter(
                to_source=self.mapping.to_source, to_concept_name=self.mapping.to_concept_name
            ).exclude(id=self.mapping.id)

        if intersection.exists():
            raise ValidationError(OPENMRS_SINGLE_MAPPING_BETWEEN_TWO_CONCEPTS)

    def map_type_should_be_valid_attribute(self, org):
        is_data_type_valid = self.is_attribute_valid(self.mapping.map_type, org, 'MapTypes', 'MapType')

        if not is_data_type_valid:
            raise ValidationError({'map_type': [OPENMRS_INVALID_MAPTYPE]})

    @staticmethod
    def is_attribute_valid(attribute_property, org, source_mnemonic, concept_class):
        from core.sources.models import Source
        from core.concepts.models import Concept

        attribute_types_source_filter = Source.objects.filter(parent_id=org.id, mnemonic=source_mnemonic)

        if not attribute_types_source_filter.exists():
            raise ValidationError({'non_field_errors': [LOOKUP_ATTRIBUTES_MUST_BE_IMPORTED]})

        matching_attribute_types = {
            'retired': False, 'is_active': True, 'concept_class': concept_class,
            'parent_id': attribute_types_source_filter.first().id, 'names__name': attribute_property or 'None'
        }

        return Concept.objects.filter(matching_attribute_types).exists()

    def lookup_attributes_should_be_valid(self):
        from core.orgs.models import Organization
        ocl_org_filter = Organization.objects.filter(mnemonic='OCL')

        if not ocl_org_filter.exists():
            raise ValidationError({'non_field_errors': [LOOKUP_ATTRIBUTES_MUST_BE_IMPORTED]})

        org = ocl_org_filter.first()

        self.map_type_should_be_valid_attribute(org)
