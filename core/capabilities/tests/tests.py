import json
from datetime import datetime, timezone as dt_timezone
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command, CommandError
from django.test import override_settings

from core.capabilities.constants import (
    AI_ASSISTANT_CALLS_CAPABILITY_ID, AI_ASSISTANT_CHANGE_COMMENTS_CAPABILITY_ID,
    CLONE_RESOURCES_PER_CALL_CAPABILITY_ID, IMPORTS_FILE_SIZE_CAPABILITY_ID,
    MAPPER_MATCH_OPERATIONS_CAPABILITY_ID, MAPPER_PROJECTS_CAPABILITY_ID,
)
from core.capabilities.exceptions import CapabilityExceeded, UsageRefused
from core.capabilities.models import Capability, GroupCapability, UsageCounter, UsageEvent, UserCapabilityOverride
from core.capabilities.policy import CapabilityPolicy, GroupLimitPolicy
from core.common.tests import OCLAPITestCase, PREVIEW_GROUP_NAME
from core.common.utils import get_capability_policy
from core.users.constants import PREVIEW_GROUP, PREVIEW_GRANDFATHERED_GROUP
from core.users.tests.factories import UserProfileFactory


class UserCapabilityOverrideViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.target = UserProfileFactory()
        self.staff = UserProfileFactory(is_staff=True)
        self.non_staff = UserProfileFactory()
        self.url = f'/users/{self.target.username}/capabilities/overrides/'

    def test_list_requires_staff(self):
        response = self.client.get(self.url, HTTP_AUTHORIZATION='Token ' + self.non_staff.get_token())
        self.assertEqual(response.status_code, 403)

    def test_list_empty_by_default(self):
        response = self.client.get(self.url, HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_list_unknown_user_404(self):
        response = self.client.get(
            '/users/no-such-user/capabilities/overrides/', HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(response.status_code, 404)

    def test_get_unknown_capability_404(self):
        response = self.client.get(
            self.url + 'totally.bogus/', HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(response.status_code, 404)

    def test_get_before_created_404(self):
        response = self.client.get(
            self.url + 'mapper.projects/', HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(response.status_code, 404)

    def test_put_unknown_capability_404(self):
        response = self.client.put(
            self.url + 'totally.bogus/', data={'limit': 5}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(response.status_code, 404)

    def test_put_requires_staff(self):
        response = self.client.put(
            self.url + 'mapper.projects/', data={'limit': 5}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.non_staff.get_token())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(UserCapabilityOverride.objects.filter(user=self.target).exists())

    def test_put_creates_then_updates(self):
        create_response = self.client.put(
            self.url + 'mapper.projects/', data={'limit': 5}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(create_response.status_code, 200)
        self.assertEqual(
            create_response.data,
            {'capability': {'name': 'mapper.projects', 'limit': 5, 'used': 0}, 'limit': 5})
        self.assertEqual(self.target.get_capability_limit(MAPPER_PROJECTS_CAPABILITY_ID), 5)

        update_response = self.client.put(
            self.url + 'mapper.projects/', data={'limit': 9}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(
            update_response.data,
            {'capability': {'name': 'mapper.projects', 'limit': 9, 'used': 0}, 'limit': 9})
        self.assertEqual(self.target.get_capability_limit(MAPPER_PROJECTS_CAPABILITY_ID), 9)
        # upsert, not a duplicate row
        self.assertEqual(UserCapabilityOverride.objects.filter(user=self.target).count(), 1)

    def test_put_zero_limit_means_unlimited(self):
        response = self.client.put(
            self.url + 'mapper.projects/', data={'limit': 0}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(response.status_code, 200)
        # 0 is returned as-is, on both the raw override field and the resolved capability
        # limit - it is not translated to null, so staff can see at a glance that this
        # user was deliberately granted unlimited access, not just left unconfigured.
        self.assertEqual(
            response.data,
            {'capability': {'name': 'mapper.projects', 'limit': 0, 'used': 0}, 'limit': 0})
        self.assertEqual(self.target.get_capability_limit(MAPPER_PROJECTS_CAPABILITY_ID), 0)

    def test_put_rejects_negative_limit(self):
        response = self.client.put(
            self.url + 'mapper.projects/', data={'limit': -2}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(response.status_code, 400)
        self.assertIn('limit', response.data)

    def test_put_minus_one_blocks_the_user(self):
        response = self.client.put(
            self.url + 'mapper.match_operations/', data={'limit': -1}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.target.get_capability_limit(MAPPER_MATCH_OPERATIONS_CAPABILITY_ID), -1)
        with self.assertRaises(CapabilityExceeded) as ctx:
            self.target.check_and_consume_capability(MAPPER_MATCH_OPERATIONS_CAPABILITY_ID)
        self.assertTrue(ctx.exception.not_entitled)

    def test_list_reflects_created_overrides(self):
        self.client.put(
            self.url + 'mapper.projects/', data={'limit': 5}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.staff.get_token())

        response = self.client.get(self.url, HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data, [{'capability': {'name': 'mapper.projects', 'limit': 5, 'used': 0}, 'limit': 5}])

    def test_delete_removes_override_and_falls_back_to_group_limit(self):
        UserCapabilityOverride.objects.create(
            user=self.target, capability_id=MAPPER_PROJECTS_CAPABILITY_ID, limit=5)

        response = self.client.delete(
            self.url + 'mapper.projects/', HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(response.status_code, 204)
        self.assertFalse(UserCapabilityOverride.objects.filter(user=self.target).exists())

    def test_delete_requires_staff(self):
        UserCapabilityOverride.objects.create(
            user=self.target, capability_id=MAPPER_PROJECTS_CAPABILITY_ID, limit=5)

        response = self.client.delete(
            self.url + 'mapper.projects/', HTTP_AUTHORIZATION='Token ' + self.non_staff.get_token())
        self.assertEqual(response.status_code, 403)
        self.assertTrue(UserCapabilityOverride.objects.filter(user=self.target).exists())

    def test_delete_nonexistent_override_404(self):
        response = self.client.delete(
            self.url + 'mapper.projects/', HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(response.status_code, 404)

    def test_get_returns_existing_override(self):
        UserCapabilityOverride.objects.create(
            user=self.target, capability_id=MAPPER_PROJECTS_CAPABILITY_ID, limit=7)

        response = self.client.get(
            self.url + 'mapper.projects/', HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data, {'capability': {'name': 'mapper.projects', 'limit': 7, 'used': 0}, 'limit': 7})

    def test_override_does_not_leak_across_users(self):
        other_user = UserProfileFactory()
        UserCapabilityOverride.objects.create(
            user=other_user, capability_id=MAPPER_PROJECTS_CAPABILITY_ID, limit=99)

        response = self.client.get(self.url, HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])


class SelfUserCapabilityOverrideViewTest(OCLAPITestCase):
    """/user/capabilities/overrides/... - the same views, self-scoped via user_is_self (user_urls.py)."""
    def setUp(self):
        super().setUp()
        self.staff = UserProfileFactory(is_staff=True)
        self.non_staff = UserProfileFactory()
        self.url = '/user/capabilities/overrides/'

    def test_requires_staff(self):
        response = self.client.get(self.url, HTTP_AUTHORIZATION='Token ' + self.non_staff.get_token())
        self.assertEqual(response.status_code, 403)

    def test_staff_can_crud_their_own_override(self):
        list_response = self.client.get(self.url, HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data, [])

        put_response = self.client.put(
            self.url + 'mapper.projects/', data={'limit': 3}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(put_response.status_code, 200)
        self.assertEqual(
            put_response.data,
            {'capability': {'name': 'mapper.projects', 'limit': 0, 'used': 0}, 'limit': 3})
        self.assertEqual(self.staff.get_capability_limit(MAPPER_PROJECTS_CAPABILITY_ID), 0)

        delete_response = self.client.delete(
            self.url + 'mapper.projects/', HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(delete_response.status_code, 204)
        self.assertFalse(UserCapabilityOverride.objects.filter(user=self.staff).exists())


class GroupCapabilityZeroMeansUnlimitedTest(OCLAPITestCase):
    """ocl_issues#2781: a GroupCapability row of limit=0 must win as unlimited over a
    numeric limit from another of the user's groups - max() alone would pick the numeric
    one instead, since 0 < 1."""

    def test_zero_group_limit_wins_over_other_groups_numeric_limit(self):
        user = UserProfileFactory()
        preview_group = Group.objects.get(name=PREVIEW_GROUP_NAME)  # mapper.projects limit=1
        unlimited_group = Group.objects.create(name='mapper-approved-test')
        GroupCapability.objects.create(
            group=unlimited_group, capability_id=MAPPER_PROJECTS_CAPABILITY_ID, limit=0)
        user.groups.add(preview_group, unlimited_group)

        self.assertEqual(user.get_capability_limit(MAPPER_PROJECTS_CAPABILITY_ID), 0)

    def test_nonzero_group_limits_still_take_the_max(self):
        user = UserProfileFactory()
        preview_group = Group.objects.get(name=PREVIEW_GROUP_NAME)  # mapper.projects limit=1
        higher_group = Group.objects.create(name='higher-limit-test')
        GroupCapability.objects.create(
            group=higher_group, capability_id=MAPPER_PROJECTS_CAPABILITY_ID, limit=5)
        user.groups.add(preview_group, higher_group)

        self.assertEqual(user.get_capability_limit(MAPPER_PROJECTS_CAPABILITY_ID), 5)


class CheckAndConsumeCapabilityZeroLimitTest(OCLAPITestCase):
    """
    ocl_issues#2781 regression guard: get_capability_limit() returns a raw 0 (not None)
    for an explicit unlimited grant, so every enforcement call site must treat exactly 0
    (never a missing config) as uncapped - a naive `is not None`/`is None` check would
    block a "grandfathered unlimited" user on their very first unit of usage instead of
    freeing them.
    """

    def test_zero_override_never_raises_regardless_of_usage(self):
        user = UserProfileFactory()
        # mapper.match_operations (not mapper.projects): get_capability_usage() special-cases
        # mapper.projects to report the live map_projects_used count (see UserProfile.
        # get_capability_usage), which stays 0 here since no MapProject rows are created -
        # this test is about the UsageCounter ledger the other capabilities still use.
        user.groups.add(Group.objects.get(name=PREVIEW_GROUP_NAME))  # mapper.match_operations limit=100
        UserCapabilityOverride.objects.create(
            user=user, capability_id=MAPPER_MATCH_OPERATIONS_CAPABILITY_ID, limit=0)

        # would exceed the preview group's real limit of 100 many times over
        for _ in range(150):
            user.check_and_consume_capability(MAPPER_MATCH_OPERATIONS_CAPABILITY_ID)

        self.assertEqual(user.get_capability_usage(MAPPER_MATCH_OPERATIONS_CAPABILITY_ID), 150)


class UnconfiguredCapabilityIsBlockedTest(OCLAPITestCase):
    """
    A capability with no per-user override and no group row for this user at all must be
    BLOCKED, not silently unlimited - the opposite of the previous (buggy) behavior. This
    is what makes it safe to add a new capability, or a new Mapper-granting group, without
    it being wide open to everyone by default until someone remembers to configure a limit.
    """

    def test_capability_with_no_config_anywhere_blocks_on_first_unit(self):
        user = UserProfileFactory()  # no groups, no override at all

        self.assertIsNone(user.get_capability_limit(MAPPER_PROJECTS_CAPABILITY_ID))
        with self.assertRaises(CapabilityExceeded):
            user.check_and_consume_capability(MAPPER_PROJECTS_CAPABILITY_ID)
        self.assertEqual(user.get_capability_usage(MAPPER_PROJECTS_CAPABILITY_ID), 0)

    def test_staff_are_unconditionally_unlimited_even_with_no_config(self):
        # mapper.match_operations (not mapper.projects): get_capability_usage() special-cases
        # mapper.projects to report the live map_projects_used count, which this doesn't
        # exercise - see UserProfile.get_capability_usage.
        user = UserProfileFactory(is_staff=True)  # no groups, no override at all

        self.assertEqual(user.get_capability_limit(MAPPER_MATCH_OPERATIONS_CAPABILITY_ID), 0)
        user.check_and_consume_capability(MAPPER_MATCH_OPERATIONS_CAPABILITY_ID)
        self.assertEqual(user.get_capability_usage(MAPPER_MATCH_OPERATIONS_CAPABILITY_ID), 1)

    def test_superusers_are_unconditionally_unlimited_even_with_no_config(self):
        user = UserProfileFactory(is_superuser=True)  # no groups, no override at all

        self.assertEqual(user.get_capability_limit(MAPPER_MATCH_OPERATIONS_CAPABILITY_ID), 0)
        user.check_and_consume_capability(MAPPER_MATCH_OPERATIONS_CAPABILITY_ID)
        self.assertEqual(user.get_capability_usage(MAPPER_MATCH_OPERATIONS_CAPABILITY_ID), 1)


class CapabilityConsumeViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        UserCapabilityOverride.objects.create(
            user=self.user, capability_id=MAPPER_MATCH_OPERATIONS_CAPABILITY_ID, limit=2)

    def consume(self, **data):
        return self.client.post(
            '/capabilities/consume/', data={'capability': 'mapper.match_operations', **data}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token())

    def test_same_idempotency_key_charges_once(self):
        first = self.consume(idempotency_key='run-1:row-1')
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.data['already_consumed'])
        self.assertEqual(first.data['used'], 1)

        retry = self.consume(idempotency_key='run-1:row-1')
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry.data['already_consumed'])
        self.assertEqual(retry.data['usage_event_id'], first.data['usage_event_id'])
        self.assertEqual(retry.data['used'], 1)
        self.assertEqual(UsageEvent.objects.filter(user=self.user).count(), 1)

    def test_retry_after_last_unit_still_succeeds(self):
        self.assertEqual(self.consume(idempotency_key='a').status_code, 200)
        self.assertEqual(self.consume(idempotency_key='b').status_code, 200)

        self.assertEqual(self.consume(idempotency_key='c').status_code, 403)
        retry = self.consume(idempotency_key='b')
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry.data['already_consumed'])
        self.assertEqual(retry.data['used'], 2)

    def test_without_key_every_call_charges(self):
        self.assertEqual(self.consume().status_code, 200)
        response = self.consume()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['used'], 2)

    def test_invalid_idempotency_key_400(self):
        self.assertEqual(self.consume(idempotency_key='x' * 256).status_code, 400)
        self.assertEqual(self.consume(idempotency_key=5).status_code, 400)

    def test_blocked_reports_not_available(self):
        UserCapabilityOverride.objects.filter(user=self.user).update(limit=-1)

        response = self.consume()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['error_code'], 'mapper_match_operations_not_entitled')
        self.assertIsNone(response.data['limit'])


class AIAssistantChangeCommentsCapabilityTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.user.groups.add(Group.objects.get(name=PREVIEW_GROUP_NAME))

    def consume(self):
        return self.client.post(
            '/capabilities/consume/', data={'capability': 'ai_assistant.change_comments'}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token())

    def test_has_its_own_allowance(self):
        response = self.consume()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['limit'], 100)
        self.assertEqual(response.data['used'], 1)
        self.assertEqual(self.user.get_capability_usage(AI_ASSISTANT_CALLS_CAPABILITY_ID), 0)

    def test_limit_reached_reports_its_own_code(self):
        UserCapabilityOverride.objects.create(
            user=self.user, capability_id=AI_ASSISTANT_CHANGE_COMMENTS_CAPABILITY_ID, limit=1)
        self.assertEqual(self.consume().status_code, 200)

        response = self.consume()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['error_code'], 'ai_assistant_change_comments_limit_reached')


class SeedGroupCapabilitiesTest(OCLAPITestCase):
    CONFIG = {
        'groups': {
            'pro': {
                'permissions': ['mapper_use', 'mapper_custom_algorithms', 'mapper_scispacy'],
                'capabilities': {'mapper.projects': 20, 'mapper.match_operations': 0},
            }
        }
    }

    @staticmethod
    def seed(config):
        out = StringIO()
        with patch.dict('os.environ', {'OCL_GROUPS_CONFIG': json.dumps(config)}):
            call_command('seed_group_capabilities', stdout=out)
        return out.getvalue()

    @staticmethod
    def limits(group_name):
        return dict(GroupCapability.objects.filter(group__name=group_name).values_list('capability__name', 'limit'))

    @staticmethod
    def codenames(group_name):
        return set(Group.objects.get(name=group_name).permissions.values_list('codename', flat=True))

    def test_no_config_is_a_noop(self):
        group_count = Group.objects.count()
        out = StringIO()
        with patch.dict('os.environ', {}, clear=False) as env:
            env.pop('OCL_GROUPS_CONFIG', None)
            call_command('seed_group_capabilities', stdout=out)

        self.assertEqual(Group.objects.count(), group_count)
        self.assertIn('nothing to seed', out.getvalue())

    def test_creates_group_with_permissions_and_limits(self):
        self.seed(self.CONFIG)

        self.assertEqual(self.codenames('pro'), {'mapper_use', 'mapper_custom_algorithms', 'mapper_scispacy'})
        self.assertEqual(self.limits('pro'), {'mapper.projects': 20, 'mapper.match_operations': 0})

    def test_seeds_and_syncs_the_capacity_tier(self):
        from core.capacity.models import GroupCapacityTier
        config = {'groups': {'group_a': {'capacity_tier': 'early_access'}, 'group_b': {}}}
        self.seed(config)
        self.assertEqual(GroupCapacityTier.objects.get(group__name='group_a').tier, 'early_access')
        self.assertFalse(GroupCapacityTier.objects.filter(group__name='group_b').exists())

        self.seed({'groups': {'group_a': {'capacity_tier': 'core'}}})
        self.assertEqual(GroupCapacityTier.objects.get(group__name='group_a').tier, 'core')

        self.seed({'groups': {'group_a': {'permissions': ['mapper_use']}}})
        self.assertFalse(GroupCapacityTier.objects.filter(group__name='group_a').exists())

    def test_rejects_an_unknown_capacity_tier(self):
        with self.assertRaisesMessage(CommandError, 'group_a: capacity_tier must be one of'):
            self.seed({'groups': {'group_a': {'capacity_tier': 'gold'}}})

    def test_reseed_syncs_permissions_and_limits(self):
        self.seed(self.CONFIG)
        self.seed({
            'groups': {
                'pro': {
                    'permissions': ['mapper_use', 'mapper_ai_assistant'],
                    'capabilities': {'mapper.projects': -1, 'ai_assistant.calls': 50},
                }
            }
        })

        self.assertEqual(self.codenames('pro'), {'mapper_use', 'mapper_ai_assistant'})
        self.assertEqual(self.limits('pro'), {'mapper.projects': -1, 'ai_assistant.calls': 50})

    def test_grants_permissions_to_existing_group(self):
        self.assertFalse(self.codenames('core_user'))

        self.seed({'groups': {'core_user': {'permissions': ['mapper_use'], 'capabilities': {'mapper.projects': 0}}}})

        self.assertEqual(self.codenames('core_user'), {'mapper_use'})
        self.assertEqual(self.limits('core_user'), {'mapper.projects': 0})

    def test_unknown_permission_and_capability_are_skipped(self):
        out = self.seed({
            'groups': {'pro': {'permissions': ['mapper_use', 'bogus'], 'capabilities': {'bogus.cap': 1}}}
        })

        self.assertEqual(self.codenames('pro'), {'mapper_use'})
        self.assertEqual(self.limits('pro'), {})
        self.assertIn('bogus', out)
        self.assertIn('bogus.cap', out)

    def test_invalid_config_raises(self):
        with patch.dict('os.environ', {'OCL_GROUPS_CONFIG': '{not json'}):
            with self.assertRaises(CommandError):
                call_command('seed_group_capabilities', stdout=StringIO())

        with self.assertRaises(CommandError):
            self.seed({'groups': {'pro': {'capabilities': {'mapper.projects': 'ten'}}}})

        with self.assertRaises(CommandError):
            self.seed({'pro': {}})


class LaunchGuardrailConfigTest(OCLAPITestCase):
    def test_capabilities_registered(self):
        self.assertEqual(Capability.objects.get(id=IMPORTS_FILE_SIZE_CAPABILITY_ID).name, 'imports.file_size_kb')
        self.assertEqual(
            Capability.objects.get(id=CLONE_RESOURCES_PER_CALL_CAPABILITY_ID).name, 'clone.resources_per_call')

    def test_permissions_registered(self):
        for codename in ('bulk_import_advanced', 'bulk_import_priority', 'list_unpaginated'):
            self.assertTrue(
                Permission.objects.filter(codename=codename, content_type__app_label='users').exists(), codename)

    def test_seeded_group_limits_and_permissions(self):
        preview = Group.objects.get(name=PREVIEW_GROUP)
        grandfathered = Group.objects.get(name=PREVIEW_GRANDFATHERED_GROUP)

        def limit(group, capability_id):
            return GroupCapability.objects.get(group=group, capability_id=capability_id).limit

        self.assertEqual(limit(preview, IMPORTS_FILE_SIZE_CAPABILITY_ID), 500)
        self.assertEqual(limit(preview, CLONE_RESOURCES_PER_CALL_CAPABILITY_ID), 100)
        self.assertEqual(limit(grandfathered, IMPORTS_FILE_SIZE_CAPABILITY_ID), 51200)
        self.assertEqual(limit(grandfathered, CLONE_RESOURCES_PER_CALL_CAPABILITY_ID), 1000)
        self.assertEqual(
            list(grandfathered.permissions.values_list('codename', flat=True)), ['bulk_import_advanced'])
        self.assertFalse(preview.permissions.filter(codename='bulk_import_advanced').exists())
        self.assertFalse(
            GroupCapability.objects.filter(group=grandfathered, capability__name__startswith='mapper.').exists())


class AuthoringCapabilityDefaultTest(OCLAPITestCase):
    def test_user_without_group_gets_preview_values(self):
        user = UserProfileFactory()
        self.assertEqual(user.get_capability_limit(IMPORTS_FILE_SIZE_CAPABILITY_ID), 500)
        self.assertEqual(user.get_capability_limit(CLONE_RESOURCES_PER_CALL_CAPABILITY_ID), 100)
        self.assertIsNone(user.get_capability_limit(MAPPER_PROJECTS_CAPABILITY_ID))  # Mapper keeps "no row = blocked"

    def test_grandfathered_gets_higher_authoring_values_and_preview_mapper_values(self):
        user = UserProfileFactory()
        user.groups.add(Group.objects.get(name=PREVIEW_GROUP), Group.objects.get(name=PREVIEW_GRANDFATHERED_GROUP))
        self.assertEqual(user.get_capability_limit(IMPORTS_FILE_SIZE_CAPABILITY_ID), 51200)
        self.assertEqual(user.get_capability_limit(CLONE_RESOURCES_PER_CALL_CAPABILITY_ID), 1000)
        self.assertEqual(user.get_capability_limit(MAPPER_PROJECTS_CAPABILITY_ID), 1)

    def test_override_and_staff(self):
        user = UserProfileFactory()
        UserCapabilityOverride.objects.create(
            user=user, capability_id=CLONE_RESOURCES_PER_CALL_CAPABILITY_ID, limit=-1)
        self.assertEqual(user.get_capability_limit(CLONE_RESOURCES_PER_CALL_CAPABILITY_ID), -1)
        self.assertEqual(
            UserProfileFactory(is_staff=True).get_capability_limit(CLONE_RESOURCES_PER_CALL_CAPABILITY_ID), 0)

    def test_missing_preview_row_falls_back_to_built_in_default(self):
        GroupCapability.objects.filter(
            group__name=PREVIEW_GROUP,
            capability_id__in=[IMPORTS_FILE_SIZE_CAPABILITY_ID, CLONE_RESOURCES_PER_CALL_CAPABILITY_ID]).delete()
        user = UserProfileFactory()
        self.assertEqual(user.get_capability_limit(IMPORTS_FILE_SIZE_CAPABILITY_ID), 500)
        self.assertEqual(user.get_capability_limit(CLONE_RESOURCES_PER_CALL_CAPABILITY_ID), 100)

    def test_per_request_limits_report_no_usage(self):
        user = UserProfileFactory()
        self.assertIsNone(user.get_capability_usage(IMPORTS_FILE_SIZE_CAPABILITY_ID))
        self.assertIsNone(user.get_capability_usage(CLONE_RESOURCES_PER_CALL_CAPABILITY_ID))


class FakePolicy(CapabilityPolicy):
    """Records every call, and answers with values GroupLimitPolicy never gives."""
    calls = []
    refusal = None
    window_start = datetime(2030, 1, 1, tzinfo=dt_timezone.utc)

    def get_limit(self, user, capability_id):
        self.calls.append(('get_limit', user, capability_id))
        return 7

    def get_usage(self, user, capability_id):
        self.calls.append(('get_usage', user, capability_id))
        return {'used': 3, 'window': 'fake'}

    def get_window_start(self, user, capability_id):
        self.calls.append(('get_window_start', user, capability_id))
        return self.window_start

    def consume(self, user, capability_id, units=1, *, idempotency_key=None, **attribution):
        self.calls.append(('consume', user, capability_id, units, idempotency_key, attribution))
        if self.refusal:
            raise UsageRefused(dict(self.refusal), not_entitled=self.refusal.get('error_code') == 'fake_not_entitled')
        return UsageEvent.objects.create(user=user, capability_id=capability_id, units=units), False

    def reverse(self, usage_event, units):
        self.calls.append(('reverse', usage_event, units))
        return units


@override_settings(CAPABILITY_POLICY='core.capabilities.tests.tests.FakePolicy')
class CapabilityPolicyContractTest(OCLAPITestCase):
    """Every call site goes through CAPABILITY_POLICY, and sends what the policy reports."""

    def setUp(self):
        super().setUp()
        FakePolicy.calls = []
        FakePolicy.refusal = None
        self.user = UserProfileFactory()
        self.user.groups.add(Group.objects.get(name=PREVIEW_GROUP_NAME))  # mapper.use, for $match

    def calls(self, name):
        return [call for call in FakePolicy.calls if call[0] == name]

    def consume(self, **data):
        return self.client.post(
            '/capabilities/consume/', data={'capability': 'ai_assistant.calls', **data}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token())

    def match(self, rows=2):
        return self.client.post(
            '/concepts/$match/', {'rows': [{'name': 'Malaria'}] * rows, 'target_repo_url': '/orgs/OCL/sources/X/'},
            format='json', HTTP_AUTHORIZATION='Token ' + self.user.get_token())

    def test_user_profile_delegates(self):
        self.assertEqual(self.user.get_capability_limit(MAPPER_MATCH_OPERATIONS_CAPABILITY_ID), 7)
        self.assertEqual(self.user.get_capability_usage(MAPPER_MATCH_OPERATIONS_CAPABILITY_ID), 3)
        event, already_consumed = self.user.check_and_consume_capability(
            MAPPER_MATCH_OPERATIONS_CAPABILITY_ID, units=2, action='act', algorithm='alg', idempotency_key='key')

        self.assertFalse(already_consumed)
        self.assertEqual(event.units, 2)
        self.assertEqual(
            self.calls('consume'),
            [('consume', self.user, MAPPER_MATCH_OPERATIONS_CAPABILITY_ID, 2, 'key',
              {'action': 'act', 'algorithm': 'alg', 'map_project': None, 'run': None})])

    def test_user_capabilities_report_every_usage_field(self):
        response = self.client.get(
            '/user/?includeCapabilities=true', HTTP_AUTHORIZATION='Token ' + self.user.get_token())

        self.assertEqual(response.status_code, 200)
        capabilities_by_name = {c['name']: c for c in response.data['capabilities']}
        self.assertEqual(
            capabilities_by_name['ai_assistant.calls'],
            {'name': 'ai_assistant.calls', 'limit': 7, 'used': 3, 'window': 'fake'})

    def test_consume_reports_every_usage_field(self):
        response = self.consume(units=2, action='act', idempotency_key='key')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {key: response.data[key] for key in ['capability', 'limit', 'used', 'window', 'units', 'already_consumed']},
            {'capability': 'ai_assistant.calls', 'limit': 7, 'used': 3, 'window': 'fake', 'units': 2,
             'already_consumed': False})
        self.assertEqual(self.calls('consume')[0][3:5], (2, 'key'))

    def test_consume_refusal_is_sent_as_the_policy_built_it(self):
        FakePolicy.refusal = {'error_code': 'fake_limit_reached', 'limit': 5, 'used': 5, 'window': 'fake'}

        response = self.consume()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            dict(response.data),
            {'detail': 'ai_assistant.calls limit reached.', 'error_code': 'fake_limit_reached', 'limit': 5,
             'used': 5, 'window': 'fake'})

        FakePolicy.refusal = {'detail': 'From the policy.', 'error_code': 'fake_not_entitled'}

        response = self.consume()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(dict(response.data), {'detail': 'From the policy.', 'error_code': 'fake_not_entitled'})

    def test_match_refusal_is_sent_as_the_policy_built_it(self):
        FakePolicy.refusal = {'error_code': 'fake_not_entitled', 'limit': None, 'used': 0, 'window': 'fake'}

        response = self.match()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            dict(response.data),
            {'detail': 'You do not have Mapper match access.', 'error_code': 'fake_not_entitled', 'limit': None,
             'used': 0, 'window': 'fake'})

    def test_match_reverses_its_event_when_the_search_fails(self):
        from core.concepts.views import MetadataToConceptsListView
        with patch.object(MetadataToConceptsListView, 'filter_queryset', side_effect=ValueError('es down')):
            with self.assertRaises(ValueError):
                self.match(rows=3)

        consume, = self.calls('consume')
        self.assertEqual(consume[1:4], (self.user, MAPPER_MATCH_OPERATIONS_CAPABILITY_ID, 3))
        reverse, = self.calls('reverse')
        self.assertEqual((reverse[1].user, reverse[1].units, reverse[2]), (self.user, 3, 3))

    def test_match_that_succeeds_is_not_reversed(self):
        from core.concepts.views import MetadataToConceptsListView
        with patch.object(MetadataToConceptsListView, 'filter_queryset', return_value=[]):
            response = self.match()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.calls('consume')), 1)
        self.assertEqual(self.calls('reverse'), [])

    def test_staff_refund_corrects_the_policy_window(self):
        staff = UserProfileFactory(is_staff=True)
        UsageCounter.objects.create(
            user=self.user, capability_id=AI_ASSISTANT_CALLS_CAPABILITY_ID, window_start=FakePolicy.window_start,
            used=5)
        UsageCounter.objects.create(user=self.user, capability_id=AI_ASSISTANT_CALLS_CAPABILITY_ID, used=5)

        response = self.client.post(
            '/capabilities/refund/', data={'user': self.user.username, 'capability': 'ai_assistant.calls', 'units': 2},
            format='json', HTTP_AUTHORIZATION='Token ' + staff.get_token())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            dict(response.data),
            {'user': self.user.username, 'capability': 'ai_assistant.calls', 'limit': 7, 'used': 3, 'window': 'fake'})
        self.assertEqual(
            dict(UsageCounter.objects.filter(user=self.user).values_list('window_start', 'used')),
            {FakePolicy.window_start: 3, None: 5})


