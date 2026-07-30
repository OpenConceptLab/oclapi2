"""
Verify expansion_url backfill coverage before flipping queries to filter on it.

Compares, per expansion, the true membership count in Postgres against the number of
ES docs carrying that expansion's URI in `expansion_url`. Reports only mismatches, so a
clean run prints nothing but the summary.

Read-only. Safe to run repeatedly while the backfill is in flight.

    python tools/verify_expansion_url_backfill.py
    python tools/verify_expansion_url_backfill.py --resource mappings
    python tools/verify_expansion_url_backfill.py --field expansion_url.keyword  # polluted index
    python tools/verify_expansion_url_backfill.py --requeue-file /tmp/missing.txt

Any expansion listed as SHORT or EMPTY should be re-enqueued:

    Expansion.objects.filter(id__in=ids)  ->  .index_concepts() / .index_mappings()
"""
import argparse
import os
import sys

import psycopg2
import requests

RESOURCES = {
    'concepts': ('concepts', 'collection_expansions_concepts', 'concept_id'),
    'mappings': ('mappings', 'collection_expansions_mappings', 'mapping_id'),
}


def db_counts(conn, table, column):
    """True membership count per expansion, from Postgres."""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT e.id, e.uri, count({column}) '
            f'FROM collection_expansions e '
            f'LEFT JOIN {table} m ON m.expansion_id = e.id '
            f'GROUP BY e.id, e.uri'
        )
        return {uri: (exp_id, count) for exp_id, uri, count in cur.fetchall()}


def es_counts(es_host, index, field):
    """Doc count per expansion URI, from ES, paged with a composite aggregation."""
    counts = {}
    after = None
    while True:
        source = {'uri': {'terms': {'field': field}}}
        agg = {'size': 1000, 'sources': [source]}
        if after:
            agg['after'] = after
        body = {'size': 0, 'aggs': {'by_uri': {'composite': agg}}}
        resp = requests.post(
            f'{es_host}/{index}/_search', json=body, timeout=120,
            headers={'Content-Type': 'application/json'}
        )
        resp.raise_for_status()
        result = resp.json()['aggregations']['by_uri']
        buckets = result.get('buckets', [])
        for bucket in buckets:
            counts[bucket['key']['uri']] = bucket['doc_count']
        after = result.get('after_key')
        if not after or not buckets:
            return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resource', choices=sorted(RESOURCES), default='concepts')
    parser.add_argument('--field', default='expansion_url',
                        help='ES field path; use expansion_url.keyword against a text-mapped index')
    parser.add_argument('--es-host', default=os.environ.get('ES_HOST_URL', 'http://localhost:9200'))
    parser.add_argument('--requeue-file', help='write ids needing a re-index, one per line')
    args = parser.parse_args()

    index, table, column = RESOURCES[args.resource]

    conn = psycopg2.connect(
        dbname=os.environ.get('DB', 'postgres'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'Postgres123'),
        host=os.environ.get('DB_HOST', 'localhost'),
        port=os.environ.get('DB_PORT', 5432),
    )
    try:
        expected = db_counts(conn, table, column)
    finally:
        conn.close()

    actual = es_counts(args.es_host, index, args.field)

    empty, short, over, stale, ok, skipped = [], [], [], [], 0, 0
    for uri, (exp_id, want) in sorted(expected.items()):
        got = actual.get(uri, 0)
        if want == 0:
            # Expansion has no members, so nothing should carry its URI. Anything here is
            # a leftover the append-only painless script cannot remove.
            if got:
                stale.append((exp_id, uri, want, got))
            else:
                skipped += 1
            continue
        if got == 0:
            empty.append((exp_id, uri, want, got))
        elif got < want:
            short.append((exp_id, uri, want, got))
        elif got > want:
            over.append((exp_id, uri, want, got))
        else:
            ok += 1

    for label, rows in (('EMPTY', empty), ('SHORT', short), ('OVER', over), ('STALE', stale)):
        for exp_id, uri, want, got in rows:
            print(f'{label:5}  id={exp_id:<8} want={want:<8} got={got:<8} {uri}')

    orphans = sorted(set(actual) - set(expected))
    for uri in orphans:
        print(f'ORPHAN id=?        want=0        got={actual[uri]:<8} {uri}')

    total = ok + len(empty) + len(short) + len(over)
    print(
        f'\n{args.resource}: {ok}/{total} expansions fully backfilled  '
        f'(empty={len(empty)} short={len(short)} over={len(over)} '
        f'stale={len(stale)} orphan={len(orphans)}; {skipped} with no members, ignored)'
    )

    if args.requeue_file:
        ids = [str(r[0]) for r in empty + short]
        with open(args.requeue_file, 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(ids))
        print(f'wrote {len(ids)} expansion id(s) needing re-index to {args.requeue_file}')

    # OVER means expansion_url holds URIs no longer backed by membership rows -- stale
    # entries the append-only painless script cannot remove. Worth investigating, but it
    # does not cause the empty-results symptom, so don't fail the gate on it.
    return 1 if (empty or short) else 0


if __name__ == '__main__':
    sys.exit(main())
