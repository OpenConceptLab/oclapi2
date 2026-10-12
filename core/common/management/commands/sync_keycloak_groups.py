from django.core.management import BaseCommand

from core.common.tasks import sync_groups_from_keycloak


class Command(BaseCommand):
    help = 'Syncs Keycloak group memberships into Django, including for users who only use API tokens.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        changed = sync_groups_from_keycloak(dry_run=dry_run)
        self.stdout.write(f"{changed} user(s) {'would change' if dry_run else 'changed'}")
