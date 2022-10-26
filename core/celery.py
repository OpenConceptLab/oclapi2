import os

from celery import Celery
from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
app = Celery('core')
app.conf.ONCE = settings.CELERY_ONCE  # force CELERY_ONCE to load settings
app.conf.CELERYBEAT_SCHEDULE = settings.CELERYBEAT_SCHEDULE
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)
