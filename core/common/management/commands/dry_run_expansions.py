import json

from django.core.management import BaseCommand, CommandError
from django.test import override_settings

from core.collections.models import Collection, Expansion


class Command(BaseCommand):
    help = ('Rebuilds expansions in a transaction that is rolled back, with the toggles of the given environment, '
            'and lists every member that would change, per reference. Nothing is saved or indexed.')
    ENVS = ['development', 'ci', 'qa', 'demo', 'staging', 'who-staging', 'production']

    def add_arguments(self, parser):
        parser.add_argument('uris', nargs='*', help='Expansion or collection version URIs (its default expansion)')
        parser.add_argument('--sample', type=int, default=0, help='Also dry run this many random expansions')
        parser.add_argument('--env', choices=self.ENVS, help='Read toggles as this environment does (default: this one)')
        parser.add_argument('--output', help='Write the full report as JSON to this file')

    def handle(self, *args, **options):
        expansions = [self.get_expansion(uri) for uri in options['uris']]
        if options['sample']:
            expansions += list(Expansion.objects.filter(is_processing=False).order_by('?')[:options['sample']])
        if not expansions:
            raise CommandError('Pass expansion or collection version URIs, or --sample')

        reports = []
        for expansion in expansions:
            if expansion.is_processing:
                self.stdout.write(f'{expansion.uri}: skipped, it is being processed')
                continue
            try:
                with override_settings(**({'ENV': options['env']} if options['env'] else {})):
                    report = expansion.dry_run_rebuild()
            except Exception as ex:  # pylint: disable=broad-except
                reports.append({'expansion': expansion.uri, 'error': repr(ex)})
                self.stdout.write(f'{expansion.uri}: rebuild failed: {ex!r}')
                continue
            reports.append(report)
            self.write_summary(report)

        if options['output']:
            with open(options['output'], 'w') as file:
                json.dump(reports, file, indent=2)
            self.stdout.write(f'Full report: {options["output"]}')

    @staticmethod
    def get_expansion(uri):
        expansion = Expansion.objects.filter(uri=uri).first()
        if not expansion:
            collection_version = Collection.objects.filter(uri=uri).first()
            expansion = collection_version.expansion if collection_version else None
        if not expansion:
            raise CommandError(f'No expansion found for {uri}')
        return expansion

    def write_summary(self, report):
        self.stdout.write(f"{report['expansion']} ({report['seconds']}s)")
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
