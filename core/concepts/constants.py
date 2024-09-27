import re

INDEX_TERM = "INDEX_TERM"
CONCEPT_TYPE = 'Concept'
SHORT = "SHORT"
FULLY_SPECIFIED = "FULLY_SPECIFIED"
LOCALES_FULLY_SPECIFIED = (FULLY_SPECIFIED, "Fully Specified")
LOCALES_SHORT = (SHORT, "Short")
LOCALES_SEARCH_INDEX_TERM = (INDEX_TERM, "Index Term")

OPENMRS_ONE_FULLY_SPECIFIED_NAME_PER_LOCALE = 'A concept may not have more than one fully specified name in any locale'
OPENMRS_NO_MORE_THAN_ONE_SHORT_NAME_PER_LOCALE = 'A concept cannot have more than one short name in a locale'
OPENMRS_NAMES_EXCEPT_SHORT_MUST_BE_UNIQUE = 'All names except short names must be unique for a concept and locale'
OPENMRS_FULLY_SPECIFIED_NAME_UNIQUE_PER_SOURCE_LOCALE = 'Concept fully specified name must be unique for ' \
                                                        'same source and locale'
OPENMRS_MUST_HAVE_EXACTLY_ONE_PREFERRED_NAME = 'A concept may not have more than one preferred name (per locale)'
OPENMRS_SHORT_NAME_CANNOT_BE_PREFERRED = 'A short name cannot be marked as locale preferred'
OPENMRS_AT_LEAST_ONE_FULLY_SPECIFIED_NAME = 'A concept must have at least one fully specified name'
OPENMRS_PREFERRED_NAME_UNIQUE_PER_SOURCE_LOCALE = 'Concept preferred name must be unique for same source and locale'
OPENMRS_DESCRIPTION_LOCALE = 'Invalid description locale'
OPENMRS_NAME_LOCALE = 'Invalid name locale'
OPENMRS_DESCRIPTION_TYPE = 'Invalid description type'
OPENMRS_NAME_TYPE = 'Invalid name type'
OPENMRS_DATATYPE = 'Invalid data type'
OPENMRS_CONCEPT_CLASS = 'Invalid concept class'
OPENMRS_EXTERNAL_ID_LENGTH = 36
OPENMRS_CONCEPT_EXTERNAL_ID_ERROR = f'Concept External ID cannot be more than {OPENMRS_EXTERNAL_ID_LENGTH} characters.'
OPENMRS_NAME_EXTERNAL_ID_ERROR = f'Concept name\'s External ID cannot be more than ' \
    f'{OPENMRS_EXTERNAL_ID_LENGTH} characters.'
OPENMRS_DESCRIPTION_EXTERNAL_ID_ERROR = f'Concept description\'s External ID cannot be more than ' \
    f'{OPENMRS_EXTERNAL_ID_LENGTH} characters.'
BASIC_DESCRIPTION_CANNOT_BE_EMPTY = 'Concept description cannot be empty'
BASIC_NAMES_CANNOT_BE_EMPTY = 'A concept must have at least one name'
MAX_LOCALES_LIMIT = 500
MAX_NAMES_LIMIT = f'max limit {MAX_LOCALES_LIMIT} of names exceeded'
MAX_DESCRIPTIONS_LIMIT = f'max limit {MAX_LOCALES_LIMIT} of descriptions exceeded'
CONCEPT_WAS_RETIRED = 'Concept was retired'
CONCEPT_WAS_UNRETIRED = 'Concept was un-retired'
CONCEPT_IS_ALREADY_RETIRED = 'Concept is already retired'
CONCEPT_IS_ALREADY_NOT_RETIRED = 'Concept is already not retired'
ALREADY_EXISTS = "Concept ID must be unique within a source."
PERSIST_CLONE_SPECIFY_USER_ERROR = "Must specify which user is attempting to create a new version."
PERSIST_CLONE_ERROR = 'An error occurred while saving new version.'
PARENT_VERSION_NOT_LATEST_CANNOT_UPDATE_CONCEPT = 'Parent version is not the latest. Cannot update concept.'
CONCEPT_PATTERN = r'[a-zA-Z0-9\-\.\_\@\+\%\s]+'
CONCEPT_REGEX = re.compile(r'^' + CONCEPT_PATTERN + '$')
