import atexit
import json
import os
import queue
import sys
import threading

MAX_QUEUED_LINES = 1000
DRAIN_SECONDS = 2


class LineSink:
    """
    A bounded queue of log lines and the daemon thread that prints them. Putting a line never blocks: when the queue
    is full the line is dropped, and the next line that gets through says how many were (`log_dropped`).
    """
    def __init__(self, max_lines):
        self.lines = queue.Queue(maxsize=max_lines)
        self.dropped = 0
        self.pending = 0  # queued or being written
        self.idle = threading.Condition()
        threading.Thread(target=self.run, name='ocl-capacity-log', daemon=True).start()

    def put(self, record):
        with self.idle:
            dropped = self.dropped
            line = json.dumps({key: value for key, value in {**record, 'log_dropped': dropped or None}.items()
                               if value is not None}, separators=(',', ':'), default=str)
            try:
                self.lines.put_nowait(line)
            except queue.Full:
                self.dropped += 1
                return
            self.dropped -= dropped
            self.pending += 1

    def run(self):
        while True:
            line = self.lines.get()
            write(line)
            with self.idle:
                self.pending -= 1
                self.idle.notify_all()

    def drain(self, timeout):
        """Wait until every line put so far is written, for `timeout` at most. True if they all were."""
        with self.idle:
            return self.idle.wait_for(lambda: self.pending == 0, timeout=timeout)


_sink = {'sink': None, 'pid': None}
_lock = threading.Lock()


def get_sink():
    if _sink['pid'] != os.getpid():
        with _lock:
            if _sink['pid'] != os.getpid():  # first use in this process (gunicorn forks workers)
                _sink.update(sink=LineSink(MAX_QUEUED_LINES), pid=os.getpid())
    return _sink['sink']


def emit(record):
    """
    Write one JSON object as one line of the API log, where CloudWatch metric filters match it. The API's other
    timing lines are prints too: gunicorn captures stdout, and the `oclapi` logger has no handler in production.
    Never blocks the call: see LineSink.
    """
    get_sink().put(record)


def write(line):
    try:
        sys.stdout.write(line + '\n')
        sys.stdout.flush()
    except Exception:
        pass


@atexit.register
def drain():
    """
    When a process exits, give the writer DRAIN_SECONDS to finish the lines still queued or in flight: gunicorn
    recycles workers, and `manage.py capacity` exits right after logging its change.
    """
    sink = _sink['sink'] if _sink['pid'] == os.getpid() else None
    if sink:
        sink.drain(DRAIN_SECONDS)
