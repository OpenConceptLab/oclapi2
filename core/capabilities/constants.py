MAPPER_PROJECTS_CAPABILITY = 'mapper.projects'
MAPPER_ROWS_PER_PROJECT_CAPABILITY = 'mapper.rows_per_project'
MAPPER_MATCH_OPERATIONS_CAPABILITY = 'mapper.match_operations'
AI_ASSISTANT_CALLS_CAPABILITY = 'ai_assistant.calls'
AI_ASSISTANT_CHANGE_COMMENTS_CAPABILITY = 'ai_assistant.change_comments'
IMPORTS_FILE_SIZE_CAPABILITY = 'imports.file_size_kb'
CLONE_RESOURCES_PER_CALL_CAPABILITY = 'clone.resources_per_call'

MAPPER_PROJECTS_CAPABILITY_ID = 1
MAPPER_ROWS_PER_PROJECT_CAPABILITY_ID = 2
MAPPER_MATCH_OPERATIONS_CAPABILITY_ID = 3
AI_ASSISTANT_CALLS_CAPABILITY_ID = 4
AI_ASSISTANT_CHANGE_COMMENTS_CAPABILITY_ID = 5
IMPORTS_FILE_SIZE_CAPABILITY_ID = 6
CLONE_RESOURCES_PER_CALL_CAPABILITY_ID = 7

CAPABILITY_ID_BY_NAME = {
    MAPPER_PROJECTS_CAPABILITY: MAPPER_PROJECTS_CAPABILITY_ID,
    MAPPER_ROWS_PER_PROJECT_CAPABILITY: MAPPER_ROWS_PER_PROJECT_CAPABILITY_ID,
    MAPPER_MATCH_OPERATIONS_CAPABILITY: MAPPER_MATCH_OPERATIONS_CAPABILITY_ID,
    AI_ASSISTANT_CALLS_CAPABILITY: AI_ASSISTANT_CALLS_CAPABILITY_ID,
    AI_ASSISTANT_CHANGE_COMMENTS_CAPABILITY: AI_ASSISTANT_CHANGE_COMMENTS_CAPABILITY_ID,
    IMPORTS_FILE_SIZE_CAPABILITY: IMPORTS_FILE_SIZE_CAPABILITY_ID,
    CLONE_RESOURCES_PER_CALL_CAPABILITY: CLONE_RESOURCES_PER_CALL_CAPABILITY_ID,
}
CAPABILITY_NAME_BY_ID = {capability_id: name for name, capability_id in CAPABILITY_ID_BY_NAME.items()}

CAPABILITY_EXCEEDED_ERROR_CODE = {
    MAPPER_PROJECTS_CAPABILITY: 'mapper_projects_limit_reached',
    MAPPER_ROWS_PER_PROJECT_CAPABILITY: 'mapper_rows_per_project_limit_reached',
    MAPPER_MATCH_OPERATIONS_CAPABILITY: 'mapper_match_operations_limit_reached',
    AI_ASSISTANT_CALLS_CAPABILITY: 'ai_assistant_calls_limit_reached',
    AI_ASSISTANT_CHANGE_COMMENTS_CAPABILITY: 'ai_assistant_change_comments_limit_reached',
    IMPORTS_FILE_SIZE_CAPABILITY: 'imports_file_size_limit_reached',
    CLONE_RESOURCES_PER_CALL_CAPABILITY: 'clone_resources_per_call_limit_reached',
}

# A limit of None means no override and no group row for this user at all - they were
# never entitled to begin with, which is a different condition from having used up a
# real, configured allowance (CAPABILITY_EXCEEDED_ERROR_CODE above). Without this
# distinction every non-superuser with no capability configured (the default state for
# everyone until they're granted one) is told they've "reached their limit" for
# something they were never given any of.
CAPABILITY_NOT_ENTITLED_ERROR_CODE = {
    MAPPER_PROJECTS_CAPABILITY: 'mapper_projects_not_entitled',
    MAPPER_ROWS_PER_PROJECT_CAPABILITY: 'mapper_rows_per_project_not_entitled',
    MAPPER_MATCH_OPERATIONS_CAPABILITY: 'mapper_match_operations_not_entitled',
    AI_ASSISTANT_CALLS_CAPABILITY: 'ai_assistant_calls_not_entitled',
    AI_ASSISTANT_CHANGE_COMMENTS_CAPABILITY: 'ai_assistant_change_comments_not_entitled',
    IMPORTS_FILE_SIZE_CAPABILITY: 'imports_file_size_not_entitled',
    CLONE_RESOURCES_PER_CALL_CAPABILITY: 'clone_resources_per_call_not_entitled',
}

# Per-request limits on authoring features every account has always had (bulk import, $clone). A user with
# no row for one of these gets the `preview` group's value - the lowest tier - instead of being blocked
# (UserProfile.get_capability_limit). New paid features (the Mapper, AI) keep "no row = blocked".
AUTHORING_CAPABILITY_IDS = (IMPORTS_FILE_SIZE_CAPABILITY_ID, CLONE_RESOURCES_PER_CALL_CAPABILITY_ID)
# Last resort when the `preview` group has no row yet (e.g. oclapi2 deployed before groups.yaml): the lowest tier,
# never blocked. groups.yaml stays the source of truth; these only bridge a gap in configuration.
AUTHORING_CAPABILITY_DEFAULT_LIMITS = {IMPORTS_FILE_SIZE_CAPABILITY_ID: 500, CLONE_RESOURCES_PER_CALL_CAPABILITY_ID: 100}
