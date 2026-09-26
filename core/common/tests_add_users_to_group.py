import tempfile
from io import StringIO

from django.core.management import call_command, CommandError

from core.common.tests import OCLTestCase
from core.users.tests.factories import UserProfileFactory


class AddUsersToGroupCommandTest(OCLTestCase):
    @staticmethod
    def write_usernames(text):
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as file:
            file.write(text)
        return file.name

    def test_adds_listed_users_and_reports_missing(self):
        alice = UserProfileFactory(username='alice')
        UserProfileFactory(username='bob')
        out = StringIO()
        call_command(
            'add_users_to_group', group='preview_grandfathered',
            usernames_file=self.write_usernames('alice\n# comment\nbob\nnobody\n'), stdout=out)
        self.assertTrue(alice.groups.filter(name='preview_grandfathered').exists())
        self.assertIn('2 added, 0 already members, 1 not found', out.getvalue())
        self.assertIn('not found: nobody', out.getvalue())

    def test_dry_run_changes_nothing(self):
        alice = UserProfileFactory(username='alice')
        call_command(
            'add_users_to_group', group='preview_grandfathered',
            usernames_file=self.write_usernames('alice\n'), dry_run=True, stdout=StringIO())
        self.assertFalse(alice.groups.filter(name='preview_grandfathered').exists())

    def test_unknown_group(self):
        with self.assertRaises(CommandError):
            call_command(
                'add_users_to_group', group='no-such-group',
                usernames_file=self.write_usernames('alice\n'), stdout=StringIO())
