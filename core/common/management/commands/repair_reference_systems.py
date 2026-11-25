import json
from collections import Counter

from django.core.management import BaseCommand
from django.db.models import Q

from core.collections.models import CollectionReference
from core.collections.parsers import CollectionReferenceExpressionStringParser
from core.common.utils import decode_string

FIELDS = ('system', 'code', 'resource_version', 'reference_type')
NOT_A_VERSION = 'its expression does not name a concept or mapping version'


class Command(BaseCommand):
    help = ('Fills in system on legacy references that have none and no value set, whose expression names a concept '
            'or mapping version, as the current parser reads the expression; code, resource_version and '
            'reference_type are filled in where they are missing or the default disagrees with the expression. Only '
            'reference fields change: no expansion is rebuilt and no member is added or removed, in any collection '
            'version. A dry run unless --apply is given; each reference it would change is listed with its old and new '
            'fields, and each it leaves alone for another reason with why. Run it before backfill_expansion_health, '
            'whose health counts the repaired references as resource-versioned.')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Save the fields; without it, a dry run')
        parser.add_argument('--output', help='Write each reference\'s change, or why it was left, as JSON lines')

    def handle(self, *args, **options):
        self.options = options
        self.totals = Counter()
        mode = 'APPLY' if options['apply'] else 'DRY RUN'
        self.stdout.write(f'{mode}.')
        output = open(options['output'], 'a', encoding='utf-8') if options['output'] else None  # pylint: disable=consider-using-with
        try:
            for reference in self.get_candidates().iterator(chunk_size=1000):
                report = self.repair(reference)
                self.write(report, output)
        finally:
            if output:
                output.close()
        self.stdout.write(f'{mode} done: {json.dumps(dict(self.totals), sort_keys=True)}')

    @staticmethod
    def get_candidates():
        """References with no system and no value set whose expression could name a concept or mapping"""
        # only what the repair reads: a collection row is wide (its JSON fields), and only its URI is reported
        return CollectionReference.objects.filter(
            Q(system__isnull=True) | Q(system=''), Q(valueset__isnull=True) | Q(valueset=[]),
            Q(expression__contains='/concepts/') | Q(expression__contains='/mappings/'),
        ).select_related('collection').only(
            'id', 'expression', 'collection_id', 'collection__uri', *FIELDS).order_by('id')

    @staticmethod
    def plan(reference):
        """(the fields to save, or None, and why it's left alone)"""
        parser = CollectionReferenceExpressionStringParser(expression=reference.expression)
        parser.parse()
        if parser.is_unknown or not (parser.system and parser.code and parser.resource_version):
            return None, NOT_A_VERSION
        if parser.version:
            # a source version and a resource version both named: for a person to judge, not this repair
            return None, 'its expression names a source version too'
        for field in ('code', 'resource_version'):
            # the parser reads the URL as written: an older save may hold the decoded form
            if getattr(reference, field) and decode_string(getattr(reference, field)) != decode_string(
                    getattr(parser, field)):
                return None, f'its {field} disagrees with its expression'
        changes = {'system': parser.system}
        for field in ('code', 'resource_version'):
            if not getattr(reference, field):
                changes[field] = getattr(parser, field)
        if parser.reference_type and reference.reference_type != parser.reference_type:
            changes['reference_type'] = parser.reference_type
        return changes, None

    def repair(self, reference):
        changes, reason = self.plan(reference)
        report = {
            'id': reference.id,
            'collection_version': reference.collection.uri if reference.collection_id else None,
            'expression': reference.expression,
            'before': {field: getattr(reference, field) for field in FIELDS},
        }
        if not changes:
            self.totals[f'left: {reason}'] += 1
            return {**report, 'left': reason}
        if self.options['apply']:
            # only while it still has no system, so a reference changed meanwhile is left as it is
            saved = CollectionReference.objects.filter(
                Q(system__isnull=True) | Q(system=''), id=reference.id).update(**changes)
            if not saved:
                self.totals['left: changed since it was read'] += 1
                return {**report, 'left': 'changed since it was read'}
        self.totals['repaired' if self.options['apply'] else 'would repair'] += 1
        return {**report, 'after': {**report['before'], **changes}}

    def write(self, report, output):
        if 'left' in report:
            # most candidates name no version at all: counted and in --output, but not one line each here
            if report['left'] != NOT_A_VERSION:
                self.stdout.write(f'LEFT {report["id"]} {report["expression"]}: {report["left"]}')
        else:
            changed = {
                field: report['after'][field] for field in FIELDS if report['after'][field] != report['before'][field]}
            self.stdout.write(
                f'{report["id"]} {report["collection_version"]} {report["expression"]}: {json.dumps(changed)}')
        if output:
            output.write(json.dumps(report, default=str) + '\n')
