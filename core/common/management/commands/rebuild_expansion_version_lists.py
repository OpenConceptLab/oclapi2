import json
from collections import Counter

from django.core.management import BaseCommand, CommandError
from django.db import transaction

from core.collections.models import Collection, Expansion
from core.collections.version_lists import RULE, ExpansionVersionLists


class Command(BaseCommand):
    help = ('Rebuilds the explicit and evaluated repo version lists of expansions from their references and members. '
            'A dry run unless --apply is given. Members are never changed. Each changed expansion is reported as a '
            'JSON line with its lists before and after, so the report doubles as a before and after copy.')

    def add_arguments(self, parser):
        parser.add_argument(
            'uris', nargs='*', help='Expansion URIs, or collection version URIs for their default expansion')
        parser.add_argument('--all', action='store_true', help='Every active expansion, by id')
        parser.add_argument('--apply', action='store_true', help='Write the rebuilt lists; without it, a dry run')
        parser.add_argument('--start-after-id', type=int, default=0, help='--all: resume after this expansion id')
        parser.add_argument('--limit', type=int, help='--all: stop after this many expansions')
        parser.add_argument('--output', help='Append each changed expansion\'s report, and each failure, as JSON lines')
        parser.add_argument('--verbose-unchanged', action='store_true', help='Also report unchanged expansions')

    def handle(self, *args, **options):
        if bool(options['uris']) == options['all']:
            raise CommandError('Pass expansion or collection version URIs, or --all')
        self.options = options
        self.totals = Counter()
        mode = 'APPLY' if options['apply'] else 'DRY RUN'
        self.stdout.write(f'{mode}. Rule: {RULE}')
        for expansion_id in self.get_expansion_ids():
            self.rebuild(expansion_id)
        self.stdout.write(f'{mode} done: {json.dumps(dict(self.totals), sort_keys=True)}')
        if options['output']:
            self.stdout.write(f'Full report: {options["output"]}')

    def get_expansion_ids(self):
        if self.options['all']:
            ids = Expansion.objects.filter(
                is_active=True, collection_version__is_active=True, id__gt=self.options['start_after_id']
            ).order_by('id').values_list('id', flat=True)
            return list(ids[:self.options['limit']] if self.options['limit'] else ids)
        return [self.get_expansion(uri).id for uri in self.options['uris']]

    @staticmethod
    def get_expansion(uri):
        expansion = Expansion.objects.filter(uri=uri).first()
        if not expansion:
            version = Collection.objects.filter(uri=uri).first()
            expansion = version.expansion if version else None
        if not expansion:
            raise CommandError(f'No expansion for {uri}')
        return expansion

    def rebuild(self, expansion_id):
        try:
            with transaction.atomic():
                # a dry run takes no lock
                expansions = Expansion.objects.select_for_update() if self.options['apply'] else Expansion.objects
                expansion = expansions.get(id=expansion_id)
                lists = ExpansionVersionLists(expansion).build()
                report = lists.to_report()
                if self.options['apply'] and report['changed']:
                    lists.apply()
        except Exception as ex:  # pylint: disable=broad-except
            self.totals['failed'] += 1
            self.write({'id': expansion_id, 'error': f'{type(ex).__name__}: {ex}'})
            return
        self.count(report)
        if report['changed'] or self.options['verbose_unchanged']:
            self.write(report)

    def count(self, report):
        self.totals['expansions'] += 1
        self.totals['changed' if report['changed'] else 'unchanged'] += 1
        for key in ('gains_lock', 'loses_lock', 'in_both'):
            if report[key]:
                self.totals[f'with_{key}'] += 1
        kept = [note for note in report['references'] if 'reason' in note]
        if kept:
            self.totals['with_references_kept_as_recorded'] += 1
        if any(len(note.get('holders', [])) > 1 for note in report['references']):
            self.totals['with_members_in_several_versions'] += 1

    def write(self, report):
        if 'error' in report:
            self.stdout.write(f'FAILED {report["id"]}: {report["error"]}')
        else:
            summary = {relation: f'+{len(change["added"])} -{len(change["removed"])}'
                       for relation, change in report['changes'].items()}
            flags = [key for key in ('gains_lock', 'loses_lock', 'in_both') if report[key]]
            self.stdout.write(f'{report["expansion"]}: {json.dumps(summary)} {" ".join(flags)}'.rstrip())
        if self.options['output']:
            with open(self.options['output'], 'a', encoding='utf-8') as file:
                file.write(json.dumps(report, default=str) + '\n')
