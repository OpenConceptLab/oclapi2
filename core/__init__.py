# This will make sure the app is always imported when
# Django starts so that shared_task will use this app.
from .celery import app as celery_app

__all__ = ('celery_app',)

API_VERSION = '2.3.91'
API_BUILD = '34269f7a'
VERSION = API_VERSION + '-' + API_BUILD
__version__ = VERSION
