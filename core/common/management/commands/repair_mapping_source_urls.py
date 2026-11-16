from django.core.management import BaseCommand
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q

from core.common.constants import HEAD
from core.mappings.models import Mapping
from core.sources.models import Source

# to first: replaced targets are told apart by comparing to_source_url with the not yet repaired from_source_url
SIDES = (
    ('to_source', 'to_source_url', 'to_source_version'),
    ('from_source', 'from_source_url', 'from_source_version'),
)


class Command(BaseCommand):
    help = (
        "Points each HEAD mapping's from_source_url/to_source_url at its from/to source when the URL is missing or "
        "is another source's URI or canonical URL (as $clone left them), and clears the source version that came "
        "with the old URL. URLs that match the mapping's own source or no known source are left alone. "
        "Only HEAD mappings (the versioned objects) are repaired. Mapping versions are left as they are: released "
        "source versions and their exports, a mapping's versions list and version detail, and collection "
        "expansions holding a version keep the old URL. The next edit of a mapping creates its new version from "
        "the repaired HEAD. "
        "A mapping whose to_concept was moved into its own source while its to_source_url names a source other "
        "than the one it was cloned from (from_source_url, matched by URI or canonical URL) had its target in "
        "another code system replaced by $clone. Its to_source_url is the only record of that code system, so it is "
        "listed with both URLs to be fixed by hand, and its to_source_url is not repaired. Each source is repaired "
        "in its own transaction."
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verb = 'would be repaired' if dry_run else 'repaired'
        for source_field, url_field, version_field in SIDES:
            counts, skipped = self.repair(source_field, url_field, version_field, dry_run)
            self.stdout.write(f'{sum(counts.values())} HEAD mapping(s) {verb} on {url_field}')
            for source_uri, count in counts.items():
                self.stdout.write(f'  {source_uri}: {count}')
            if skipped:
                self.stdout.write(
                    f'{len(skipped)} HEAD mapping(s) skipped on {url_field}: target in another code system was '
                    f'replaced by a concept in the same source, fix by hand')
                for uri, from_url, to_url in skipped:
                    self.stdout.write(f'  {uri} (from_source_url {from_url}, to_source_url {to_url})')

    @staticmethod
    def get_head_mappings():
        return Mapping.objects.filter(id=F('versioned_object_id'))

    @staticmethod
    def url_missing(url_field):
        return Q(**{f'{url_field}__isnull': True}) | Q(**{url_field: ''})

    @staticmethod
    def source_url_is(url_field):
        return Q(uri=OuterRef(url_field)) | (Q(canonical_url=OuterRef(url_field)) & ~Q(canonical_url=''))

    def get_replaced_targets(self, rows):
        same_source = Source.objects.filter(version=HEAD).filter(
            self.source_url_is('to_source_url')).filter(self.source_url_is('from_source_url'))
        return rows.filter(to_source_id=F('parent_id')).exclude(Exists(same_source))

    @staticmethod
    def source_url_in(url_field, sources):
        return Q(**{f'{url_field}__in': sources.values('uri')}) | Q(
            **{f'{url_field}__in': sources.exclude(canonical_url__isnull=True).exclude(
                canonical_url='').values('canonical_url')})

    def get_source_ids(self, source_field, url_field):
        candidates = self.url_missing(url_field) | self.source_url_in(url_field, Source.objects.filter(version=HEAD))
        return list(
            self.get_head_mappings().filter(**{f'{source_field}__isnull': False}).filter(candidates).exclude(
                **{url_field: F(f'{source_field}__uri')}
            ).exclude(
                **{url_field: F(f'{source_field}__canonical_url')}
            ).order_by().values_list(f'{source_field}_id', flat=True).distinct()
        )

    def repair(self, source_field, url_field, version_field, dry_run=False):
        counts, skipped = {}, []
        for source in Source.objects.filter(id__in=self.get_source_ids(source_field, url_field)).order_by('uri'):
            count, source_skipped = self.repair_source(source, source_field, url_field, version_field, dry_run)
            if count:
                counts[source.uri] = count
            skipped += source_skipped
        return counts, skipped

    def repair_source(self, source, source_field, url_field, version_field, dry_run=False):  # pylint: disable=too-many-arguments
        rows = self.get_head_mappings().filter(**{f'{source_field}_id': source.id}).exclude(**{url_field: source.uri})
        if source.canonical_url:
            rows = rows.exclude(**{url_field: source.canonical_url})
        missing = rows.filter(self.url_missing(url_field))
        elsewhere = rows.filter(
            self.source_url_in(url_field, Source.objects.filter(version=HEAD).exclude(id=source.id)))
        skipped = []
        if source_field == 'to_source':
            replaced_target = self.get_replaced_targets(elsewhere)
            skipped = list(replaced_target.order_by('uri').values_list('uri', 'from_source_url', 'to_source_url'))
            elsewhere = elsewhere.exclude(id__in=replaced_target.values('id'))
        if dry_run:
            return missing.count() + elsewhere.count(), skipped
        with transaction.atomic():
            count = missing.update(**{url_field: source.uri})
            return count + elsewhere.update(**{url_field: source.uri, version_field: None}), skipped
