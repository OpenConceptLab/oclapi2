from django.contrib.auth.models import Group

from core.capabilities.constants import MAPPER_PROJECTS_CAPABILITY_ID
from core.capabilities.exceptions import CapabilityExceeded
from core.capabilities.models import GroupCapability, UserCapabilityOverride
from core.common.tests import OCLAPITestCase
from core.users.constants import PREVIEW_GROUP_NAME
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
            self.url + 'mapper.projects/', data={'limit': -1}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.staff.get_token())
        self.assertEqual(response.status_code, 400)
        self.assertIn('limit', response.data)

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
        user.groups.add(Group.objects.get(name=PREVIEW_GROUP_NAME))  # mapper.projects limit=1
        UserCapabilityOverride.objects.create(
            user=user, capability_id=MAPPER_PROJECTS_CAPABILITY_ID, limit=0)

        # would exceed the preview group's real limit of 1 many times over
        for _ in range(10):
            user.check_and_consume_capability(MAPPER_PROJECTS_CAPABILITY_ID)

        self.assertEqual(user.get_capability_usage(MAPPER_PROJECTS_CAPABILITY_ID), 10)


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
        user = UserProfileFactory(is_staff=True)  # no groups, no override at all

        self.assertEqual(user.get_capability_limit(MAPPER_PROJECTS_CAPABILITY_ID), 0)
        user.check_and_consume_capability(MAPPER_PROJECTS_CAPABILITY_ID)
        self.assertEqual(user.get_capability_usage(MAPPER_PROJECTS_CAPABILITY_ID), 1)

    def test_superusers_are_unconditionally_unlimited_even_with_no_config(self):
        user = UserProfileFactory(is_superuser=True)  # no groups, no override at all

        self.assertEqual(user.get_capability_limit(MAPPER_PROJECTS_CAPABILITY_ID), 0)
        user.check_and_consume_capability(MAPPER_PROJECTS_CAPABILITY_ID)
        self.assertEqual(user.get_capability_usage(MAPPER_PROJECTS_CAPABILITY_ID), 1)
