from django.core.management import BaseCommand
from django.db.models import Count
from django.db.models.functions import Lower

from core.services.auth.openid import OpenIDAuthService
from core.users.models import UserProfile


class Command(BaseCommand):
    help = ('Links active accounts without an sso_id to the Keycloak user with exactly their username. A mixed-case '
            'account links to the Keycloak user with its lowercased username when no other account has that username '
            'in another case and the emails match. Every link and every skip is listed.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        keycloak_users = {user['username']: user for user in OpenIDAuthService.get_users()}
        linked = set(UserProfile.objects.filter(sso_id__isnull=False).values_list('sso_id', flat=True))
        case_twins = set(UserProfile.objects.order_by().values(lower=Lower('username')).annotate(
            count=Count('id')).filter(count__gt=1).values_list('lower', flat=True))
        links, skipped = [], []
        for user in UserProfile.objects.filter(is_active=True, sso_id__isnull=True).order_by('username'):
            kc_user, reason = self.match(user, keycloak_users, case_twins)
            if kc_user and kc_user['id'] in linked:
                kc_user, reason = None, 'its Keycloak user is linked to another account'
            if not kc_user:
                skipped.append(f'{user.username} ({reason})')
                continue
            linked.add(kc_user['id'])
            links.append(f"{user.username} -> {kc_user['username']}")
            if not options['dry_run']:
                UserProfile.objects.filter(id=user.id, sso_id__isnull=True).update(sso_id=kc_user['id'])
        verb = 'would be linked' if options['dry_run'] else 'linked'
        self.stdout.write(f'{len(links)} account(s) {verb}, {len(skipped)} skipped')
        for link in links:
            self.stdout.write(f'  {verb}: {link}')
        for username in skipped:
            self.stdout.write(f'  skipped: {username}')

    @staticmethod
    def match(user, keycloak_users, case_twins):
        if user.username in keycloak_users:
            return keycloak_users[user.username], None
        lower = user.username.lower()
        if lower == user.username or lower not in keycloak_users:
            return None, 'no Keycloak user has this username'
        if lower in case_twins:
            return None, 'another account has this username in another case'
        kc_user = keycloak_users[lower]
        email = (user.email or '').strip().lower()
        if not email or email != (kc_user.get('email') or '').strip().lower():
            return None, 'emails differ'
        return kc_user, None
