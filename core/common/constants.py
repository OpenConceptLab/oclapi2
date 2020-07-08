import re

HEAD = 'HEAD'
TEMP = '--TEMP--'

NAMESPACE_PATTERN = r'[a-zA-Z0-9\-\.\_\@]+'
NAMESPACE_REGEX = re.compile(r'^' + NAMESPACE_PATTERN + '$')

ACCESS_TYPE_VIEW = 'View'
ACCESS_TYPE_EDIT = 'Edit'
ACCESS_TYPE_NONE = 'None'
DEFAULT_ACCESS_TYPE = ACCESS_TYPE_VIEW
ACCESS_TYPE_CHOICES = ((ACCESS_TYPE_VIEW, 'View'),
                       (ACCESS_TYPE_EDIT, 'Edit'),
                       (ACCESS_TYPE_NONE, 'None'))
SUPER_ADMIN_USER_ID = 1
UPDATED_SINCE_PARAM = 'updatedSince'
RELEASED_PARAM = 'released'
PROCESSING_PARAM = 'processing'
ISO_639_1 = 'ISO 639-1'
NA = 'n/a'
YES = 'yes'
NO = 'no'
CUSTOM_VALIDATION_SCHEMA_OPENMRS = 'OpenMRS'
LOOKUP_CONCEPT_CLASSES = ['Concept Class', 'Datatype', 'NameType', 'DescriptionType', 'MapType', 'Locale']
LOOKUP_SOURCES = ['Classes', 'Datatypes', 'NameTypes', 'DescriptionTypes', 'MapTypes', 'Locales']
REFERENCE_VALUE_SOURCE_MNEMONICS = ['Classes', 'Datatypes', 'NameTypes', 'DescriptionTypes', 'Locales']
FIVE_MINS = 5 * 60
DEFAULT_REPOSITORY_TYPE = 'Collection'
OPENMRS_REPOSITORY_TYPE = 'OpenMRSDictionary'
