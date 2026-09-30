import json


def emit(record):
    """
    Write one JSON object as one line of the API log, where CloudWatch metric filters match it. The API's other
    timing lines are prints too: gunicorn captures stdout, and the `oclapi` logger has no handler in production.
    """
    print(json.dumps({key: value for key, value in record.items() if value is not None},
                     separators=(',', ':'), default=str), flush=True)
