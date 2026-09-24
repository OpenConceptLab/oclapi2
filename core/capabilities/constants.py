MAPPER_PROJECTS_CAPABILITY = 'mapper.projects'
MAPPER_ROWS_PER_PROJECT_CAPABILITY = 'mapper.rows_per_project'
MAPPER_MATCH_OPERATIONS_CAPABILITY = 'mapper.match_operations'
AI_ASSISTANT_CALLS_CAPABILITY = 'ai_assistant.calls'

MAPPER_PROJECTS_CAPABILITY_ID = 1
MAPPER_ROWS_PER_PROJECT_CAPABILITY_ID = 2
MAPPER_MATCH_OPERATIONS_CAPABILITY_ID = 3
AI_ASSISTANT_CALLS_CAPABILITY_ID = 4

CAPABILITY_ID_BY_NAME = {
    MAPPER_PROJECTS_CAPABILITY: MAPPER_PROJECTS_CAPABILITY_ID,
    MAPPER_ROWS_PER_PROJECT_CAPABILITY: MAPPER_ROWS_PER_PROJECT_CAPABILITY_ID,
    MAPPER_MATCH_OPERATIONS_CAPABILITY: MAPPER_MATCH_OPERATIONS_CAPABILITY_ID,
    AI_ASSISTANT_CALLS_CAPABILITY: AI_ASSISTANT_CALLS_CAPABILITY_ID,
}
CAPABILITY_NAME_BY_ID = {capability_id: name for name, capability_id in CAPABILITY_ID_BY_NAME.items()}

CAPABILITY_EXCEEDED_ERROR_CODE = {
    MAPPER_PROJECTS_CAPABILITY: 'mapper_projects_limit_reached',
    MAPPER_ROWS_PER_PROJECT_CAPABILITY: 'mapper_rows_per_project_limit_reached',
    MAPPER_MATCH_OPERATIONS_CAPABILITY: 'mapper_match_operations_limit_reached',
    AI_ASSISTANT_CALLS_CAPABILITY: 'ai_assistant_calls_limit_reached',
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
}
