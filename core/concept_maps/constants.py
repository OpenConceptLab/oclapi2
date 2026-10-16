RESOURCE_TYPE = 'ConceptMap'

# ConceptMap relationships as FHIR R5 names them (http://hl7.org/fhir/concept-map-relationship): the version-neutral
# form, which an adapter writes as its version's codes (core.fhir.adapters.r4: R4's equivalence)
EQUIVALENT = 'equivalent'
RELATED_TO = 'related-to'
SOURCE_IS_NARROWER = 'source-is-narrower-than-target'
SOURCE_IS_BROADER = 'source-is-broader-than-target'
NOT_RELATED_TO = 'not-related-to'

# The relationship of each OCL map type, the map type spelt as core.fhir.ops.translate.map_type_key spells it (upper
# case, hyphens for spaces, trimmed), so SAME-AS, SAME AS and Same As are one (design D27). Any other map type is
# RELATED_TO. A mapping says its from concept is NARROWER-THAN (or BROADER-THAN) its to concept.
MAP_TYPE_RELATIONSHIPS = {
    'SAME-AS': EQUIVALENT,
    'NARROWER-THAN': SOURCE_IS_NARROWER,
    'BROADER-THAN': SOURCE_IS_BROADER,
    'NOT-SAME-AS': NOT_RELATED_TO,
}
# The map type a relationship is stored as, when a ConceptMap is written
RELATIONSHIP_MAP_TYPES = {relationship: map_type for map_type, relationship in MAP_TYPE_RELATIONSHIPS.items()}
