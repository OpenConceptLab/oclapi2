import atexit
import json
import os
import queue
import sys
import threading

MAX_QUEUED_LINES = 1000

_state = {'queue': None, 'pid': None, 'dropped': 0}
_lock = threading.Lock()


def emit(record):
    """
    Write one JSON object as one line of the API log, where CloudWatch metric filters match it. The API's other
    timing lines are prints too: gunicorn captures stdout, and the `oclapi` logger has no handler in production.

    Never blocks the request: the line goes on a bounded queue that a writer thread prints, so a backed-up log sink
    can't hold a call. When the queue is full the line is dropped, and the next line that gets through says how
    many were (`log_dropped`).
    """
    dropped = _state['dropped']
    if dropped:
        record = {**record, 'log_dropped': dropped}
    line = json.dumps({key: value for key, value in record.items() if value is not None},
                      separators=(',', ':'), default=str)
    try:
        get_queue().put_nowait(line)
        _state['dropped'] -= dropped
    except queue.Full:
        _state['dropped'] += 1


def get_queue():
    if _state['pid'] != os.getpid():
        with _lock:
            if _state['pid'] != os.getpid():  # first use in this process (gunicorn forks workers)
                lines = queue.Queue(maxsize=MAX_QUEUED_LINES)
                threading.Thread(target=write_lines, args=(lines,), name='ocl-capacity-log', daemon=True).start()
                _state.update(queue=lines, pid=os.getpid(), dropped=0)
    return _state['queue']


def write_lines(lines):
    while True:
        write(lines.get())


def write(line):
    try:
        sys.stdout.write(line + '\n')
        sys.stdout.flush()
    except Exception:
        pass


@atexit.register
def drain():
    """Print what's still queued when a worker exits (gunicorn recycles them)."""
    lines = _state['queue'] if _state['pid'] == os.getpid() else None
    while lines is not None:
        try:
            write(lines.get_nowait())
        except queue.Empty:
            break
