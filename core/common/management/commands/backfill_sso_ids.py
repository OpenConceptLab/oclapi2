from django.core.management import BaseCommand

from core.services.auth.openid import OpenIDAuthService
from core.users.models import UserProfile


class Command(BaseCommand):
    help = ('Links active accounts without an sso_id to the Keycloak user with exactly their username. '
            'Accounts with no exact match, or whose Keycloak user is already linked, are listed and left alone.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        keycloak_ids = {user['username']: user['id'] for user in OpenIDAuthService.get_users()}
        linked = set(UserProfile.objects.filter(sso_id__isnull=False).values_list('sso_id', flat=True))
        set_count, skipped = 0, []
        for user in UserProfile.objects.filter(is_active=True, sso_id__isnull=True):
            keycloak_id = keycloak_ids.get(user.username)
            if not keycloak_id or keycloak_id in linked:
                skipped.append(user.username)
                continue
            linked.add(keycloak_id)
            set_count += 1
            if not options['dry_run']:
                UserProfile.objects.filter(id=user.id).update(sso_id=keycloak_id)
        self.stdout.write(f"{set_count} account(s) {'would be linked' if options['dry_run'] else 'linked'}, "
                          f"{len(skipped)} skipped")
        for username in skipped:
            self.stdout.write(f'  skipped: {username}')
