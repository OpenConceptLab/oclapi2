from django.core.management import BaseCommand

from core.capabilities.constants import (
    AI_ASSISTANT_CALLS_CAPABILITY, MAPPER_MATCH_OPERATIONS_CAPABILITY, MAPPER_PROJECTS_CAPABILITY,
    MAPPER_ROWS_PER_PROJECT_CAPABILITY,
)
from core.users.constants import PREVIEW_GROUP_NAME

# The preview group's starting caps (ocl_issues#2762/#2782). Seeded here via
# get_or_create rather than core/fixtures/capabilities.yaml: pre_startup.sh runs
# `loaddata core/fixtures/*` on every container start, and a loaddata fixture always
# overwrites these rows - so an admin's runtime cap tuning (via UserCapabilityOverride
# or the Django admin) would silently revert on the next restart. get_or_create here
# only sets these defaults the first time a (group, capability) pair has no row yet;
# it never touches one that already exists.
DEFAULT_PREVIEW_LIMITS = {
    MAPPER_PROJECTS_CAPABILITY: 1,
    MAPPER_ROWS_PER_PROJECT_CAPABILITY: 25,
    MAPPER_MATCH_OPERATIONS_CAPABILITY: 100,
    AI_ASSISTANT_CALLS_CAPABILITY: 6,
}


class Command(BaseCommand):
    help = 'seed the preview group\'s default capability limits, without overwriting any that already exist'

    def handle(self, *args, **options):
        from django.contrib.auth.models import Group

        from core.capabilities.models import Capability, GroupCapability

        preview_group = Group.objects.filter(name=PREVIEW_GROUP_NAME).first()
        if not preview_group:
            return

        for capability_name, limit in DEFAULT_PREVIEW_LIMITS.items():
            capability = Capability.objects.filter(name=capability_name).first()
            if not capability:
                continue
            GroupCapability.objects.get_or_create(
                group=preview_group, capability=capability, defaults={'limit': limit}
            )
