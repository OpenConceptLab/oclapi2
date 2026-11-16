from django.core.management import BaseCommand
from django.db import transaction
from django.db.models import F, Q

from core.common.constants import HEAD
from core.mappings.models import Mapping
from core.sources.models import Source

SIDES = (
    ('from_source', 'from_source_url', 'from_source_version'),
    ('to_source', 'to_source_url', 'to_source_version'),
)


class Command(BaseCommand):
    help = ("Points each HEAD mapping's from_source_url/to_source_url at its from/to source when the URL is missing "
            "or is another source's URI or canonical URL (as $clone left them), and clears the source version that "
            "came with the old URL. Only HEAD mappings (the versioned objects) are repaired: mapping versions, which "
            "released source versions and their exports hold, are left as they are, and the next edit of a mapping "
            "creates its new version from the repaired HEAD. URLs that match the mapping's own source or no known "
            "source are left alone. Each source is repaired in its own transaction.")

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verb = 'would be repaired' if dry_run else 'repaired'
        for source_field, url_field, version_field in SIDES:
            counts = self.repair(source_field, url_field, version_field, dry_run)
            self.stdout.write(f'{sum(counts.values())} HEAD mapping(s) {verb} on {url_field}')
            for source_uri, count in counts.items():
                self.stdout.write(f'  {source_uri}: {count}')

    @staticmethod
    def get_head_mappings():
        return Mapping.objects.filter(id=F('versioned_object_id'))

    @staticmethod
    def source_url_in(url_field, sources):
        return Q(**{f'{url_field}__in': sources.values('uri')}) | Q(
            **{f'{url_field}__in': sources.exclude(canonical_url__isnull=True).exclude(
                canonical_url='').values('canonical_url')})

    def get_source_ids(self, source_field, url_field):
        candidates = Q(**{f'{url_field}__isnull': True}) | self.source_url_in(
            url_field, Source.objects.filter(version=HEAD))
        return list(
            self.get_head_mappings().filter(**{f'{source_field}__isnull': False}).filter(candidates).exclude(
                **{url_field: F(f'{source_field}__uri')}
            ).exclude(
                **{url_field: F(f'{source_field}__canonical_url')}
            ).order_by().values_list(f'{source_field}_id', flat=True).distinct()
        )

    def repair(self, source_field, url_field, version_field, dry_run=False):
        counts = {}
        for source in Source.objects.filter(id__in=self.get_source_ids(source_field, url_field)).order_by('uri'):
            count = self.repair_source(source, source_field, url_field, version_field, dry_run)
            if count:
                counts[source.uri] = count
        return counts

    def repair_source(self, source, source_field, url_field, version_field, dry_run=False):  # pylint: disable=too-many-arguments
        rows = self.get_head_mappings().filter(**{f'{source_field}_id': source.id}).exclude(**{url_field: source.uri})
        if source.canonical_url:
            rows = rows.exclude(**{url_field: source.canonical_url})
        missing = rows.filter(**{f'{url_field}__isnull': True})
        elsewhere = rows.filter(
            self.source_url_in(url_field, Source.objects.filter(version=HEAD).exclude(id=source.id)))
        if dry_run:
            return missing.count() + elsewhere.count()
        with transaction.atomic():
            count = missing.update(**{url_field: source.uri})
            return count + elsewhere.update(**{url_field: source.uri, version_field: None})
