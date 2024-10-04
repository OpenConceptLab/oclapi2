from django.core.management import BaseCommand

from core.collections.models import Collection
from core.events.models import Event
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile


class Command(BaseCommand):
    help = 'populate events from existing content'

    def add_joined_ocl_event(self):
        for user in UserProfile.objects.filter(is_active=True):
            if not user.events.filter(referenced_object_url__isnull=True, event_type=Event.JOINED).exists():
                event = Event.record_joined_ocl(user)
                if event:
                    Event.objects.filter(id=event.id).update(created_at=user.created_at)

    def add_org_created_event(self):
        for org in Organization.objects.filter(is_active=True).select_related('created_by'):
            if Event.objects.filter(referenced_object_url=org.url, event_type=Event.CREATED).exists():
                continue
            event = Event.record(org, Event.CREATED, org.created_by, org.created_by)
            if event:
                Event.objects.filter(id=event.id).update(created_at=org.created_at)

    def add_source_created_event(self):
        for source in Source.objects.filter(is_active=True).select_related('created_by'):
            if Event.objects.filter(referenced_object_url=source.url, event_type=Event.CREATED).exists():
                continue
            event = Event.record(source, Event.CREATED, source.created_by, source.created_by)
            if event:
                Event.objects.filter(id=event.id).update(created_at=source.created_at)

    def add_collection_created_event(self):
        for collection in Collection.objects.filter(is_active=True).select_related('created_by'):
            if Event.objects.filter(referenced_object_url=collection.url, event_type=Event.CREATED).exists():
                continue
            event = Event.record(collection, Event.CREATED, collection.created_by, collection.created_by)
            if event:
                Event.objects.filter(id=event.id).update(created_at=collection.created_at)

    def handle(self, *args, **options):
        self.add_joined_ocl_event()
        self.add_org_created_event()
        self.add_source_created_event()
        self.add_collection_created_event()
