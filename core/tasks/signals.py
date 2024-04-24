from celery.signals import task_postrun
from django import db


@task_postrun.connect
def on_task_done(sender=None, headers=None, body=None, **kwargs):
    for conn in db.connections.all():
        try:
            conn.close_if_unusable_or_obsolete()
        except db.utils.InterfaceError:
            pass
        except db.DatabaseError as exc:
            str_exc = str(exc)
            if 'closed' not in str_exc and 'not connected' not in str_exc:
                raise exc