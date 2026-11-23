import json
import time
from collections import Counter

from django.core.management import BaseCommand, CommandError
from django.db.models import Q

from core.collections.constants import EXPANSION_HEALTH_TOGGLE
from core.collections.health import RULES_VERSION, ExpansionHealth
from core.collections.models import Collection, Expansion


class Command(BaseCommand):
    help = ('Computes expansion health for expansions that have none, or whose health is not current or was computed '
            'with older rules. A dry run unless --apply is given: it computes and reports, and stores nothing. '
            'Expansions being built are skipped and reported, since their build stores health when it ends. '
            'Run it after rebuild_expansion_version_lists, whose lists health reads.')

    def add_arguments(self, parser):
        parser.add_argument(
            'uris', nargs='*', help='Expansion URIs, or collection version URIs for their default expansion')
        parser.add_argument('--all', action='store_true', help='Every active expansion that needs it, by id')
        parser.add_argument('--apply', action='store_true', help='Store the computed health; without it, a dry run')
        parser.add_argument(
            '--force', action='store_true', help='--all: also expansions whose health is current and up to date')
        parser.add_argument('--start-after-id', type=int, default=0, help='--all: resume after this expansion id')
        parser.add_argument('--limit', type=int, help='--all: stop after this many expansions')
        parser.add_argument('--pause', type=float, default=0, help='Seconds to wait between expansions')
        parser.add_argument('--output', help='Append each expansion\'s result, and each failure, as JSON lines')

    def handle(self, *args, **options):
        if bool(options['uris']) == options['all']:
            raise CommandError('Pass expansion or collection version URIs, or --all')
        if options['apply'] and not Expansion.is_health_on():
            raise CommandError(
                f'{EXPANSION_HEALTH_TOGGLE} is off here, so stored health would not be kept current. Turn it on first')
        self.options = options
        self.totals = Counter()
        self.states = Counter()
        self.reasons = Counter()
        mode = 'APPLY' if options['apply'] else 'DRY RUN'
        self.stdout.write(f'{mode}. Rules version {RULES_VERSION}.')
        self.has_computed = False
        for expansion_id in self.get_expansion_ids():
            self.backfill(expansion_id)
        totals = {**self.totals, 'states': dict(self.states), 'reasons': dict(self.reasons)}
        self.stdout.write(f'{mode} done: {json.dumps(totals, sort_keys=True)}')
        if options['output']:
            self.stdout.write(f'Full report: {options["output"]}')

    def get_expansion_ids(self):
        if not self.options['all']:
            return [self.get_expansion(uri).id for uri in self.options['uris']]
        # those being built are listed too, so a skipped one shows in the report
        expansions = Expansion.objects.filter(
            is_active=True, collection_version__is_active=True, id__gt=self.options['start_after_id'])
        if not self.options['force']:
            # none yet, not current, or computed with older rules
            expansions = expansions.filter(
                Q(health__isnull=True) | ~Q(health_is_current=True) | ~Q(health__rules_version=RULES_VERSION))
        ids = expansions.order_by('id').values_list('id', flat=True)
        return list(ids[:self.options['limit']] if self.options['limit'] else ids)

    @staticmethod
    def get_expansion(uri):
        expansion = Expansion.objects.filter(uri=uri).first()
        if not expansion:
            version = Collection.objects.filter(uri=uri).first()
            expansion = version.expansion if version else None
        if not expansion:
            raise CommandError(f'No expansion for {uri}')
        return expansion

    def backfill(self, expansion_id):
        expansion = Expansion.objects.filter(id=expansion_id).select_related('collection_version').first()
        if not expansion:
            return
        skipped = self.get_skip_reason(expansion)
        if skipped:
            self.totals[f'skipped_{skipped.replace(" ", "_")}'] += 1
            self.write({'expansion': expansion.uri, 'id': expansion.id, 'skipped': skipped})
            return
        # waits only between computations: a skip costs nothing
        if self.has_computed and self.options['pause']:
            time.sleep(self.options['pause'])
        self.has_computed = True
        before = {'state': (expansion.health or {}).get('state'), 'is_current': expansion.health_is_current,
                  'rules_version': (expansion.health or {}).get('rules_version')}
        started = time.time()
        try:
            health = expansion.update_health() if self.options['apply'] else ExpansionHealth(expansion).compute()
        except Exception as ex:  # pylint: disable=broad-except
            health, error = None, f'{type(ex).__name__}: {ex}'
        else:
            error = None if health else 'computing health failed; see the log'
        report = {
            'expansion': expansion.uri,
            'id': expansion.id,
            'collection_version': expansion.collection_version.uri,
            'is_default': expansion.collection_version.expansion_uri == expansion.uri,
            'before': before,
            'seconds': round(time.time() - started, 2),
        }
        if error:
            self.totals['failed'] += 1
            self.write({**report, 'error': error})
            return
        self.totals['expansions'] += 1
        self.states[health['state']] += 1
        for reason in health['reasons']:
            self.reasons[reason['code']] += 1
        self.write({**report, 'health': health})

    def get_skip_reason(self, expansion):
        if expansion.is_processing:
            return 'being built'
        is_up_to_date = expansion.health_is_current is True and \
            (expansion.health or {}).get('rules_version') == RULES_VERSION
        # listed at the start, so a build since may have stored it
        if self.options['all'] and not self.options['force'] and is_up_to_date:
            return 'now current'
        return None

    def write(self, report):
        if 'skipped' in report:
            self.stdout.write(f'SKIPPED {report["expansion"]}: {report["skipped"]}')
        elif 'error' in report:
            self.stdout.write(f'FAILED {report["expansion"]}: {report["error"]}')
        else:
            health = report['health']
            reasons = ' '.join(sorted(reason['code'] for reason in health['reasons']))
            self.stdout.write(
                f'{report["expansion"]}: {report["before"]["state"]} -> {health["state"]} {reasons}'.rstrip())
        if self.options['output']:
            with open(self.options['output'], 'a', encoding='utf-8') as file:
                file.write(json.dumps(report, default=str) + '\n')
