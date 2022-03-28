from django.core.management import BaseCommand
from django.db.models import Q, F

from core.sources.models import Source


class Command(BaseCommand):
    help = 'Source HEAD should only have latest version'

    def handle(self, *args, **options):
        for source in Source.objects.filter(version='HEAD'):
            print(f"***SOURCE: {source.uri}")
            source.concepts.set(
                source.concepts.filter(Q(is_latest_version=True) | Q(id=F('versioned_object_id'))))
