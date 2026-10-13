from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management import BaseCommand

from core.users.constants import SUPERADMIN_GROUP, STAFF_GROUP
from core.users.models import UserProfile


class Command(BaseCommand):
    help = 'setup superuser'

    def handle(self, *args, **options):
        # base_entities.yaml no longer reloads on existing databases (OpenConceptLab/ocl_issues#2911), so set the
        # flags it used to restore here, or a demoted ocladmin would stop the container from starting
        user = UserProfile.objects.get(username='ocladmin')
        user.is_superuser = True
        user.is_staff = True
        user.groups.add(*Group.objects.filter(name__in=[SUPERADMIN_GROUP, STAFF_GROUP]))
        user.set_password(settings.API_SUPERUSER_PASSWORD)
        user.save()
        user.set_token(settings.API_SUPERUSER_TOKEN)
        user.save()
