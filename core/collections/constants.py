COLLECTION_TYPE = 'Collection'
COLLECTION_REFERENCE_TYPE = 'CollectionReference'
COLLECTION_VERSION_TYPE = 'Collection Version'
EXPRESSION_NUMBER_OF_PARTS_WITH_VERSION = 9
INCLUDE_REFERENCES_PARAM = 'includeReferences'

REFERENCE_ALREADY_EXISTS = 'Concept or Mapping reference name must be unique in a collection.'
CONCEPT_FULLY_SPECIFIED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE = "Concept fully specified name must be unique for " \
                                                                "same collection and locale."
CONCEPT_PREFERRED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE = "Concept preferred name must be unique for same collection " \
                                                          "and locale."
CONCEPT_VERSION_ADDED_TO_COLLECTION = 'Added the concept version to the collection. ' \
                                      'Future updates will not be added automatically.'
MAPPING_VERSION_ADDED_TO_COLLECTION = 'Added the mapping version to the collection. ' \
                                      'Future updates will not be added automatically.'
CONCEPT_ADDED_TO_COLLECTION_FMT = 'The concept {} is successfully added to collection {}'
MAPPING_ADDED_TO_COLLECTION_FMT = 'The mapping {} is successfully added to collection {}'
UNKNOWN_REFERENCE_ADDED_TO_COLLECTION_FMT = 'The reference is successfully added to collection {}'
REFERENCE_RESOLVED_TO_NOTHING_FMT = 'The reference is saved in collection {}, but it resolved to no concepts ' \
                                    'or mappings in {}'
REFERENCE_VERSION_NOT_HELD_FMT = 'The reference is saved in collection {}, but no version of {} holds ' \
                                 'resource version {}'
REFERENCE_NAMED_VERSION_NOT_HELD_FMT = 'The reference is saved in collection {}, but {} does not hold ' \
                                       'resource version {}'
REFERENCE_EXCLUDED_BY_PARAMETERS_FMT = "The reference is saved in collection {}, but the expansion's parameters " \
                                       "exclude everything it resolved to in {}"
REFERENCE_FOUND_NOTHING = 'found_nothing'
REFERENCE_EXCLUDED_BY_PARAMETERS = 'excluded_by_parameters'
REFERENCE_VERSION_NOT_HELD = 'resource_version_not_held'
# a value set the reference names can't narrow it: not found (or not viewable by its creator), or never expanded
REFERENCE_VALUESET_NOT_FOUND = 'valueset_not_found'
REFERENCE_VALUESET_NOT_EXPANDED = 'valueset_not_expanded'
REFERENCE_VALUESET_NOT_FOUND_FMT = 'The reference is saved in collection {}, but it adds nothing: value set {} ' \
                                   'could not be found'
REFERENCE_NOT_SAVED_VALUESET_NOT_FOUND_FMT = 'The reference is not saved in collection {}: it would add nothing, ' \
                                            'as value set {} could not be found'
REFERENCE_NOT_SAVED_VALUESET_NOT_EXPANDED_FMT = 'The reference is not saved in collection {}: it would add ' \
                                               'nothing, as value set {} has no default expansion'
REFERENCE_VALUESET_NOT_EXPANDED_FMT = 'The reference is saved in collection {}, but it adds nothing: value set {} ' \
                                      'has no default expansion'

DELETE_FAILURE = 'Could not delete Collection.'
DELETE_SUCCESS = 'Successfully deleted Collection.'
NO_MATCH = 'No Collection matches the given query.'
VERSION_ALREADY_EXISTS = "Collection version '{}' already exist."
SOURCE_MAPPINGS = 'sourcemappings'
SOURCE_TO_CONCEPTS = 'sourcetoconcepts'
TRANSFORM_TO_RESOURCE_VERSIONS = 'resourceversions'  # intentional
TRANSFORM_TO_EXTENSIONAL = 'extensional'

CONCEPT_REFERENCE_TYPE = 'concepts'
MAPPING_REFERENCE_TYPE = 'mappings'
REFERENCE_TYPE_CHOICES = (
    (CONCEPT_REFERENCE_TYPE, 'Concepts'),
    (MAPPING_REFERENCE_TYPE, 'Mappings')
)

RESOURCE_VERSION_REPO_RESOLUTION_TOGGLE = 'RESOURCE_VERSION_REPO_RESOLUTION_TOGGLE'
UNRESOLVED_RESOURCE_VERSION_TYPE = 'reference.resource_version'
INTENSIONAL_REFERENCE_RESOURCE_VERSIONS_TOGGLE = 'INTENSIONAL_REFERENCE_RESOURCE_VERSIONS_TOGGLE'
EXPANSION_LOCKED_REPO_VERSION_TOGGLE = 'EXPANSION_LOCKED_REPO_VERSION_TOGGLE'
EXPANSION_HEALTH_TOGGLE = 'EXPANSION_HEALTH_TOGGLE'
EXPANSION_HEALTH_DEBOUNCE_SECONDS = 60
# a repo version's fields that expansion health reads
HEALTH_RELEVANT_VERSION_FIELDS = {'released', 'retired', 'is_active', 'public_access', 'expansion_uri'}
EXPANSION_HEALTH_RECHECK_LIMIT = 5000
# the nightly recheck spreads its runs over this long, so they don't all reach the workers at once
EXPANSION_HEALTH_RECHECK_WINDOW_SECONDS = 4 * 60 * 60
VERSION_EXPANSION_LOCK = 'lock'
VERSION_EXPANSION_LATEST = 'latest'
VERSION_EXPANSION_NONE = 'none'
VERSION_EXPANSION_CHOICES = (VERSION_EXPANSION_LOCK, VERSION_EXPANSION_LATEST, VERSION_EXPANSION_NONE)
