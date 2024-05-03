import json
import traceback
import uuid

from celery.result import AsyncResult
from celery.states import PENDING, ALL_STATES, FAILURE, RETRY, SUCCESS, REJECTED, REVOKED, STARTED
from celery import Task as CeleryTask
from celery.worker.request import Request
from celery_once import QueueOnce, AlreadyQueued
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone
from pydash import get

from core.celery import app
from core.common.constants import SUPER_ADMIN_USER_ID
from core.common.utils import get_bulk_import_celery_once_lock_key


class Task(models.Model):
    class Meta:
        db_table = 'celery_tasks'
    STATE_CHOICES = ((state, state) for state in sorted(ALL_STATES))
    id = models.TextField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=1000)
    kwargs = models.JSONField(null=True, blank=True)
    state = models.CharField(max_length=255, default=PENDING, choices=STATE_CHOICES)
    result = models.TextField(null=True, blank=True)
    summary = models.JSONField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    traceback = models.TextField(null=True, blank=True)
    retry = models.IntegerField(default=0)
    queue = models.TextField(default='default')
    created_by = models.ForeignKey(
        'users.UserProfile',
        on_delete=models.CASCADE,
        related_name='async_tasks',
        default=SUPER_ADMIN_USER_ID
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)  # also received at
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    children = ArrayField(models.TextField(), null=True, blank=True, default=list)

    @property
    def json_result(self):
        if self.result:
            try:
                return json.loads(self.result)
            except json.JSONDecodeError:
                return self.result
        return self.result

    def is_finished(self):
        return self.state in (SUCCESS, FAILURE)

    @property
    def user_queue(self):
        parsed_task = self.parse_id() if '~' in self.id else {}
        return parsed_task.get('queue', None)

    @property
    def queue_name(self):
        return self.user_queue or self.queue

    @property
    def runtime(self):
        elapsed_seconds = None
        start_at = self.started_at
        end_at = self.finished_at or self.updated_at
        if start_at and end_at:
            time_difference = end_at - start_at
            elapsed_seconds = time_difference.total_seconds()
        return elapsed_seconds

    @property
    def username(self):
        return self.created_by.username

    @property
    def status(self):
        return self.state

    def record_exception(self, exception):
        error_message = str(exception)
        traceback_info = traceback.format_exc()
        self.error_message = error_message
        self.traceback = traceback_info
        self.save()
        return self

    def save_common(self, args, kwargs, einfo=None):  # pylint: disable=unused-argument
        self.kwargs = kwargs

        if einfo:
            self.record_exception(einfo)
        else:
            self.save()

        return self

    def clean(self):
        if not self.created_by_id and '~' in self.id and '-' in self.id:
            self._set_created_by()
        super().clean()

    def _set_created_by(self):
        info = self.parse_bulk_import_task_id(self.id)
        if info and info.get('username'):
            from core.users.models import UserProfile
            user = UserProfile.objects.filter(username=info.get('username')).first()
            self.created_by_id = user.id or SUPER_ADMIN_USER_ID

    @classmethod
    def before_start(cls, task_id, args, kwargs, name=None):  # pylint: disable=unused-argument
        is_temp = kwargs.pop('permanent', None) is False
        if is_temp:
            return None
        from core.users.models import UserProfile
        task = cls.objects.filter(id=task_id).first()
        if not task:
            if name and 'bulk_import_parts_inline' in name:
                task = cls(id=task_id, state=STARTED)
                task.save()
            else:
                return None
        task.created_by = UserProfile.objects.filter(username=kwargs.pop('username', None)).first() or task.created_by
        task.name = name or task.name
        task.state = STARTED
        task.queue = kwargs.get('queue', None) or task.queue or 'default'
        task.kwargs = kwargs
        task.started_at = timezone.now()
        task.save()
        return task

    @classmethod
    def after_return(cls, status, retval, task_id, args, kwargs, einfo):  # pylint: disable=too-many-arguments
        task = cls.objects.filter(id=task_id).first()
        if not task:
            return None
        task.state = status
        task.result = str(retval) if retval else None
        task.finished_at = timezone.now()
        if isinstance(task.result, Exception):
            task.result = str(task.result)
            task.record_exception(task.result)

        return task.save_common(args, kwargs, einfo)

    @classmethod
    def on_failure(cls, exc, task_id, args, kwargs, einfo):  # pylint: disable=too-many-arguments
        task = cls.objects.filter(id=task_id).exclude(state=REVOKED).first()
        if not task:
            return None
        task.state = FAILURE
        task.finished_at = timezone.now()
        return task.save_common(args, kwargs, einfo or exc)

    @classmethod
    def on_retry(cls, exc, task_id, args, kwargs, einfo):  # pylint: disable=too-many-arguments
        task = cls.objects.filter(id=task_id).first()
        if not task:
            return
        task.retry += 1
        task.state = RETRY
        task.save_common(args, kwargs, einfo or exc)

    @classmethod
    def on_success(cls, retval, task_id, args, kwargs):
        task = cls.objects.filter(id=task_id).first()
        if not task:
            return
        task.result = json.dumps(retval, default=str) if retval else None
        task.state = SUCCESS
        task.finished_at = timezone.now()
        task.save_common(args, kwargs)

    @property
    def child_tasks(self):
        return Task.objects.filter(id__in=self.children)

    def children_still_playing(self):
        return self.child_tasks.exclude(state__in=(SUCCESS, FAILURE, REVOKED))

    def revoke(self):
        result = AsyncResult(self.id)
        for child in self.children_still_playing():
            child.revoke()

        app.control.revoke(self.id, terminate=True, signal='SIGKILL')
        celery_once_key = get_bulk_import_celery_once_lock_key(result)
        if celery_once_key:
            celery_once = QueueOnce()
            celery_once.name = result.name
            celery_once.once_backend.clear_lock(celery_once_key)
        self.state = REVOKED
        self.save()

    def has_access(self, user):
        return user.is_staff or user.id == self.created_by_id

    def parse_id(self):
        return self.parse_bulk_import_task_id(self.id)

    @staticmethod
    def parse_bulk_import_task_id(task_id):
        """
        Used to parse bulk import task id, which is in format '{uuid}-{username}~{queue}'.
        :param task_id:
        :return: dictionary with uuid, username, queue
        """
        task = {'uuid': task_id[:37]}
        username = task_id[37:]
        queue_index = username.find('~')
        if queue_index != -1:
            queue = username[queue_index + 1:]
            username = username[:queue_index]
        else:
            queue = 'default'

        task['username'] = username
        task['queue'] = queue
        return task

    @classmethod
    def new(cls, queue='default', user=None, username=None, import_queue=None, **kwargs):
        if not user and username:
            from core.users.models import UserProfile
            user = UserProfile.objects.filter(username=username).first()
        username = user.username if user else username
        task = cls(
            id=cls.generate_user_task_id(username, import_queue or queue or 'default'),
            created_by=user, queue=queue or 'default', **kwargs)
        task.save()
        return task

    @classmethod
    def generate_user_task_id(cls, username, queue):
        return str(uuid.uuid4()) + '-' + username + '~' + queue

    @staticmethod
    def queue_criteria(queue):
        return models.Q(queue=queue) | models.Q(id__endswith=f'~{queue}')


