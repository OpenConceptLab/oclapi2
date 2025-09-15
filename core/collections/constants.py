COLLECTION_TYPE = 'Collection'
COLLECTION_REFERENCE_TYPE = 'CollectionReference'
COLLECTION_VERSION_TYPE = 'Collection Version'
EXPRESSION_NUMBER_OF_PARTS_WITH_VERSION = 9
INCLUDE_REFERENCES_PARAM = 'includeReferences'

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

DELETE_FAILURE = 'Could not delete Collection.'
DELETE_SUCCESS = 'Successfully deleted Collection.'
NO_MATCH = 'No Collection matches the given query.'
VERSION_ALREADY_EXISTS = "Collection version '{}' already exist."
SOURCE_MAPPINGS = 'sourcemappings'
SOURCE_TO_CONCEPTS = 'sourcetoconcepts'
TRANSFORM_TO_RESOURCE_VERSIONS = 'resourceversions'
TRANSFORM_TO_EXTENSIONAL = 'extensional'

CONCEPT_REFERENCE_TYPE = 'concepts'
MAPPING_REFERENCE_TYPE = 'mappings'
REFERENCE_TYPE_CHOICES = (
    (CONCEPT_REFERENCE_TYPE, 'Concepts'),
    (MAPPING_REFERENCE_TYPE, 'Mappings')
)
