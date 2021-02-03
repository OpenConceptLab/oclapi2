from django.core.management import BaseCommand

from core.collections.models import Collection
from core.orgs.models import Organization
from core.sources.models import Source


class Command(BaseCommand):
    help = 'populate Source/Collection/Org (empty) text field from extras.about'

    @staticmethod
    def process(queryset):
        for obj in queryset.all():
            obj.text = obj.extras['about']
            obj.save()

    def handle(self, *args, **options):
        self.process(Organization.objects.filter(text__isnull=True, extras__about__isnull=False))
        self.process(Source.objects.filter(text__isnull=True, extras__about__isnull=False))
        self.process(Collection.objects.filter(text__isnull=True, extras__about__isnull=False))
