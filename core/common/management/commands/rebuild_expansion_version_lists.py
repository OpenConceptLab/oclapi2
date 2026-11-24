import json
from collections import Counter

from django.core.management import BaseCommand, CommandError
from django.db import transaction

from core.collections.models import Collection, Expansion
from core.collections.version_lists import RULE, ExpansionVersionLists


# printed even when 0, so a dry run after --apply can show that nothing is left to change
ZERO_TOTALS = (
    'expansions', 'changed', 'unchanged', 'changed_released', 'changed_unreleased', 'unsettled',
    'settled_after_several_passes', 'skipped_unsettled', 'failed', 'with_gains_lock')


class Command(BaseCommand):
    help = ('Rebuilds the explicit and evaluated repo version lists of expansions from their references and members. '
            'A dry run unless --apply is given. Members are never changed. Each changed expansion is reported as a '
            'JSON line with its lists before and after, so the report doubles as a before and after copy.')

    def add_arguments(self, parser):
        parser.add_argument(
            'uris', nargs='*', help='Expansion URIs, or collection version URIs for their default expansion')
        parser.add_argument('--all', action='store_true', help='Every active expansion, by id')
        parser.add_argument('--apply', action='store_true', help='Write the rebuilt lists; without it, a dry run')
        parser.add_argument(
            '--skip-released', action='store_true',
            help='Leave out expansions of released collection versions, whose lists FHIR reports releases from')
        parser.add_argument('--start-after-id', type=int, default=0, help='--all: resume after this expansion id')
        parser.add_argument('--limit', type=int, help='--all: stop after this many expansions')
        parser.add_argument('--output', help='Append each changed expansion\'s report, and each failure, as JSON lines')
        parser.add_argument('--verbose-unchanged', action='store_true', help='Also report unchanged expansions')

    def handle(self, *args, **options):
        if bool(options['uris']) == options['all']:
            raise CommandError('Pass expansion or collection version URIs, or --all')
        self.options = options
        self.totals = Counter({key: 0 for key in ZERO_TOTALS})
        mode = 'APPLY' if options['apply'] else 'DRY RUN'
        self.stdout.write(f'{mode}. Rule: {RULE}')
        if options['output']:
            # a bad path fails here, before anything is written
            open(options['output'], 'a', encoding='utf-8').close()  # pylint: disable=consider-using-with
        for expansion_id in self.get_expansion_ids():
            self.rebuild(expansion_id)
        self.stdout.write(f'{mode} done: {json.dumps(dict(self.totals), sort_keys=True)}')
        # gaining a lock changes what a HEAD's next version-less add resolves to: the number to decide the run on
        self.stdout.write(
            f'Gaining a lock: {self.totals["with_gains_lock"]} expansion(s): '
            f'{self.totals["with_gains_lock_released"]} of released and '
            f'{self.totals["with_gains_lock_unreleased"]} of unreleased collection versions; '
            f'{self.totals["with_gains_lock_head"]} of them a HEAD\'s default expansion, whose next add resolves by it')
        if options['output']:
            self.stdout.write(f'Full report: {options["output"]}')

    def get_expansion_ids(self):
        if self.options['all']:
            expansions = Expansion.objects.filter(
                is_active=True, collection_version__is_active=True, id__gt=self.options['start_after_id'])
            if self.options['skip_released']:
                expansions = expansions.exclude(collection_version__released=True)
            ids = expansions.order_by('id').values_list('id', flat=True)
            return list(ids[:self.options['limit']] if self.options['limit'] else ids)
        expansions = [self.get_expansion(uri) for uri in self.options['uris']]
        return [
            expansion.id for expansion in expansions
            if not (self.options['skip_released'] and expansion.collection_version.released)]

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
                # apply locks the expansion and its collection version, so a release committed meanwhile waits and is
                # then skipped; a dry run takes no lock
                expansions = Expansion.objects.select_related('collection_version').select_for_update(
                    of=('self', 'collection_version')) if self.options['apply'] else Expansion.objects
                expansion = expansions.get(id=expansion_id)
                if self.options['skip_released'] and expansion.collection_version.released:
                    # released after it was selected
                    self.totals['skipped_released'] += 1
                    return
                lists = ExpansionVersionLists(expansion).build()
                report = lists.to_report()
                if self.options['apply'] and report['changed']:
                    if report['settled']:
                        lists.apply()
                    else:
                        # its last pass would change again: written, a second run would move it once more
                        self.totals['skipped_unsettled'] += 1
        except Exception as ex:  # pylint: disable=broad-except
            self.totals['failed'] += 1
            self.write({'id': expansion_id, 'error': f'{type(ex).__name__}: {ex}'})
            return
        self.count(report)
        if report['changed'] or self.options['verbose_unchanged']:
            self.write(report)

    def count(self, report):
        kind = 'released' if report['collection_version_released'] else 'unreleased'
        self.totals['expansions'] += 1
        self.totals[f'expansions_{kind}'] += 1
        self.totals['changed' if report['changed'] else 'unchanged'] += 1
        if not report['settled']:
            # a further rebuild would still change its lists: worth a look before the run
            self.totals['unsettled'] += 1
        elif report['passes'] > 1:
            self.totals['settled_after_several_passes'] += 1
        if report['changed']:
            self.totals[f'changed_{kind}'] += 1
        if report['reported_release_changes']:
            # FHIR $expand would report another release for these members
            self.totals[f'with_reported_release_changes_{kind}'] += 1
            self.totals[f'members_with_reported_release_change_{kind}'] += report['reported_release_changes']
        for key in ('gains_lock', 'loses_lock', 'changes_lock', 'in_both'):
            if report[key]:
                self.totals[f'with_{key}'] += 1
        if report['gains_lock']:
            self.totals[f'with_gains_lock_{kind}'] += 1
            if report['is_head'] and report['is_default']:
                self.totals['with_gains_lock_head'] += 1
        self.count_notes(report['references'])

    def count_notes(self, notes):
        if any(note.get('kept') for note in notes):
            self.totals['with_references_kept_as_recorded'] += 1
        if any(note.get('partial_holders') for note in notes):
            self.totals['with_references_from_several_versions'] += 1
        if any('does not resolve' in note.get('reason', '') for note in notes):
            self.totals['with_unresolved_references'] += 1
        if any('holds none of its members' in note.get('reason', '') for note in notes):
            # its system resolves today to another repository than the one its members came from
            self.totals['with_references_resolving_elsewhere'] += 1
        if any(len(note.get('holders', [])) > 1 for note in notes):
            self.totals['with_members_in_several_versions'] += 1

    def write(self, report):
        if 'error' in report:
            self.stdout.write(f'FAILED {report["id"]}: {report["error"]}')
        else:
            summary = {relation: f'+{len(change["added"])} -{len(change["removed"])}'
                       for relation, change in report['changes'].items()}
            flags = [key for key in ('gains_lock', 'loses_lock', 'changes_lock', 'in_both') if report[key]]
            if report['collection_version_released']:
                flags.append('released')
            if not report['settled']:
                flags.append('unsettled')
            if report['reported_release_changes']:
                flags.append(f'reported_release_changes={report["reported_release_changes"]}')
            self.stdout.write(f'{report["expansion"]}: {json.dumps(summary)} {" ".join(flags)}'.rstrip())
        if self.options['output']:
            with open(self.options['output'], 'a', encoding='utf-8') as file:
                file.write(json.dumps(report, default=str) + '\n')
