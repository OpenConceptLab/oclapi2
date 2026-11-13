SOURCE_TYPE = 'Source'
SOURCE_VERSION_TYPE = 'Source Version'

DELETE_FAILURE = 'Could not delete Source.'
DELETE_SUCCESS = 'Successfully deleted Source.'
VERSION_ALREADY_EXISTS = "Source version '{}' already exist."
HIERARCHY_ROOT_MUST_BELONG_TO_SAME_SOURCE = 'Hierarchy Root must belong to the same Source.'
SEEDED_FROM_REPO_VERSION = 'repo_version'
SEEDED_FROM_IMPORT = 'import'
ISOLATED_VERSION_NEEDS_ONE_SOURCE_VERSION = \
    'A seed_from_import import needs exactly one Source Version line, after its concepts and mappings.'
ISOLATED_VERSION_CONTENT_FAILED = 'Not created: some of the version content failed to import.'
ISOLATED_VERSION_LINE_INVALID = 'The Source Version line needs an id.'
ISOLATED_VERSION_EXISTS = 'This Source Version already exists.'
ISOLATED_VERSION_NO_EDIT_ACCESS = 'You need edit access to the source to import a version into it.'
HIERARCHY_ROOT_CANNOT_BE_DROPPED = 'Hierarchy Root must be present in a version seeded from an import.'
ISOLATED_METADATA_FIELDS = (
    'description', 'full_name', 'website', 'external_id', 'canonical_url', 'identifier', 'contact', 'jurisdiction',
    'publisher', 'purpose', 'copyright', 'revision_date', 'text', 'experimental', 'source_type', 'content_type',
    'collection_reference', 'hierarchy_meaning', 'case_sensitive', 'compositional', 'version_needed',
)
ISOLATED_REQUIRED_METADATA_FIELDS = ('name', 'default_locale', 'supported_locales')
HIERARCHY_MEANINGS = (
    ('grouped-by', 'grouped-by'),
    ('is-a', 'is-a'),
    ('part-of', 'part-of'),
    ('classified-with', 'classified-with'),
)
AUTO_ID_SEQUENTIAL = 'sequential'
AUTO_ID_UUID = 'uuid'
AUTO_ID_CHOICES = (
    (AUTO_ID_SEQUENTIAL, 'Sequential'),
    (AUTO_ID_UUID, 'UUID')
)
LOCALE_EXTERNAL_AUTO_ID_CHOICES = (
    (AUTO_ID_UUID, 'UUID'),
)

DROPPED_CHILDREN_CHUNK_SIZE = 5000
