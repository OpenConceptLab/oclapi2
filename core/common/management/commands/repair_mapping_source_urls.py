from django.core.management import BaseCommand
from django.db import transaction
from django.db.models import Q

from core.common.constants import HEAD
from core.mappings.models import Mapping
from core.sources.models import Source


class Command(BaseCommand):
    help = ("Points each mapping's from_source_url/to_source_url at its from/to source when the URL is missing or "
            "is another source's URL (as $clone left them), and clears the source version that came with the old "
            "URL. URLs that match the mapping's own source or no known source are left alone.")

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verb = 'would be repaired' if dry_run else 'repaired'
        for side in ('from', 'to'):
            counts = self.repair(side, dry_run)
            self.stdout.write(f'{sum(counts.values())} mapping row(s) {verb} on {side}_source_url')
            for source_uri, count in counts.items():
                self.stdout.write(f'  {source_uri}: {count}')

    @staticmethod
    def source_url_in(url_field, sources):
        return Q(**{f'{url_field}__in': sources.values('uri')}) | Q(
            **{f'{url_field}__in': sources.exclude(canonical_url__isnull=True).exclude(
                canonical_url='').values('canonical_url')})

    def get_source_ids(self, side):
        url_field = f'{side}_source_url'
        candidates = Q(**{f'{url_field}__isnull': True}) | self.source_url_in(
            url_field, Source.objects.filter(version=HEAD))
        return list(
            Mapping.objects.filter(**{f'{side}_source__isnull': False}).filter(candidates).order_by().values_list(
                f'{side}_source_id', flat=True).distinct()
        )

    def repair(self, side, dry_run=False):
        url_field, version_field = f'{side}_source_url', f'{side}_source_version'
        counts = {}
        with transaction.atomic():
            for source in Source.objects.filter(id__in=self.get_source_ids(side)).order_by('uri'):
                rows = Mapping.objects.filter(**{f'{side}_source_id': source.id}).exclude(**{url_field: source.uri})
                if source.canonical_url:
                    rows = rows.exclude(**{url_field: source.canonical_url})
                missing = rows.filter(**{f'{url_field}__isnull': True})
                others = Source.objects.filter(version=HEAD).exclude(id=source.id)
                elsewhere = rows.filter(self.source_url_in(url_field, others))
                if dry_run:
                    count = missing.count() + elsewhere.count()
                else:
                    count = missing.update(**{url_field: source.uri})
                    count += elsewhere.update(**{url_field: source.uri, version_field: None})
                if count:
                    counts[source.uri] = count
        return counts
