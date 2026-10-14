"""
Leave exactly one `is_latest_version` concept version per versioned object in a source (OpenConceptLab/ocl_online#297,
part B): the newest by (created_at, id) stays latest, every older row still flagged latest is unflagged.

Only the flag moves. No rows are deleted, `updated_at` and the concept's content are untouched, and the ES docs get a
partial update of `is_latest_version` alone, so no embeddings are recomputed. A version pinned by an expansion,
a collection reference or another source version stays valid: it just stops claiming to be the latest.

Dry-run is the default and writes nothing. It prints the plan and, for every row it would unflag, whether the row is
used by an expansion, a collection reference and/or a source version other than HEAD.

    # plan and usage report, no writes
    python tools/fix_duplicate_latest_concepts.py --source-id 32 --csv plan.csv

    # apply (re-checks inside each transaction; safe to re-run, a second run finds nothing)
    python tools/fix_duplicate_latest_concepts.py --source-id 32 --apply --expect-rows 388

    # leave rows that an expansion or reference uses for a person to look at
    python tools/fix_duplicate_latest_concepts.py --source-id 32 --apply --skip-used

Connection comes from DB, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT and ES_HOSTS / ES_SCHEME / ES_HOST / ES_PORT, as in
tools/backfill_expansion_url.py. Run it after the #297 twin fix, so the "newest" row is chosen among kept VOs only.
"""
import argparse
import csv
import os
import sys

import psycopg2
from elasticsearch import Elasticsearch
from elasticsearch.helpers import parallel_bulk

PLAN_SQL = """
WITH latest AS (
    SELECT c.id, c.versioned_object_id, c.mnemonic, c.version, c.created_at,
           ROW_NUMBER() OVER (PARTITION BY c.versioned_object_id ORDER BY c.created_at DESC, c.id DESC) AS rn
    FROM concepts c
    WHERE c.parent_id = %(source_id)s AND c.is_latest_version AND c.id <> c.versioned_object_id
), dup_vos AS (
    SELECT versioned_object_id FROM latest GROUP BY versioned_object_id HAVING count(*) > 1
)
SELECT l.id, l.versioned_object_id, l.mnemonic, l.version, l.created_at,
       EXISTS (SELECT 1 FROM collection_expansions_concepts x WHERE x.concept_id = l.id) AS in_expansion,
       EXISTS (SELECT 1 FROM collections_referencedconcept r WHERE r.concept_id = l.id) AS in_reference,
       (SELECT count(*) FROM concepts_sources cs WHERE cs.concept_id = l.id AND cs.source_id <> %(source_id)s) AS in_other_sources,
       (SELECT k.id FROM latest k WHERE k.versioned_object_id = l.versioned_object_id AND k.rn = 1) AS keep_id
FROM latest l
JOIN dup_vos d ON d.versioned_object_id = l.versioned_object_id
WHERE l.rn > 1
ORDER BY l.versioned_object_id, l.created_at
"""

UNFLAG_SQL = """
UPDATE concepts c SET is_latest_version = false
WHERE c.id = ANY(%(ids)s) AND c.is_latest_version AND c.parent_id = %(source_id)s
  AND EXISTS (SELECT 1 FROM concepts n
              WHERE n.versioned_object_id = c.versioned_object_id AND n.id <> n.versioned_object_id
                AND n.is_latest_version AND (n.created_at, n.id) > (c.created_at, c.id))
RETURNING c.id
"""


def es_client():
    hosts = os.environ.get('ES_HOSTS')
    scheme = os.environ.get('ES_SCHEME', 'http')
    if hosts:
        urls = [h if '://' in h else f'{scheme}://{h}' for h in hosts.split(',')]
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


def load_plan(conn, source_id):
    with conn.cursor() as cur:
        cur.execute(PLAN_SQL, {'source_id': source_id})
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def summarise(plan):
    vos = {r['versioned_object_id'] for r in plan}
    used = [r for r in plan if r['in_expansion'] or r['in_reference'] or r['in_other_sources']]
    print(f'versioned objects with several latest rows: {len(vos)}')
    print(f'rows to unflag: {len(plan)}')
    print(f'  in an expansion: {sum(1 for r in plan if r["in_expansion"])}')
    print(f'  in a collection reference: {sum(1 for r in plan if r["in_reference"])}')
    print(f'  in another source version: {sum(1 for r in plan if r["in_other_sources"])}')
    print(f'  used by any of these: {len(used)}')


def unflag(conn, source_id, ids, batch_size):
    done = []
    for start in range(0, len(ids), batch_size):
        batch = ids[start:start + batch_size]
        with conn:  # one transaction per batch
            with conn.cursor() as cur:
                cur.execute(UNFLAG_SQL, {'ids': batch, 'source_id': source_id})
                done.extend(row[0] for row in cur.fetchall())
        print(f'  db: {len(done)}/{len(ids)} unflagged')
    return done


def sync_es(es, index, ids):
    actions = ({'_op_type': 'update', '_index': index, '_id': i, 'retry_on_conflict': 5,
                'doc': {'is_latest_version': 'false'}} for i in ids)
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
    parser.add_argument('--source-id', type=int, required=True, help='HEAD source id (LOINC: 32 on Staging)')
    parser.add_argument('--apply', action='store_true', help='write; without it nothing changes')
    parser.add_argument('--skip-used', action='store_true', help='leave rows used by an expansion or reference')
    parser.add_argument('--expect-rows', type=int, help='abort unless the plan has exactly this many rows')
    parser.add_argument('--index', default='concepts', help='ES index or alias (default: concepts)')
    parser.add_argument('--batch-size', type=int, default=500)
    parser.add_argument('--csv', help='write the plan, with usage, to this file')
    parser.add_argument('--no-es', action='store_true', help='skip the ES update (run a reindex yourself)')
    args = parser.parse_args()

    conn = pg_connect()
    plan = load_plan(conn, args.source_id)
    summarise(plan)
    if args.csv and plan:
        with open(args.csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=list(plan[0]))
            writer.writeheader()
            writer.writerows(plan)
        print(f'plan written to {args.csv}')
    if args.expect_rows is not None and len(plan) != args.expect_rows:
        sys.exit(f'aborting: plan has {len(plan)} rows, expected {args.expect_rows}')
    if not args.apply:
        print('dry run: nothing written (use --apply)')
        return

    todo = [r for r in plan if not (args.skip_used and (r['in_expansion'] or r['in_reference']))]
    if len(todo) != len(plan):
        print(f'skipping {len(plan) - len(todo)} used rows')
    done = unflag(conn, args.source_id, [r['id'] for r in todo], args.batch_size)
    remaining = len(load_plan(conn, args.source_id))
    print(f'unflagged {len(done)} rows; plan now has {remaining} rows')
    if done and not args.no_es:
        failed = sync_es(es_client(), args.index, done)
        if failed:
            sys.exit(f'{failed} ES updates failed: re-run the ES step by hand for those ids')


if __name__ == '__main__':
    main()
