from django.core.management import BaseCommand
from django.db.models import Q

from core.common.constants import HEAD
from core.common.tasks import update_active_count
from core.sources.models import Source

FIELDS = ('active_concepts', 'active_mappings')


class Command(BaseCommand):
    help = ('Recounts the active concepts and mappings stored on source versions (HEAD aside), the counts their '
            'unfiltered lists answer with. Every count that changes is listed.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--uri', action='append', default=[],
            help='Only the versions of the source with this URI, e.g. /orgs/MyOrg/sources/MySource/. Repeatable.')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        changed = 0
        for version in self.get_versions(options['uri']):
            for field in FIELDS:
                before = getattr(version, field)
                after = self.recount(version, field, dry_run)
                if before != after:
                    changed += 1
                    self.stdout.write(f'  {version.uri} {field}: {before} -> {after}')
        verb = 'would change' if dry_run else 'changed'
        self.stdout.write(f'{changed} count(s) {verb}')

    @staticmethod
    def get_versions(uris):
        versions = Source.objects.exclude(version=HEAD)
        if uris:  # a version's URI is its source's, followed by the version
            criteria = Q()
            for uri in uris:
                criteria |= Q(uri__startswith=uri if uri.endswith('/') else uri + '/')
            versions = versions.filter(criteria)
        return versions.order_by('id').iterator()

    @staticmethod
    def recount(version, field, dry_run):
        if dry_run:
            getattr(version, f'set_{field}')()
            return getattr(version, field)
        update_active_count(Source, version.id, field)  # under the count tasks' lock, clearing the cached lists
        return Source.objects.filter(id=version.id).values_list(field, flat=True).first()
