# This will make sure the app is always imported when
# Django starts so that shared_task will use this app.
from .celery import app as celery_app

__all__ = ('celery_app',)

API_VERSION = '2.3.96'
API_BUILD = '7cfc6e4d'
VERSION = API_VERSION + '-' + API_BUILD
__version__ = VERSION
