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
    # Before WP6 a FHIR ConceptMap write stored an R4 equivalence other than equivalent as the map type, verbatim:
    # each is the relationship HL7's R4/R5 conversion gives it (unmatched, R5's noMap, is not-related-to). R5's
    # relationship codes are themselves.
    'EQUIVALENT': EQUIVALENT,
    'EQUAL': EQUIVALENT,
    'WIDER': SOURCE_IS_NARROWER,
    'SUBSUMES': SOURCE_IS_NARROWER,
    'NARROWER': SOURCE_IS_BROADER,
    'SPECIALIZES': SOURCE_IS_BROADER,
    'RELATEDTO': RELATED_TO,
    'INEXACT': RELATED_TO,
    'DISJOINT': NOT_RELATED_TO,
    'UNMATCHED': NOT_RELATED_TO,
    'RELATED-TO': RELATED_TO,
    'SOURCE-IS-NARROWER-THAN-TARGET': SOURCE_IS_NARROWER,
    'SOURCE-IS-BROADER-THAN-TARGET': SOURCE_IS_BROADER,
    'NOT-RELATED-TO': NOT_RELATED_TO,
}
# The OCL map type a relationship is stored as, when a ConceptMap is written; related-to has none (the equivalence
# written is kept)
RELATIONSHIP_MAP_TYPES = {
    EQUIVALENT: 'SAME-AS',
    SOURCE_IS_NARROWER: 'NARROWER-THAN',
    SOURCE_IS_BROADER: 'BROADER-THAN',
    NOT_RELATED_TO: 'NOT-SAME-AS',
}
