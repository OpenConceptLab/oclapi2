"""
Take the concept rows that tools/fix_duplicate_latest_concepts.py unflagged out of HEAD's membership
(OpenConceptLab/ocl_online#297, part B: the "388" rows).

It reads the ids from that script's plan CSV (column `id`) and, for each, checks it is still safe:
  - the row belongs to the source and is a member of HEAD (concepts_sources),
  - it is no longer flagged latest,
  - a newer row of the same versioned object is flagged latest AND is a member of HEAD, so the concept stays in HEAD.
Rows failing a check are listed and left alone. Only the HEAD membership rows are deleted: the concept rows, their
hierarchy, mappings and every other version's membership stay as they are. Dry-run is the default.

    tools/run_via_bastion.sh tools/remove_stale_latest_from_head.py --head-id 32 --ids-csv plan.csv
    tools/run_via_bastion.sh tools/remove_stale_latest_from_head.py --head-id 32 --ids-csv plan.csv --apply --expect-rows 388

Afterwards recalculate HEAD's counts (update_children_counts on the Source). With --no-es the ES docs are left for the
HEAD reindex; otherwise the `HEAD` label is removed from each doc's `source_version`.
"""
import argparse
import csv
import os
import sys

import psycopg2
import psycopg2.extensions
import psycopg2.extras
from elasticsearch import Elasticsearch
from elasticsearch.helpers import parallel_bulk

CHECK_SQL = """
SELECT c.id, c.mnemonic, c.versioned_object_id, c.is_latest_version, c.retired, c.created_at,
       EXISTS (SELECT 1 FROM concepts_sources cs WHERE cs.concept_id = c.id AND cs.source_id = %(head_id)s) AS in_head,
       (c.parent_id = %(head_id)s) AS in_source,
       EXISTS (SELECT 1 FROM concepts n JOIN concepts_sources ns ON ns.concept_id = n.id AND ns.source_id = %(head_id)s
               WHERE n.versioned_object_id = c.versioned_object_id AND n.id <> n.versioned_object_id
                 AND n.is_latest_version AND (n.created_at, n.id) > (c.created_at, c.id)) AS newer_latest_in_head
FROM concepts c WHERE c.id = ANY(%(ids)s)
"""

REMOVE_SQL = "DELETE FROM concepts_sources WHERE source_id = %(head_id)s AND concept_id = ANY(%(ids)s)"
ES_REMOVE_HEAD = "if (ctx._source.source_version != null) { ctx._source.source_version.removeIf(v -> v == 'HEAD') }"


def es_client():
    hosts = os.environ.get('ES_HOSTS')
    scheme = os.environ.get('ES_SCHEME', 'http')
    if hosts:
        urls = [h if '://' in h else f'{scheme}://{h}' for h in hosts.split(',')]
    else:
        urls = [f"{scheme}://{os.environ.get('ES_HOST', 'localhost')}:{os.environ.get('ES_PORT', '9200')}"]
    return Elasticsearch(hosts=urls, request_timeout=120, retry_on_timeout=True, max_retries=3)

# lets Ctrl-C interrupt a query that is still running on the server
psycopg2.extensions.set_wait_callback(psycopg2.extras.wait_select)


def pg_connect():
    return psycopg2.connect(
        dbname=os.environ.get('DB', 'postgres'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'Postgres123'),
        host=os.environ.get('DB_HOST', 'localhost'),
        port=os.environ.get('DB_PORT', 5432),
    )


def check(conn, head_id, ids):
    with conn.cursor() as cur:
        cur.execute(CHECK_SQL, {'head_id': head_id, 'ids': ids})
        cols = [d[0] for d in cur.description]
        found = {row[0]: dict(zip(cols, row)) for row in map(tuple, cur.fetchall())}
    ok, problems = [], []
    for i in ids:
        row = found.get(i)
        if not row:
            problems.append((i, 'row not found'))
        elif not row['in_source']:
            problems.append((i, 'not a concept of this source'))
        elif row['is_latest_version']:
            problems.append((i, 'still flagged latest'))
        elif not row['newer_latest_in_head']:
            problems.append((i, 'no newer latest row of this concept in HEAD: removing would drop the concept'))
        elif not row['in_head']:
            problems.append((i, 'already not in HEAD'))
        else:
            ok.append(row)
    return ok, problems


def sync_es(es, index, ids):
    actions = ({'_op_type': 'update', '_index': index, '_id': i, 'retry_on_conflict': 5,
                'script': {'lang': 'painless', 'source': ES_REMOVE_HEAD}} for i in ids)
    ok = missing = failed = 0
    for success, info in parallel_bulk(es, actions, thread_count=4, chunk_size=500, raise_on_error=False):
        if success:
            ok += 1
        elif info.get('update', {}).get('status') == 404:
            missing += 1
        else:
            failed += 1
            print(f'  es failure: {info}', file=sys.stderr)
    print(f'  es: updated {ok}, not in index {missing}, failed {failed}')
    return failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--head-id', type=int, required=True)
    parser.add_argument('--ids-csv', required=True, help="the plan CSV of fix_duplicate_latest_concepts.py (column 'id')")
    parser.add_argument('--apply', action='store_true', help='write; without it nothing changes')
    parser.add_argument('--expect-rows', type=int, help='abort unless exactly this many rows pass the checks')
    parser.add_argument('--index', default='concepts', help='ES index or alias (default: concepts)')
    parser.add_argument('--batch-size', type=int, default=500)
    parser.add_argument('--no-es', action='store_true', help='leave the ES docs to the HEAD reindex')
    args = parser.parse_args()

    with open(args.ids_csv, newline='', encoding='utf-8') as f:
        ids = sorted({int(row['id']) for row in csv.DictReader(f)})
    conn = pg_connect()
    with conn.cursor() as cur:
        cur.execute('SELECT version FROM sources WHERE id = %s', (args.head_id,))
        found = cur.fetchone()
    if not found or found[0] != 'HEAD':
        sys.exit(f'source {args.head_id} is not a HEAD')

    ok, problems = check(conn, args.head_id, ids)
    print(f'ids in CSV: {len(ids)}; safe to remove from HEAD: {len(ok)}; left alone: {len(problems)}')
    print(f'  retired among them: {sum(1 for r in ok if r["retired"])}')
    for i, why in problems[:20]:
        print(f'  {i}: {why}')
    if args.expect_rows is not None and len(ok) != args.expect_rows:
        sys.exit(f'aborting: {len(ok)} rows pass, expected {args.expect_rows}')
    if not args.apply:
        print('dry run: nothing written (use --apply)')
        return

    safe = [r['id'] for r in ok]
    removed = 0
    for start in range(0, len(safe), args.batch_size):
        batch = safe[start:start + args.batch_size]
        with conn:  # one transaction per batch
            with conn.cursor() as cur:
                cur.execute(REMOVE_SQL, {'head_id': args.head_id, 'ids': batch})
                removed += cur.rowcount
        print(f'  db: {removed}/{len(safe)} removed from HEAD')
    if safe and not args.no_es:
        failed = sync_es(es_client(), args.index, safe)
        if failed:
            sys.exit(f'{failed} ES updates failed: a re-run will not retry them, fix those ids by hand')
    print('now recalculate HEAD counts (update_children_counts on the Source)')


if __name__ == '__main__':
    main()
