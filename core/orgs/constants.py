ORG_OBJECT_TYPE = 'Organization'

DELETE_SUCCESS = 'Successfully deleted Organization.'
DELETE_ACCEPTED = 'Successfully accepted deleted Organization.'
NO_MEMBERS = 'noMembers'
CANNOT_REMOVE_ONLY_MEMBER = 'Cannot remove the only member of an organization.'
CANNOT_REMOVE_LAST_ADMIN = 'Cannot remove or demote the last admin of an organization.'

INVALID_ROLE = 'Must be admin or member.'

ROLE_ADMIN = 'admin'
ROLE_MEMBER = 'member'

ROLE_ADMIN_ID = 1
ROLE_MEMBER_ID = 2

ROLE_ID_BY_NAME = {
    ROLE_ADMIN: ROLE_ADMIN_ID,
    ROLE_MEMBER: ROLE_MEMBER_ID,
}
ROLES = tuple(ROLE_ID_BY_NAME)
