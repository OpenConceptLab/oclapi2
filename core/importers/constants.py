ALREADY_QUEUED = 'The same import has been already queued'
INVALID_UPDATE_IF_EXISTS = "update_if_exists must be either 'true' or 'false'"
INVALID_INDEX = "index must be either 'true' or 'false'"
NO_CONTENT_TO_IMPORT = 'No content to import'
INVALID_SEED_FROM_IMPORT = "'seed_from_import' must be 'true' or 'false'."
SEED_FROM_IMPORT_IMPORT_TYPE = 'seed_from_import is not supported for npm imports.'
SEED_FROM_IMPORT_ENDPOINT = 'seed_from_import is not supported by this endpoint.'
# The off-peak indexing of what large imports deferred (OpenConceptLab/ocl_online#268, DeferredImportIndex.run)
DEFERRED_INDEX_LOCK_KEY = 'deferred_import_indexing'
DEFERRED_INDEX_LOCK_SECONDS = 60 * 60  # renewed after each row, so a runner that dies frees the lock within this
DEFERRED_INDEX_MAX_ATTEMPTS = 5  # a row that failed this many runs is skipped until its attempts are reset
