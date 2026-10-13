from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management import BaseCommand

from core.users.constants import SUPERADMIN_GROUP, STAFF_GROUP
from core.users.models import UserProfile


class Command(BaseCommand):
    help = 'setup superuser'

    def handle(self, *args, **options):
        user = UserProfile.objects.get(username='ocladmin', is_superuser=True)
        user.groups.add(*Group.objects.filter(name__in=[SUPERADMIN_GROUP, STAFF_GROUP]))
        user.set_password(settings.API_SUPERUSER_PASSWORD)
        user.save()
        user.set_token(settings.API_SUPERUSER_TOKEN)
        user.save()

