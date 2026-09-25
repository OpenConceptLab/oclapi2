USER_OBJECT_TYPE = 'User'
VERIFICATION_TOKEN_MISMATCH = 'This link is invalid, possibly because it has already been used.' \
                              ' Please contact OCL Team.'
VERIFY_EMAIL_MESSAGE = 'A verification email has been sent to the address on record. Verify your email address to ' \
                       'activate your account.'
REACTIVATE_USER_MESSAGE = 'A verification email has been sent to the address on record. Verify your email address to ' \
                        're-activate your account.'
OCL_SERVERS_GROUP = 'ocl_servers'
OCL_FHIR_SERVERS_GROUP = 'ocl_fhir_servers'
HAPI_FHIR_SERVERS_GROUP = 'hapi_fhir_servers'
OPERATIONS_PANEL_GROUP = 'operations_panel'
MAPPER_AI_ASSISTANT_GROUP = 'mapper_ai_assistant'
MAPPER_WAITLIST_GROUP = 'mapper-waitlist'  # obsolete no effect
MAPPER_APPROVED_GROUP = 'mapper-approved'  # obsolete no effect
EARLY_ACCESS_NGO_GROUP = 'early_access_ngo'
GUEST_GROUP = 'guest_user'
STANDARD_GROUP = 'standard_user'
PREMIUM_GROUP = 'premium_user'
STAFF_GROUP = 'staff_user'
SUPERADMIN_GROUP = 'superadmin_user'
GRAPHQL_API_GROUP = 'graphql_api'
CORE_USER_GROUP = 'core_user'

MAPPER_USE_PERMISSION = 'users.mapper_use'
MAPPER_AI_ASSISTANT_PERMISSION = 'users.mapper_ai_assistant'
MAPPER_CUSTOM_ALGORITHMS_PERMISSION = 'users.mapper_custom_algorithms'
MAPPER_ORG_PROJECTS_PERMISSION = 'users.mapper_org_projects'
MAPPER_SCISPACY_PERMISSION = 'users.mapper_scispacy'
PREVIEW_GROUP = 'preview'
PREVIEW_GRANDFATHERED_GROUP = 'preview_grandfathered'  # existing accounts (ocl_online#230); layered on `preview`
BULK_IMPORT_ADVANCED_PERMISSION = 'users.bulk_import_advanced'
BULK_IMPORT_PRIORITY_PERMISSION = 'users.bulk_import_priority'
LIST_UNPAGINATED_PERMISSION = 'users.list_unpaginated'
