import sys

from django.conf import settings
from django.core.management import BaseCommand

from core.common.services import RedisService
from core.users.models import UserProfile


class Command(BaseCommand):
    help = 'celery beat healthcheck'

    def handle(self, *args, **options):
        redis_service = RedisService()
        if not redis_service.exists(settings.CELERYBEAT_HEALTHCHECK_KEY):
            sys.exit(1)