class WorkerRequest(Request):
    def on_failure(self, exc_info, send_failed_event=True, return_ok=False):
        super().on_failure(exc_info, send_failed_event=send_failed_event, return_ok=return_ok)
        Task.on_failure(exc_info.exception, self.task_id, self.args, self.kwargs, exc_info)


class AsyncTask(CeleryTask):  # pylint: disable=abstract-method
    Request = WorkerRequest

    def on_failure(self, exc, task_id, args, kwargs, einfo):  # pylint: disable=too-many-arguments
        super().on_failure(exc, task_id, args, kwargs, einfo)
        return Task.on_failure(exc, task_id, args, kwargs, einfo)

    def on_success(self, retval, task_id, args, kwargs):
        super().on_success(retval, task_id, args, kwargs)
        return Task.on_success(retval, task_id, args, kwargs)

    def on_retry(self, exc, task_id, args, kwargs, einfo):  # pylint: disable=too-many-arguments
        super().on_retry(exc, task_id, args, kwargs, einfo)
        return Task.on_retry(exc, task_id, args, kwargs, einfo)

    def after_return(self, status, retval, task_id, args, kwargs, einfo):  # pylint: disable=too-many-arguments
        super().after_return(status, retval, task_id, args, kwargs, einfo)
        return Task.after_return(status, retval, task_id, args, kwargs, einfo)

    def before_start(self, task_id, args, kwargs):
        super().before_start(task_id, args, kwargs)
        return Task.before_start(task_id, args, kwargs, self.name)

    def apply_async(self, args=None, kwargs=None, task_id=None, producer=None,  # pylint: disable=too-many-arguments
                    link=None, link_error=None, shadow=None, **options):
        if task_id and self.name and kwargs and kwargs.get('permanent', None) is not False:
            Task.objects.filter(id=task_id).update(name=self.name)
        return super().apply_async(args, kwargs, task_id, producer, link, link_error, shadow, **options)


class QueueOnceCustomTask(QueueOnce, AsyncTask):  # pylint: disable=abstract-method
    def apply_async(self, args=None, kwargs=None, **options):
        task_id = options.get('once', {}).get('task_id', self.once.get('task_id', False))
        try:
            response = super().apply_async(args, kwargs, **options)
            if task_id and get(response, 'state') == REJECTED:
                Task.objects.filter(id=task_id).delete()
            return response
        except AlreadyQueued as e:
            if task_id:
                Task.objects.filter(id=task_id).delete()
            raise e
