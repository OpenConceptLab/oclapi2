from django.core.management import BaseCommand

from core.sources.models import Source


class Command(BaseCommand):
    help = 'Source HEAD should only have latest version'

    def handle(self, *args, **options):
        for source in Source.objects.filter(version='HEAD'):
            print(f"***SOURCE: {source.uri} - concepts")
            source.keep_concept_latest_versions_on_head()
            print(f"***SOURCE: {source.uri} - mappings")
            source.keep_mapping_latest_versions_on_head()
