from django.core.management import BaseCommand

from core.collections.models import Collection
from core.common.utils import get_export_key_pattern, get_export_service
from core.sources.models import Source


class Command(BaseCommand):
    help = ('Moves repo version exports stored under the old key format to the unique key format. An old prefix that '
            'more than one repo version builds is skipped and listed, unless --assign names the version that owns it.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--assign', action='append', default=[], metavar='OLD_PREFIX=VERSION_URI',
            help='Give the keys under a shared old prefix to one of the versions that builds it.')

    def handle(self, *args, **options):
        self.service = get_export_service()
        self.dry_run = options['dry_run']
        assigned = dict(item.split('=', 1) for item in options['assign'])
        shared = {}
        moved = 0
        for model in (Source, Collection):
            for version in model.objects.exclude(organization=None, user=None).iterator():
                legacy_prefix = version.get_legacy_version_export_path(suffix=None)
                keys = self.service.get_keys(legacy_prefix, get_export_key_pattern(legacy_prefix))
                if not keys:
                    continue
                if version.owns_legacy_export_path():
                    moved += self.move(version, legacy_prefix, keys)
                else:
                    shared.setdefault(legacy_prefix, []).append((version, keys))

        for legacy_prefix, candidates in shared.items():
            owner = next((version for version, _ in candidates if version.uri == assigned.get(legacy_prefix)), None)
            if owner:
                moved += self.move(owner, legacy_prefix, candidates[0][1])
            else:
                self.stdout.write(f'Shared, skipped: {legacy_prefix} ({", ".join(v.uri for v, _ in candidates)})')

        suffix = ' (dry run)' if self.dry_run else ''
        self.stdout.write(f'{moved} export keys moved{suffix}')

    def move(self, version, legacy_prefix, keys):
        prefix = version.get_version_export_path(suffix=None)
        for key in keys:
            target_key = prefix + key[len(legacy_prefix):]
            self.stdout.write(f'{key} -> {target_key}')
            if not self.dry_run:
                self.service.copy(key, target_key)
                self.service.remove(key)
        return len(keys)
