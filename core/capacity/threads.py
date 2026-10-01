"""The capacity limit's Redis thread. It imports nothing from Django, so a subprocess test can check process exit."""
import queue
import threading
from concurrent.futures import Future

import redis


class RedisThread:
    """
    One daemon thread per process that runs the capacity limit's Redis operations, so that a caller can stop waiting
    at a deadline: socket timeouts don't bound DNS or a walk through the Sentinels.
    - It's a daemon, so an operation stuck in DNS never holds up a worker's exit. A ThreadPoolExecutor's threads
      would: the interpreter waits for them at exit.
    - Its queue is short. When it's full, a call fails at once instead of waiting behind a stuck operation.
    - An operation whose caller gave up before it started is skipped.
    """
    MAX_QUEUED = 8

    def __init__(self):
        self.jobs = queue.Queue(maxsize=self.MAX_QUEUED)
        self.thread = threading.Thread(target=self.run, name='ocl-capacity-redis', daemon=True)
        self.thread.start()

    def run(self):
        while True:
            future, operation, args = self.jobs.get()
            if not future.set_running_or_notify_cancel():
                continue  # cancelled: its caller stopped waiting before it started
            try:
                future.set_result(operation(*args))
            except Exception as ex:
                future.set_exception(ex)

    def submit(self, operation, *args):
        future = Future()
        try:
            self.jobs.put_nowait((future, operation, args))
        except queue.Full as ex:
            raise redis.ConnectionError('the capacity Redis thread is busy') from ex
        return future
