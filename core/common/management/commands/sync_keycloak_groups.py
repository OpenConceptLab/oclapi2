from django.core.management import BaseCommand

from core.common.tasks import sync_groups_from_keycloak


class Command(BaseCommand):
    help = 'Syncs Keycloak group memberships into Django, including for users who only use API tokens.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        summary = sync_groups_from_keycloak(dry_run=dry_run)
        self.stdout.write(f"{summary['changed']} user(s) {'would change' if dry_run else 'changed'}")
        for name in sorted(set(summary['gained']) | set(summary['lost'])):
            self.stdout.write(f"  {name}: +{summary['gained'].get(name, 0)} -{summary['lost'].get(name, 0)}")
        for change in summary['access_changes']:
            self.stdout.write(f'  access change: {change}')
