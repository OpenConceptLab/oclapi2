import re

# What an answer too costly to give says, by what was asked
SEARCH_TOO_COSTLY = 'The search would take too long to answer. Narrow it, for example with url, _id or version.'
EXPANSION_TOO_COSTLY = ('The expansion would take too long to answer. Narrow it, for example with filter, or page it '
                        'with count.')
TRANSLATION_TOO_COSTLY = ('The translation would take too long to answer. Narrow it, for example with url, version or '
                          'targetsystem.')
CODE_SYSTEMS_TOO_COSTLY = 'The list of code systems would take too long to answer.'

FHIR_VERSION = '4.0'  # the fhirVersion MIME parameter, and what $versions answers
FHIR_RELEASE = '4.0.1'  # CapabilityStatement.fhirVersion
ACCEPTED_FHIR_VERSIONS = (FHIR_VERSION, FHIR_RELEASE)

FHIR_JSON = 'application/fhir+json'
FHIR_XML = 'application/fhir+xml'
# Other names for the two formats: the short _format values, the DSTU2 types, and the generic XML types R4 says
# a server SHOULD accept. Plain application/json stays itself, so OCL's own clients keep getting it.
MEDIA_TYPE_ALIASES = {
    'json': FHIR_JSON,
    'application/json+fhir': FHIR_JSON,
    'xml': FHIR_XML,
    'application/xml+fhir': FHIR_XML,
    'application/xml': FHIR_XML,
    'text/xml': FHIR_XML,
}
FORMAT_PARAMETERS = ('_format', 'format')
# Protocol parameters: they choose the response format, and are never operation or search input
CONTROL_PARAMETERS = frozenset({'_format', 'format', '_pretty'})
RESOURCE_TYPES = ('CodeSystem', 'ValueSet', 'ConceptMap')
OWNER_BASE_PATHS = ('metadata', '$versions')  # what an owner base answers besides its resource types

FHIR_NS = 'http://hl7.org/fhir'
XHTML_NS = 'http://www.w3.org/1999/xhtml'
EXTENSION_ELEMENTS = ('extension', 'modifierExtension')
EMPTY_VALUES = (None, '', {}, [])
# What XML 1.0 can't hold: C0 controls other than tab, LF and CR, lone surrogates, U+FFFE and U+FFFF
XML_FORBIDDEN_CHARACTERS = re.compile('[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]')
REPLACEMENT_CHARACTER = '\ufffd'
XML_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_.-]*$')
