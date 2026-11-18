import json
import os
import random
from functools import partial

from django.conf import settings
from django.core.management import BaseCommand, CommandError

from core.collections.dry_runs import ExpansionDryRun
from core.collections.models import Collection, Expansion
from core.sources.models import Source


class Command(BaseCommand):
    help = ('Simulates expansion changes in transactions that are rolled back and lists every member that would '
            'change, per reference. Nothing is saved or indexed, and no task is queued. Without --toggle, rebuilds '
            'are compared with the stored expansions; with --toggle, each change is made with production\'s toggles '
            'and again with the given toggles on as well, and the two are compared.')
    ENVS = ['development', 'ci', 'qa', 'demo', 'staging', 'who-staging', 'production']
    MODES = ['rebuild', 'add', 'version', 'import']

    def add_arguments(self, parser):
        parser.add_argument(
            'uris', nargs='*',
            help='rebuild: expansion or collection version URIs (its default expansion); '
                 'add, version, import: HEAD collection version URIs')
        parser.add_argument(
            '--mode', choices=self.MODES, default='rebuild',
            help='rebuild: rebuild the expansion; add: remove each sampled reference and add it again; '
                 'version: create a release from HEAD (HEAD stays locked until each run rolls back); '
                 'import: bulk import --size concepts of --source')
        parser.add_argument(
            '--toggle', action='append', default=[],
            help='Compare production\'s toggles with these on as well (repeatable). Required except for rebuild')
        parser.add_argument('--env', choices=self.ENVS, help='rebuild without --toggle: read toggles as this env does')
        parser.add_argument('--sample', type=int, default=0, help='Also dry run this many random expansions or HEADs')
        parser.add_argument(
            '--loose-locks', action='store_true',
            help='add only: sample only HEADs whose expansion locks a HEAD, unreleased or retired version')
        parser.add_argument('--references', type=int, default=5, help='add: references per HEAD, at random')
        parser.add_argument('--source', help='import: source URI whose concepts are imported')
        parser.add_argument('--size', type=int, default=100, help='import: number of concepts to import')
        parser.add_argument('--seed', type=int, help='Random seed, so a rerun samples the same')
        parser.add_argument(
            '--output',
            help='Append each report as a JSON line to this file; a rerun skips those already in it, except failures')

    def handle(self, *args, **options):
        mode, toggles = options['mode'], options['toggle']
        if toggles and options['env']:
            raise CommandError('--env applies only without --toggle')
        if mode != 'rebuild' and not toggles:
            raise CommandError(f'--mode {mode} needs --toggle')
        unknown = ExpansionDryRun.unknown_toggles(toggles)
        if unknown:
            raise CommandError(f'Unknown toggles: {", ".join(unknown)}')
        if mode == 'import' and not options['source']:
            raise CommandError('--mode import needs --source')
        if options['loose_locks'] and mode != 'add':
            raise CommandError('--loose-locks applies only to --mode add')
        if options['seed'] is not None:
            random.seed(options['seed'])

        self.dry_run = ExpansionDryRun(toggles) if toggles else \
            ExpansionDryRun([], base_env=options['env'] or settings.ENV)
        self.options = options
        self.done = self.get_done_keys()
        targets = self.get_expansions() if mode == 'rebuild' else self.get_heads()
        if not targets:
            raise CommandError('Pass URIs, or --sample')

        getattr(self, f'run_{mode}')(targets)
        if self.dry_run.blocked_tasks:
            self.stdout.write(f'Tasks not queued: {dict(self.dry_run.blocked_tasks)}')
        if options['output']:
            self.stdout.write(f'Full report: {options["output"]}')

    def get_expansions(self):
        expansions = [self.get_expansion(uri) for uri in self.options['uris']]
        if self.options['sample']:
            ids = list(Expansion.objects.filter(is_processing=False).order_by('id').values_list('id', flat=True))
            expansions += list(Expansion.objects.filter(id__in=self.sample(ids)).order_by('uri'))
        return expansions

    def sample(self, ids):
        return random.sample(ids, min(self.options['sample'], len(ids)))

    def get_heads(self):
        heads = [self.get_head(uri) for uri in self.options['uris']]
        if self.options['sample']:
            queryset = ExpansionDryRun.get_heads_with_loose_locks() if self.options['loose_locks'] else \
                Collection.objects.filter(version='HEAD')
            ids = list(queryset.exclude(expansion_uri=None).exclude(expansion_uri='').order_by('id').values_list(
                'id', flat=True))
            heads += list(Collection.objects.filter(id__in=self.sample(ids)).order_by('uri'))
        return heads

    @staticmethod
    def to_collection_version_uri(uri):
        """A HEAD's URI has no HEAD segment"""
        return uri[:-len('HEAD/')] if uri.endswith('/HEAD/') else uri

    def get_expansion(self, uri):
        expansion = Expansion.objects.filter(uri=uri).first()
        if not expansion:
            collection_version = Collection.objects.filter(uri=self.to_collection_version_uri(uri)).first()
            expansion = collection_version.expansion if collection_version else None
        if not expansion:
            raise CommandError(f'No expansion found for {uri}')
        return expansion

    def get_head(self, uri):
        head = Collection.objects.filter(uri=self.to_collection_version_uri(uri), version='HEAD').first()
        if not head or not head.expansion_uri:
            raise CommandError(f'No HEAD collection version with an expansion found for {uri}')
        return head

    def get_done_keys(self):
        path = self.options['output']
        if not path or not os.path.exists(path):
            return set()
        try:
            with open(path) as file:
                reports = [json.loads(line) for line in file if line.strip()]
        except json.JSONDecodeError as ex:
            raise CommandError(f'{path} is not a JSON lines report from this command') from ex
        if not all(isinstance(report, dict) for report in reports):
            raise CommandError(f'{path} is not a JSON lines report from this command')
        done = {report.get('key') for report in reports if 'error' not in report}
        self.stdout.write(f'{len(done)} already in {path}, skipping them')
        return done

    def is_skipped(self, key, expansion):
        if key in self.done:
            return True
        if expansion.is_processing:
            self.stdout.write(f'{key}: skipped, it is being processed')
            return True
        return False

    def run_safely(self, key, run, write_summary):
        try:
            report = run()
        except Exception as ex:  # pylint: disable=broad-except
            report = {'key': key, 'error': repr(ex)}
            self.stdout.write(f'{key}: failed: {ex!r}')
        else:
            write_summary(report)
        if self.options['output']:
            with open(self.options['output'], 'a') as file:
                file.write(json.dumps(report, default=str) + '\n')

    def run_rebuild(self, expansions):
        for expansion in expansions:
            if self.is_skipped(expansion.uri, expansion):
                continue
            run = self.dry_run.rebuild if self.dry_run.toggles else self.dry_run.rebuild_against_stored
            self.run_safely(expansion.uri, partial(run, expansion), self.write_summary)

    def run_add(self, heads):
        for head in heads:
            references = list(head.references.filter(include=True).order_by('id'))
            versionless = [reference for reference in references if not reference.names_repo_version]
            candidates = versionless or references
            for reference in random.sample(candidates, min(self.options['references'], len(candidates))):
                key = f'{head.uri} {reference.expression}'
                if self.is_skipped(key, head.expansion):
                    continue
                self.run_safely(key, partial(self.dry_run.add, head, reference), self.write_summary)

    def run_version(self, heads):
        for head in heads:
            if not self.is_skipped(head.uri, head.expansion):
                self.run_safely(head.uri, partial(self.dry_run.create_version, head), self.write_summary)

    def run_import(self, heads):
        source = Source.objects.filter(uri=self.options['source']).first()
        if not source:
            raise CommandError(f'No source found for {self.options["source"]}')
        source_uris = None
        size = self.options['size']
        for head in heads:
            key = ExpansionDryRun.get_import_key(head, size)
            if self.is_skipped(key, head.expansion):
                continue
            if source_uris is None:
                source_uris = ExpansionDryRun.get_source_concept_uris(source)
            expressions = ExpansionDryRun.get_import_expressions(head, source_uris, size)
            self.run_safely(key, partial(self.dry_run.import_references, head, expressions, size), self.write_summary)

    def write_summary(self, report):
        seconds = report['seconds']
        timing = f'{seconds[0]}s -> {seconds[1]}s' if isinstance(seconds, list) else f'{seconds}s'
        self.stdout.write(f"{report['key']} ({timing})")
        for resource_type in ('concepts', 'mappings'):
            counts = ', '.join(f'{count} {status}' for status, count in report[resource_type].items())
            self.stdout.write(f'  {resource_type}: {counts}')
        for field in [*Expansion.REPO_VERSION_RELATIONS, 'unresolved_repo_versions']:
            if field in report:
                self.stdout.write(f"  {field}: {report[field]['before']} -> {report[field]['after']}")
        for expression, changes in report['references'].items():
            self.stdout.write(f'  {expression}: {len(changes)} member(s) would change')
        if report['unattributed']:
            self.stdout.write(f"  no reference resolved: {len(report['unattributed'])} member(s) would change")
        if any(report.get('errors') or []):
            self.stdout.write(f"  errors: {report['errors']}")
        if 'added' in report:
            self.stdout.write(f"  added: {report['added'][0]} -> {report['added'][1]}")
        for label in ('off', 'on'):
            if label in report:
                run = report[label]
                self.stdout.write(
                    f"  toggles {label}: built_with={run['built_with']}, against HEAD {run['versus_head']}, "
                    f"HEAD concepts whose newest version isn't in its source version: "
                    f"{run['newest_not_in_source_versions']}")
