COLLECTION_TYPE = 'Collection'
COLLECTION_VERSION_TYPE = 'Collection Version'
EXPRESSION_RESOURCE_URI_PARTS_COUNT = 6
EXPRESSION_RESOURCE_VERSION_URI_PARTS_COUNT = 7
EXPRESSION_REFERENCE_TYPE_PART_INDEX = 5
EXPRESSION_NUMBER_OF_PARTS_WITH_VERSION = 9
CONCEPTS_EXPRESSIONS = 'concepts'
MAPPINGS_EXPRESSIONS = 'mappings'
INCLUDE_REFERENCES_PARAM = 'includeReferences'
ALL_SYMBOL = '*'

EXPRESSION_INVALID = 'Expression specified is not valid.'
REFERENCE_ALREADY_EXISTS = 'Concept or Mapping reference name must be unique in a collection.'
CONCEPT_FULLY_SPECIFIED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE = "Concept fully specified name must be unique for " \
                                                                "same collection and locale."
CONCEPT_PREFERRED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE = "Concept preferred name must be unique for same collection " \
                                                          "and locale."
HEAD_OF_CONCEPT_ADDED_TO_COLLECTION = 'Added the latest versions of concept to the collection. ' \
                                      'Future updates will not be added automatically.'
HEAD_OF_MAPPING_ADDED_TO_COLLECTION = 'Added the latest versions of mapping to the collection. ' \
                                      'Future updates will not be added automatically.'
CONCEPT_ADDED_TO_COLLECTION_FMT = 'The concept {} is successfully added to collection {}'
MAPPING_ADDED_TO_COLLECTION_FMT = 'The mapping {} is successfully added to collection {}'

DELETE_FAILURE = 'Could not delete Collection.'
DELETE_SUCCESS = 'Successfully deleted Collection.'
NO_MATCH = 'No Collection matches the given query.'
VERSION_ALREADY_EXISTS = "Collection version '{}' already exist."
SOURCE_MAPPINGS = 'sourcemappings'
