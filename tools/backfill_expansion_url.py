"""
Backfill `expansion_url` on the concepts and mappings ES indices.

Drives the whole backfill from ONE ordered SQL join instead of per-expansion Celery tasks:

    SELECT ec.concept_id, e.uri FROM collection_expansions_concepts ec
    JOIN collection_expansions e ON e.id = ec.expansion_id ORDER BY ec.concept_id

Consecutive rows are grouped per document, so each doc gets exactly ONE bulk update
carrying its complete set of expansion URIs. That is ~4x fewer ES operations than the
per-expansion path (which issues one update per expansion x doc pair), and it skips
Celery, the Task table, the per-expansion COUNT(*), and the AlreadyQueued dedup that can
silently drop an enqueue.

It writes the field with a plain partial-doc merge rather than the painless
APPEND_COLLECTION_FIELDS_SCRIPT -- no script compilation or execution per op -- and
because the URI set is computed complete from SQL, the write is authoritative:
re-runnable, and it clears stale URIs instead of only appending.

REQUIRES the `expansion_url` mapping to already exist as `keyword` on both indices.
The script refuses to run otherwise, since a dynamic mapping would create text+keyword
and that cannot be undone without a full reindex.

    # size the job first -- no writes
    python tools/backfill_expansion_url.py --stats

    # run it
    python tools/backfill_expansion_url.py --resource both --tune

    # resume after an interruption
    python tools/backfill_expansion_url.py --resource concepts --start-after 10564395

Docs absent from ES are reported as `missing` and skipped, NOT full-indexed -- the
full-index fallback in the app recomputes embeddings and is far more expensive than this
whole backfill.
"""
import argparse
import os
import sys
import time
from itertools import groupby

import psycopg2
from elasticsearch import Elasticsearch
from elasticsearch.helpers import parallel_bulk

RESOURCES = {
    'concepts': ('concepts', 'collection_expansions_concepts', 'concept_id'),
    'mappings': ('mappings', 'collection_expansions_mappings', 'mapping_id'),
}
FIELD = 'expansion_url'


def es_client():
    hosts = os.environ.get('ES_HOSTS')
    scheme = os.environ.get('ES_SCHEME', 'http')
    if hosts:
        urls = [f'{scheme}://{host}' for host in hosts.split(',')]
    else:
        urls = [f"{scheme}://{os.environ.get('ES_HOST', 'localhost')}:{os.environ.get('ES_PORT', '9200')}"]
    return Elasticsearch(hosts=urls, request_timeout=120, retry_on_timeout=True, max_retries=3)


def pg_connect():
    return psycopg2.connect(
        dbname=os.environ.get('DB', 'postgres'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'Postgres123'),
        host=os.environ.get('DB_HOST', 'localhost'),
        port=os.environ.get('DB_PORT', 5432),
    )


def assert_keyword_mapping(es, index):
    """Refuse to write into a wrongly-typed or absent mapping -- it is unrecoverable."""
    resp = es.indices.get_field_mapping(index=index, fields=FIELD)
    mapping = resp.get(index, {}).get('mappings', {})
    if FIELD not in mapping:
        sys.exit(
            f"ABORT: {index} has no '{FIELD}' mapping. PUT it as keyword first:\n"
            f'  curl -X PUT "$ES/{index}/_mapping" -H "Content-Type: application/json" '
            f'-d \'{{"properties":{{"{FIELD}":{{"type":"keyword"}}}}}}\'\n'
            f'Running without it lets dynamic mapping create text+keyword, which cannot '
            f'be changed or deleted afterwards.'
        )
    definition = mapping[FIELD]['mapping'][FIELD]
    if definition.get('type') != 'keyword' or 'fields' in definition:
        sys.exit(
            f"ABORT: {index}.{FIELD} is {definition!r}, expected {{'type': 'keyword'}}.\n"
            f'This index needs recreating -- a field type cannot be changed in place.'
        )


def row_stream(conn, table, column, start_after):
    """Server-side cursor streaming (doc_id, uri) ordered by doc_id."""
    cursor = conn.cursor(name=f'backfill_{table}')
    cursor.itersize = 20000
    where = f'WHERE m.{column} > {int(start_after)}' if start_after else ''
    cursor.execute(
        f'SELECT m.{column}, e.uri '
        f'FROM {table} m JOIN collection_expansions e ON e.id = m.expansion_id '
        f'{where} ORDER BY m.{column}'
    )
    return cursor


def actions(cursor, index, progress, limit=0):
    for doc_id, rows in groupby(cursor, key=lambda row: row[0]):
        if limit and progress['docs'] >= limit:
            return
        urls = sorted({row[1] for row in rows if row[1]})
        progress['docs'] += 1
        progress['last_id'] = doc_id
        progress['urls'] += len(urls)
        yield {
            '_op_type': 'update',
            '_index': index,
            '_id': doc_id,
            'retry_on_conflict': 5,
            'doc': {FIELD: urls},
        }


def tune(es, index, on):
    """refresh_interval=-1 during ingest is the single biggest ES-side win."""
    settings = {'refresh_interval': -1 if on else None}
    es.indices.put_settings(index=index, settings=settings)
    print(f'  [tune] {index} refresh_interval -> {"-1 (disabled)" if on else "default"}')


