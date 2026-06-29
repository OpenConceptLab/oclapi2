"""
Benchmarks the default (non-S3) bulk import path against a sample file,
reporting wall-clock time and total DB query count.

Runs inside a transaction that is always rolled back at the end, so it is
safe to run against a real dev DB -- nothing it creates is kept.

Usage (inside the api container, or any env with DJANGO_SETTINGS_MODULE set):
    python tools/benchmark_bulk_import.py [path/to/sample.json] [--username ocladmin]

Defaults to core/samples/pepfar_datim_moh_fy19.json (413 mixed concept/mapping/
reference rows) since it's already exercised by core/importers/tests.py and
needs no extra fixtures beyond the 'ocladmin' user that ships with every env.

To compare old vs. new behavior, run this once on the current code, then
`git stash` the importer changes and run it again, e.g.:
    python tools/benchmark_bulk_import.py            # with changes
    git stash && python tools/benchmark_bulk_import.py && git stash pop  # baseline
"""
import argparse
import os
import sys
import time
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

import django  # noqa: E402  pylint: disable=wrong-import-position

django.setup()

from django.db import transaction  # noqa: E402  pylint: disable=wrong-import-position
from django.test.utils import CaptureQueriesContext  # noqa: E402  pylint: disable=wrong-import-position
from django.db import connection  # noqa: E402  pylint: disable=wrong-import-position

from core.importers.models import BulkImportInline  # noqa: E402  pylint: disable=wrong-import-position

DEFAULT_SAMPLE = os.path.join(
    os.path.dirname(__file__), '..', 'core', 'samples', 'pepfar_datim_moh_fy19.json'
)


class RollbackTransaction(Exception):
    pass


def run_import(content, username):
    # Django's per-connection query log is capped at 9000 entries (deque maxlen);
    # bulk imports blow past that easily, which would silently truncate the count.
    connection.queries_log = deque(maxlen=10_000_000)
    importer = BulkImportInline(content, username, True)
    with CaptureQueriesContext(connection) as queries_ctx:
        start = time.perf_counter()
        importer.run()
        elapsed = time.perf_counter() - start
    return importer, elapsed, len(queries_ctx.captured_queries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sample', nargs='?', default=DEFAULT_SAMPLE)
    parser.add_argument('--username', default='ocladmin')
    args = parser.parse_args()

    with open(args.sample, 'r') as file:
        content = file.read()

    print(f"Sample: {args.sample}")
    print(f"Username: {args.username}")
    print("Running import inside a transaction that will be rolled back...")

    importer = None
    elapsed = None
    query_count = None
    try:
        with transaction.atomic():
            importer, elapsed, query_count = run_import(content, args.username)
            raise RollbackTransaction()
    except RollbackTransaction:
        pass

    print()
    print("==== Result ====")
    print(f"processed:          {importer.processed}")
    print(f"created:             {len(importer.created)}")
    print(f"updated:             {len(importer.updated)}")
    print(f"unchanged:           {len(importer.unchanged)}")
    print(f"invalid:             {len(importer.invalid)}")
    print(f"failed:              {len(importer.failed)}")
    print(f"permission_denied:   {len(importer.permission_denied)}")
    print()
    print("==== Performance ====")
    print(f"wall_time_seconds:   {elapsed:.4f}")
    print(f"db_query_count:      {query_count}")
    if importer.processed:
        print(f"queries_per_item:    {query_count / importer.processed:.2f}")
        print(f"ms_per_item:         {(elapsed * 1000) / importer.processed:.3f}")


if __name__ == '__main__':
    sys.exit(main())
