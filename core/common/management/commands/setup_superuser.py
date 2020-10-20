from django.conf import settings
from django.core.management import BaseCommand

from core.users.models import UserProfile


class Command(BaseCommand):
    help = 'setup superuser'

    def handle(self, *args, **options):
        user = UserProfile.objects.get(username='ocladmin', is_superuser=True)
        user.set_password(settings.API_SUPERUSER_PASSWORD)
        user.save()
        user.set_token(settings.API_SUPERUSER_TOKEN)
        user.save()

