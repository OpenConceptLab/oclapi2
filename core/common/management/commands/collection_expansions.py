from django.conf import settings
from django.core.management import BaseCommand

from core.collections.models import Collection, Expansion
from core.users.models import UserProfile


class Command(BaseCommand):
    help = 'Convert collection version without expansions to with expansions'

    def handle(self, *args, **options):
        for collection in Collection.objects.exclude(autoexpand=False).filter(expansions__isnull=True):
            print(f"Migrating Collection Version f{collection.url}...")
            if collection.is_head:
                collection.autoexpand_head = True
            else:
                collection.autoexpand = True
            collection.save()
            expansion = collection.fix_auto_expansion()
            if expansion and expansion.id:
                print(f"Created Expansion f{expansion.url}")
            else:
                print(f"Could not create expansion.")
                if collection.is_head:
                    collection.autoexpand_head = None
                else:
                    collection.autoexpand = None
                collection.save()
                print("Moving On!")