class WindowedPolicy(GroupLimitPolicy):
    window_start = None

    def get_window_start(self, user, capability_id):
        return self.window_start


@override_settings(CAPABILITY_POLICY='core.capabilities.tests.tests.WindowedPolicy')
class GroupLimitPolicyWindowTest(OCLAPITestCase):
    first_window = datetime(2030, 1, 1, tzinfo=dt_timezone.utc)
    second_window = datetime(2030, 2, 1, tzinfo=dt_timezone.utc)

    def setUp(self):
        super().setUp()
        WindowedPolicy.window_start = self.first_window
        self.user = UserProfileFactory()
        UserCapabilityOverride.objects.create(
            user=self.user, capability_id=MAPPER_MATCH_OPERATIONS_CAPABILITY_ID, limit=3)

    def tearDown(self):
        WindowedPolicy.window_start = None
        super().tearDown()

    def consume(self, units=1, idempotency_key=None):
        return self.user.check_and_consume_capability(
            MAPPER_MATCH_OPERATIONS_CAPABILITY_ID, units=units, idempotency_key=idempotency_key)

    def used_by_window(self):
        return dict(UsageCounter.objects.filter(user=self.user).values_list('window_start', 'used'))

    def test_each_window_counts_from_zero(self):
        event, _ = self.consume(units=3)
        with self.assertRaises(CapabilityExceeded):
            self.consume()

        WindowedPolicy.window_start = self.second_window
        self.consume()

        self.assertEqual(event.window_start, self.first_window)
        self.assertEqual(self.user.get_capability_usage(MAPPER_MATCH_OPERATIONS_CAPABILITY_ID), 1)
        self.assertEqual(self.used_by_window(), {self.first_window: 3, self.second_window: 1})

    def test_a_key_replays_in_a_later_window(self):
        event, _ = self.consume(units=2, idempotency_key='key')

        WindowedPolicy.window_start = self.second_window
        replay, already_consumed = self.consume(units=2, idempotency_key='key')

        self.assertTrue(already_consumed)
        self.assertEqual(replay.id, event.id)
        self.assertEqual(self.used_by_window(), {self.first_window: 2, self.second_window: 0})

    def test_reverse_lowers_the_window_the_units_counted_in(self):
        event, _ = self.consume(units=2)
        WindowedPolicy.window_start = self.second_window
        self.consume()

        self.assertEqual(WindowedPolicy().reverse(event, 2), 2)
        self.assertEqual(WindowedPolicy().reverse(event, 2), 0)

        event.refresh_from_db()
        self.assertEqual((event.units, event.reversed_units), (2, 2))
        self.assertEqual(self.used_by_window(), {self.first_window: 0, self.second_window: 1})

    def test_reverse_never_takes_back_more_than_the_event_or_the_counter(self):
        event, _ = self.consume(units=3)
        UsageCounter.refund(self.user, MAPPER_MATCH_OPERATIONS_CAPABILITY_ID, units=2, window_start=self.first_window)

        self.assertEqual(WindowedPolicy().reverse(event, 5), 3)

        event.refresh_from_db()
        self.assertEqual(event.reversed_units, 3)
        self.assertEqual(self.used_by_window(), {self.first_window: 0})


class GroupLimitPolicyDefaultWindowTest(OCLAPITestCase):
    def test_usage_counts_in_the_window_that_never_ends(self):
        user = UserProfileFactory()
        UserCapabilityOverride.objects.create(user=user, capability_id=MAPPER_MATCH_OPERATIONS_CAPABILITY_ID, limit=0)

        event, _ = user.check_and_consume_capability(MAPPER_MATCH_OPERATIONS_CAPABILITY_ID, units=2)

        self.assertIsNone(event.window_start)
        self.assertEqual(
            list(UsageCounter.objects.filter(user=user).values_list('window_start', 'used')), [(None, 2)])
        self.assertEqual(get_capability_policy().reverse(event, 2), 2)
        self.assertEqual(user.get_capability_usage(MAPPER_MATCH_OPERATIONS_CAPABILITY_ID), 0)
