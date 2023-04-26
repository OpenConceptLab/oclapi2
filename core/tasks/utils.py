import time

from celery.result import AsyncResult

from core.tasks.constants import TASK_NOT_COMPLETED


def wait_until_task_complete(task_id, wait_time=20):
    """Wait until task finishes"""
    start_time = time.monotonic()
    while True:
        task = AsyncResult(task_id)
        if task.ready():
            return task.get()
        elapsed_time = time.monotonic() - start_time
        if elapsed_time >= wait_time:
            return TASK_NOT_COMPLETED
        time.sleep(0.5)
