"""
Read-only: for every code that has two versioned objects in a HEAD (the #297 "twins"), compare the stored checksums of
the two copies' latest versions, so you can see which twin pairs are the same concept stored twice.

    tools/run_via_bastion.sh tools/compare_twin_concept_checksums.py --head-id 32 --csv twins.csv

Per pair it reports, from `concepts.checksums` (standard and smart):
  same_both      standard and smart both match: the copies are the same concept content
  same_smart     only the smart checksum matches
  same_standard  only the standard checksum matches
  differ         neither matches
  missing        a copy has no stored checksum, so nothing can be said
"stale" is the twin whose latest version is older (created_at, then id), "current" the other. The CSV lists both
versioned object ids so a later script can retire the stale tree for the `same_both` pairs.

The checksums cover the concept's own fields (names, class, datatype, extras and so on). They do not cover
hierarchy or mappings, so equal checksums say nothing about those.
"""
import argparse
import csv
import os
import sys
from collections import Counter

import psycopg2
import psycopg2.extensions
import psycopg2.extras

TWINS_SQL = """
SELECT v.mnemonic, v.id FROM concepts v
WHERE v.parent_id = %(head_id)s AND v.id = v.versioned_object_id
  AND v.mnemonic IN (SELECT mnemonic FROM concepts WHERE parent_id = %(head_id)s AND id = versioned_object_id
                     GROUP BY mnemonic HAVING count(*) = 2)
"""

LATEST_SQL = """
SELECT DISTINCT ON (c.versioned_object_id) c.versioned_object_id AS vo, c.id AS version_id, c.created_at,
       c.checksums ->> 'standard' AS standard, c.checksums ->> 'smart' AS smart, c.retired
FROM concepts c
WHERE c.versioned_object_id = ANY(%(ids)s) AND c.id <> c.versioned_object_id AND c.is_latest_version
ORDER BY c.versioned_object_id, c.created_at DESC, c.id DESC
"""

# lets Ctrl-C interrupt a query that is still running on the server
psycopg2.extensions.set_wait_callback(psycopg2.extras.wait_select)


ALL_VERSIONS_SQL = """
SELECT c.versioned_object_id AS vo, c.created_at, c.checksums ->> 'standard' AS standard, c.checksums ->> 'smart' AS smart
FROM concepts c WHERE c.versioned_object_id = ANY(%(ids)s) AND c.id <> c.versioned_object_id
"""


def pg_connect():
    return psycopg2.connect(
        dbname=os.environ.get('DB', 'postgres'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'Postgres123'),
        host=os.environ.get('DB_HOST', 'localhost'),
        port=os.environ.get('DB_PORT', 5432),
    )


def classify(stale, current):
    if not (stale['standard'] and stale['smart'] and current['standard'] and current['smart']):
        return 'missing'
    same_standard = stale['standard'] == current['standard']
    same_smart = stale['smart'] == current['smart']
    if same_standard and same_smart:
        return 'same_both'
    if same_smart:
        return 'same_smart'
    if same_standard:
        return 'same_standard'
    return 'differ'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--head-id', type=int, required=True)
    parser.add_argument('--csv', help='write one row per twin pair to this file')
    args = parser.parse_args()

    conn = pg_connect()
    with conn.cursor() as cur:
        cur.execute('SELECT version FROM sources WHERE id = %s', (args.head_id,))
        found = cur.fetchone()
        if not found or found[0] != 'HEAD':
            sys.exit(f'source {args.head_id} is not a HEAD')
        cur.execute(TWINS_SQL, {'head_id': args.head_id})
        twin_vos = cur.fetchall()
        print(f'twin versioned objects: {len(twin_vos)}', flush=True)
        latest = {}
        ids = [vo for _, vo in twin_vos]
        for start in range(0, len(ids), 1000):
            cur.execute(LATEST_SQL, {'ids': ids[start:start + 1000]})
            cols = [d[0] for d in cur.description]
            for r in cur.fetchall():
                row = dict(zip(cols, r))
                latest[row['vo']] = row
            print(f'  latest versions: {min(start + 1000, len(ids))}/{len(ids)}', flush=True)
        history = {}
        for start in range(0, len(ids), 1000):
            cur.execute(ALL_VERSIONS_SQL, {'ids': ids[start:start + 1000]})
            for vo, created_at, standard, smart in cur.fetchall():
                history.setdefault(vo, []).append((created_at, standard, smart))
    rows = []
    for mnemonic, vo in twin_vos:
        row = latest.get(vo) or {'vo': vo, 'version_id': None, 'created_at': None,
                                 'standard': None, 'smart': None, 'retired': None}
        rows.append({'mnemonic': mnemonic, **row})
    rows.sort(key=lambda r: (r['mnemonic'], r['created_at'] is None, r['created_at'] or 0, r['version_id'] or 0))

    groups = {}
    for row in rows:
        groups.setdefault(row['mnemonic'], []).append(row)
    pairs, odd = [], 0
    for mnemonic, members in groups.items():
        if len(members) != 2 or any(m['version_id'] is None for m in members):
            odd += 1
            continue
        stale, current = members  # oldest latest version first
        in_history = [h for h in history.get(current['vo'], [])
                      if h[1] and h[2] and h[1] == stale['standard'] and h[2] == stale['smart']]
        pairs.append({
            'mnemonic': mnemonic, 'result': classify(stale, current),
            'stale_vo': stale['vo'], 'current_vo': current['vo'],
            'stale_version': stale['version_id'], 'current_version': current['version_id'],
            'stale_created': stale['created_at'], 'current_created': current['created_at'],
            'stale_retired': stale['retired'], 'current_retired': current['retired'],
            'stale_equals_a_current_version': bool(in_history),
            'current_versions': len(history.get(current['vo'], [])),
        })

    counts = Counter(p['result'] for p in pairs)
    print(f'twin pairs: {len(pairs)} (not comparable: {odd})')
    for key in ('same_both', 'same_smart', 'same_standard', 'differ', 'missing'):
        print(f'  {key}: {counts.get(key, 0)}')
    print(f'stale latest equals SOME version of the current copy: {sum(1 for p in pairs if p["stale_equals_a_current_version"])}')
    print(f'stale copy retired: {sum(1 for p in pairs if p["stale_retired"])}, '
          f'current copy retired: {sum(1 for p in pairs if p["current_retired"])}')
    if args.csv:
        with open(args.csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=list(pairs[0]))
            writer.writeheader()
            writer.writerows(pairs)
        print(f'written to {args.csv}')


if __name__ == '__main__':
    main()
