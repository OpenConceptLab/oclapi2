import json
from collections import Counter

from django.core.management import BaseCommand, CommandError

from core.common.constants import HEAD
from core.sources.models import Source

FIELDS = ('active_concepts', 'active_mappings')
# printed even when 0, so a dry run after --apply can show that nothing is left to change
ZERO_TOTALS = ('versions', 'unchanged', 'stale', 'repaired', 'uncounted', 'skipped_processing')


class Command(BaseCommand):
    help = ('Compares the stored active concept and mapping counts of source versions other than HEAD with their '
            'members, active and unretired, as the version lists count them. A dry run unless --apply is given, which '
            'recounts each stale version and clears its cached lists. Each stale version is reported as a JSON line.')

    def add_arguments(self, parser):
        parser.add_argument('uris', nargs='*', help='Source version URIs')
        parser.add_argument('--all', action='store_true', help='Every active source version but HEAD, by id')
        parser.add_argument('--apply', action='store_true', help='Recount the stale versions; without it, a dry run')
        parser.add_argument('--start-after-id', type=int, default=0, help='--all: resume after this version id')
        parser.add_argument('--limit', type=int, help='--all: stop after this many versions')

    def handle(self, *args, **options):
        if bool(options['uris']) == options['all']:
            raise CommandError('Pass source version URIs, or --all')
        self.options = options
        self.totals = Counter({key: 0 for key in ZERO_TOTALS})
        mode = 'APPLY' if options['apply'] else 'DRY RUN'
        self.stdout.write(mode)
        for version in self.get_versions():
            self.check_version(version)
        self.stdout.write(f'{mode} done: {json.dumps(dict(self.totals), sort_keys=True)}')

    def get_versions(self):
        versions = Source.objects.exclude(version=HEAD).filter(is_active=True)
        if self.options['all']:
            versions = versions.filter(id__gt=self.options['start_after_id']).order_by('id')
            return versions[:self.options['limit']] if self.options['limit'] else versions.iterator()
        found = []
        for uri in self.options['uris']:
            version = versions.filter(uri=uri).first()
            if not version:
                raise CommandError(f'No source version other than HEAD at {uri}')
            found.append(version)
        return found

    @staticmethod
    def get_actual_counts(version):
        return {
            'active_concepts': version.concepts.filter(retired=False, is_active=True).count(),
            'active_mappings': version.mappings.filter(retired=False, is_active=True).count(),
        }

    def check_version(self, version):
        self.totals['versions'] += 1
        if any(version._background_process_ids or []):  # pylint: disable=protected-access
            self.totals['skipped_processing'] += 1  # seeding or indexing, which counts it when done
            return
        stored = {field: getattr(version, field) for field in FIELDS}
        if all(count is None for count in stored.values()):  # its lists count through the join
            self.totals['uncounted'] += 1
            return
        actual = self.get_actual_counts(version)
        stale = [field for field in FIELDS if stored[field] is not None and stored[field] != actual[field]]
        if not stale:
            self.totals['unchanged'] += 1
            return
        self.totals['stale'] += 1
        self.stdout.write(json.dumps({
            'id': version.id, 'uri': version.uri, 'released': version.released,
            **{field: {'stored': stored[field], 'members': actual[field]} for field in stale}}, sort_keys=True))
        if self.options['apply']:
            for field in stale:
                getattr(version, f'update_{field.replace("active_", "")}_count')(sync=True)
            self.totals['repaired'] += 1
