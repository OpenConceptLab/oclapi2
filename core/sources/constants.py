SOURCE_TYPE = 'Source'
SOURCE_VERSION_TYPE = 'Source Version'

DELETE_FAILURE = 'Could not delete Source.'
DELETE_SUCCESS = 'Successfully deleted Source.'
VERSION_ALREADY_EXISTS = "Source version '{}' already exist."
CONTENT_REFERRED_PRIVATELY = 'Could not delete source "{}". One or more resources in the source are ' \
                             'referenced by private repositories. Contact an OCL administrator for more information.'
HIERARCHY_ROOT_MUST_BELONG_TO_SAME_SOURCE = 'Hierarchy Root must belong to the same Source.'
HIERARCHY_MEANINGS = (
    ('grouped-by', 'grouped-by'),
    ('is-a', 'is-a'),
    ('part-of', 'part-of'),
    ('classified-with', 'classified-with'),
)