def run(es, conn, resource, args):
    index, table, column = RESOURCES[resource]
    assert_keyword_mapping(es, index)

    progress = {'docs': 0, 'urls': 0, 'last_id': args.start_after or 0}
    stats = {'ok': 0, 'missing': 0, 'failed': 0, 'confirmed_id': 0}
    started = time.time()
    print(f'\n=== {resource} -> index "{index}" ===')

    if args.tune:
        tune(es, index, True)
    cursor = row_stream(conn, table, column, args.start_after)
    try:
        for ok, info in parallel_bulk(
            es, actions(cursor, index, progress, args.limit),
            thread_count=args.threads, chunk_size=args.chunk_size, queue_size=args.threads * 2,
            raise_on_error=False, raise_on_exception=False,
        ):
            result = info.get('update') or {}
            if ok:
                stats['ok'] += 1
            else:
                if result.get('status') == 404:
                    stats['missing'] += 1
                else:
                    stats['failed'] += 1
                    if stats['failed'] <= 10:
                        print(f'  FAIL {info}', file=sys.stderr)
            # Watermark from CONFIRMED results, not the generator -- the generator runs
            # well ahead of the bulk consumer, so its position would skip queued docs.
            # Still only exact with --threads 1, since chunks confirm out of order.
            doc_id = int(result.get('_id') or 0)
            if doc_id > stats['confirmed_id']:
                stats['confirmed_id'] = doc_id
            done = stats['ok'] + stats['missing'] + stats['failed']
            if done % args.report_every == 0:
                rate = done / max(time.time() - started, 0.001)
                print(
                    f'  {done:>9,} docs  {rate:>8,.0f}/s  updated={stats["ok"]:,} '
                    f'missing={stats["missing"]:,} failed={stats["failed"]:,} '
                    f'confirmed_id={stats["confirmed_id"]}', flush=True
                )
    finally:
        cursor.close()
        if args.tune:
            tune(es, index, False)
            es.indices.refresh(index=index)

    elapsed = time.time() - started
    attempted_total = stats['ok'] + stats['missing'] + stats['failed']
    print(
        f'  DONE {resource}: updated={stats["ok"]:,} missing={stats["missing"]:,} '
        f'failed={stats["failed"]:,} | {progress["urls"]:,} uri rows across '
        f'{progress["docs"]:,} docs | {elapsed:,.1f}s'
    )
    # Report both: 404s take a much cheaper path than real writes, so an overall rate on a
    # drifted index flatters the result. Extrapolate to prod from the WRITE rate.
    print(
        f'       {attempted_total / max(elapsed, 0.001):,.0f} ops/s attempted, '
        f'{stats["ok"] / max(elapsed, 0.001):,.0f} writes/s '
        f'(extrapolate prod runtime from writes/s)'
    )
    attempted = stats['ok'] + stats['missing'] + stats['failed']
    if stats['missing'] and attempted:
        share = stats['missing'] / attempted
        print(
            f'  NOTE {stats["missing"]:,} docs ({share:.1%}) are in the expansion join table '
            f'but absent from ES.'
        )
        if share > 0.01:
            print(
                '       That is pre-existing index drift, not caused by this backfill -- '
                'those docs\n'
                '       are missing from search entirely. Worth investigating separately; '
                'they cannot\n'
                '       get expansion_url until they are indexed.'
            )
    if stats['failed']:
        print('  ^ non-404 failures present -- re-run before flipping the query filter.')
    return stats['failed'] == 0


def show_stats(es, conn):
    """Sizing numbers per environment. Read-only."""
    print(f'{"resource":10} {"uri rows":>12} {"docs to update":>15} {"es docs":>12} {"already":>12}')
    with conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM collection_expansions')
        expansions = cur.fetchone()[0]
        for resource, (index, table, column) in sorted(RESOURCES.items()):
            cur.execute(f'SELECT count(*), count(DISTINCT {column}) FROM {table}')
            rows, docs = cur.fetchone()
            try:
                total = es.count(index=index)['count']
                have = es.count(index=index, query={'exists': {'field': FIELD}})['count']
            except Exception as exc:  # pylint: disable=broad-except
                total, have = f'ERR', f'{exc.__class__.__name__}'
            print(f'{resource:10} {rows:>12,} {docs:>15,} {total:>12,} {have:>12,}')
    print(f'\nexpansions: {expansions:,}')
    print('"docs to update" is the number of bulk ops this script issues (one per doc).')
    print('"uri rows" is what the per-expansion Celery path would issue instead.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resource', choices=sorted(RESOURCES) + ['both'], default='both')
    parser.add_argument('--stats', action='store_true', help='print sizing numbers and exit')
    parser.add_argument('--threads', type=int, default=8)
    parser.add_argument('--chunk-size', type=int, default=2000)
    parser.add_argument('--report-every', type=int, default=50000)
    parser.add_argument('--limit', type=int, default=0,
                        help='stop after this many docs -- measure throughput on a bounded slice')
    parser.add_argument('--start-after', type=int, default=0,
                        help='resume: skip docs with id <= this (use last_id from output)')
    parser.add_argument('--tune', action='store_true',
                        help='disable refresh_interval during ingest, restore after. '
                             'Much faster, but delays search visibility on a live index.')
    args = parser.parse_args()

    es, conn = es_client(), pg_connect()
    try:
        if args.stats:
            show_stats(es, conn)
            return 0
        resources = sorted(RESOURCES) if args.resource == 'both' else [args.resource]
        clean = all(run(es, conn, resource, args) for resource in resources)
        print('\nNext: verify coverage before flipping the query filter --')
        print('  python tools/verify_expansion_url_backfill.py --resource concepts')
        print('  python tools/verify_expansion_url_backfill.py --resource mappings')
        return 0 if clean else 1
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
