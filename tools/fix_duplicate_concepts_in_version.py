"""
Remove duplicate concept rows from ONE repo version (OpenConceptLab/ocl_online#297, part A): a mnemonic that the
version holds twice, once from each "twin" versioned object.

Keeper per pair:
  1. the row used by a collection expansion or a collection reference, when only one of the two is;
  2. otherwise the newest by (created_at, id).
A pair where BOTH rows are used is not touched (listed as a conflict) unless --include-both-used.
Groups that are not exactly a pair are never touched.

Apply removes the loser row from the version's membership (concepts_sources) and its version label from the ES doc's
`source_version`. It does not delete the concept row, and it does not touch HEAD or any other version: the twin
versioned object is dealt with separately. Dry-run is the default and writes nothing; for every loser it reports
the other things that point at it (expansions, references, mappings either way, hierarchy links, other versions),
so nothing is dropped unseen.

    python tools/fix_duplicate_concepts_in_version.py --head-id 32 --version-id 6885 --csv plan.csv
    python tools/fix_duplicate_concepts_in_version.py --head-id 32 --version-id 6885 --apply --skip-linked --expect-pairs <n from the dry run>

After applying, recalculate the version's counts and checksums (update_children_counts, get_checksums(recalculate=True)
on the Source), as they still describe the old membership.

Connection comes from DB, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT and ES_HOSTS / ES_SCHEME / ES_HOST / ES_PORT, as in
tools/backfill_expansion_url.py.
"""
import argparse
import csv
import os
import sys
import time

import psycopg2
import psycopg2.extensions
import psycopg2.extras
from elasticsearch import Elasticsearch
from elasticsearch.helpers import parallel_bulk

PAIRS_SQL = """
WITH members AS (
    SELECT c.id, c.mnemonic, c.versioned_object_id, c.created_at, c.retired, v.retired AS vo_retired,
           EXISTS (SELECT 1 FROM collection_expansions_concepts x WHERE x.concept_id = c.id) AS in_expansion,
           EXISTS (SELECT 1 FROM collections_referencedconcept r WHERE r.concept_id = c.id) AS in_reference
    FROM concepts_sources cs JOIN concepts c ON c.id = cs.concept_id
    JOIN concepts v ON v.id = c.versioned_object_id
    WHERE cs.source_id = %(version_id)s AND c.parent_id = %(head_id)s
), dups AS (
    SELECT mnemonic FROM members GROUP BY mnemonic HAVING count(*) > 1
)
SELECT m.mnemonic, m.id, m.versioned_object_id, m.created_at, m.retired, m.vo_retired, m.in_expansion, m.in_reference,
       count(*) OVER (PARTITION BY m.mnemonic) AS group_size
FROM members m JOIN dups d ON d.mnemonic = m.mnemonic
ORDER BY m.mnemonic, m.created_at DESC, m.id DESC
"""

USAGE_SQL = """
SELECT c.id,
       (SELECT count(*) FROM mappings m WHERE m.from_concept_id = c.id OR m.from_concept_id = c.versioned_object_id) AS mappings_from,
       (SELECT count(*) FROM mappings m WHERE m.to_concept_id = c.id OR m.to_concept_id = c.versioned_object_id) AS mappings_to,
       (SELECT count(*) FROM concepts_hierarchicalconcepts h WHERE h.parent_id = c.id) AS hierarchy_as_parent,
       (SELECT count(*) FROM concepts_hierarchicalconcepts h WHERE h.child_id = c.id) AS hierarchy_as_child,
       (SELECT count(*) FROM concepts_sources s WHERE s.concept_id = c.id AND s.source_id <> %(version_id)s) AS other_sources
FROM concepts c WHERE c.id = ANY(%(ids)s)
"""

REMOVE_SQL = "DELETE FROM concepts_sources WHERE source_id = %(version_id)s AND concept_id = ANY(%(ids)s)"

ES_REMOVE_VERSION = (
    "if (ctx._source.source_version != null) { ctx._source.source_version.removeIf(v -> v == params.v) }"
)


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


def query(conn, sql, params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


LINKS_HIERARCHY_SQL = """
SELECT h.child_id, h.parent_id, cc.mnemonic AS child_mn, pc.mnemonic AS parent_mn
FROM concepts_hierarchicalconcepts h
JOIN concepts cc ON cc.id = h.child_id JOIN concepts pc ON pc.id = h.parent_id
WHERE h.child_id = ANY(%(ids)s)
UNION
SELECT h.child_id, h.parent_id, cc.mnemonic, pc.mnemonic
FROM concepts_hierarchicalconcepts h
JOIN concepts cc ON cc.id = h.child_id JOIN concepts pc ON pc.id = h.parent_id
WHERE h.parent_id = ANY(%(ids)s)
"""

LINKS_MAPPINGS_SQL = """
SELECT m.map_type, m.from_concept_id, m.to_concept_id, fc.mnemonic AS from_mn, tc.mnemonic AS to_mn
FROM mappings m
JOIN concepts fc ON fc.id = m.from_concept_id JOIN concepts tc ON tc.id = m.to_concept_id
WHERE m.from_concept_id = ANY(%(ids)s) AND m.is_latest_version
UNION
SELECT m.map_type, m.from_concept_id, m.to_concept_id, fc.mnemonic, tc.mnemonic
FROM mappings m
JOIN concepts fc ON fc.id = m.from_concept_id JOIN concepts tc ON tc.id = m.to_concept_id
WHERE m.to_concept_id = ANY(%(ids)s) AND m.is_latest_version
"""


def link_signatures(conn, rows, batch_size=300):
    """
    What hangs off each row, by the code on the other end so a twin on that end counts as the same code:
    its hierarchy parents and children, and the latest mappings into and out of it (matched on the row or its VO).
    Done in batches with progress, since the mappings table is large.
    """
    keys = {}
    for row in rows:
        keys.setdefault(row['id'], row['id'])
        keys.setdefault(row['versioned_object_id'], row['id'])
    signatures = {row['id']: set() for row in rows}
    ids = list(keys)
    started = time.time()
    for start in range(0, len(ids), batch_size):
        batch = ids[start:start + batch_size]
        for e in query(conn, LINKS_HIERARCHY_SQL, {'ids': batch}):
            if e['child_id'] in keys:
                signatures[keys[e['child_id']]].add(('parent', e['parent_mn']))
            if e['parent_id'] in keys:
                signatures[keys[e['parent_id']]].add(('child', e['child_mn']))
        for m in query(conn, LINKS_MAPPINGS_SQL, {'ids': batch}):
            if m['to_concept_id'] in keys:
                signatures[keys[m['to_concept_id']]].add(('map_in', m['map_type'], m['from_mn']))
            if m['from_concept_id'] in keys:
                signatures[keys[m['from_concept_id']]].add(('map_out', m['map_type'], m['to_mn']))
        done = min(start + batch_size, len(ids))
        print(f'  links: {done}/{len(ids)} ids, {time.time() - started:.0f}s', flush=True)
    return signatures


def choose_by_coverage(conn, decided, allow_swap=True):
    """
    decided: (mnemonic, keep, lose, reason). The newer row is kept when it carries everything the older one does.
    Otherwise the older row is kept when it carries everything the newer one does. Neither: the pair is left alone.
    A row chosen because an expansion or reference uses it is never swapped.
    """
    members = [m for _, keep, lose, _ in decided for m in (keep, lose)]
    sigs = link_signatures(conn, members)
    chosen, uncovered, swapped = [], [], 0
    for mnemonic, keep, lose, reason in decided:
        keep_covers = sigs[lose['id']] <= sigs[keep['id']]
        if keep_covers or reason == 'keep the used one':
            chosen.append((mnemonic, keep, lose, reason if keep_covers else reason + ', links not covered'))
        elif allow_swap and sigs[keep['id']] <= sigs[lose['id']]:
            chosen.append((mnemonic, lose, keep, 'older row covers the links, newer does not'))
            swapped += 1
        else:
            uncovered.append(mnemonic)
    return chosen, uncovered, swapped


def build_plan(conn, head_id, version_id, include_both_used, skip_linked=False, cover_aware=False, allow_swap=True,
               prefer_active=False, keep_active_vo=False):
    rows = query(conn, PAIRS_SQL, {'head_id': head_id, 'version_id': version_id})
    groups = {}
    for row in rows:
        groups.setdefault(row['mnemonic'], []).append(row)
    decided, conflicts, skipped_groups = [], [], []
    for mnemonic, members in groups.items():
        if len(members) != 2:
            skipped_groups.append(mnemonic)
            continue
        newest, other = members  # ordered newest first
        used = [bool(m['in_expansion'] or m['in_reference']) for m in members]
        if all(used) and not include_both_used:
            conflicts.append(mnemonic)
            continue
        if keep_active_vo and newest['vo_retired'] != other['vo_retired']:
            keep, lose = (other, newest) if newest['vo_retired'] else (newest, other)
            decided.append((mnemonic, keep, lose, 'keep the row of the active VO'))
        elif prefer_active and newest['retired'] != other['retired']:
            keep, lose = (other, newest) if newest['retired'] else (newest, other)
            decided.append((mnemonic, keep, lose, 'keep the active one'))
        elif used[1] and not used[0]:
            decided.append((mnemonic, other, newest, 'keep the used one'))
        else:
            decided.append((mnemonic, newest, other, 'both used, keep newest' if all(used) else 'keep newest'))
    uncovered, swapped = [], 0
    if cover_aware and decided:
        decided, uncovered, swapped = choose_by_coverage(conn, decided, allow_swap)
        print(f'coverage: {swapped} pairs flipped to keep the older row, {len(uncovered)} pairs where neither covers the other')
    plan = [{'mnemonic': mnemonic, 'keep_id': keep['id'], 'lose_id': lose['id'],
             'keep_vo': keep['versioned_object_id'], 'lose_vo': lose['versioned_object_id'],
             'keep_created': keep['created_at'], 'lose_created': lose['created_at'],
             'lose_in_expansion': lose['in_expansion'], 'lose_in_reference': lose['in_reference'],
             'reason': reason} for mnemonic, keep, lose, reason in decided]
    usage = {u['id']: u for u in query(conn, USAGE_SQL, {'ids': [p['lose_id'] for p in plan], 'version_id': version_id})} \
        if plan else {}
    for p in plan:
        u = usage.get(p['lose_id'], {})
        p.update({k: u.get(k, 0) for k in ('mappings_from', 'mappings_to', 'hierarchy_as_parent', 'hierarchy_as_child', 'other_sources')})
    if skip_linked:
        linked = [p for p in plan if p['mappings_from'] or p['mappings_to'] or p['hierarchy_as_parent']]
        print(f'skipping {len(linked)} pairs whose removed row is a mapping end or a hierarchy parent')
        plan = [p for p in plan if p not in linked]
    return plan, conflicts + uncovered, skipped_groups


def summarise(plan, conflicts, skipped_groups):
    print(f'pairs to resolve: {len(plan)}')
    print(f'  keep the used one: {sum(1 for p in plan if p["reason"] == "keep the used one")}')
    print(f'  keep newest: {sum(1 for p in plan if p["reason"] != "keep the used one")}')
    print(f'pairs left alone (both rows used, or neither row covers links of the other): {len(conflicts)}')
    print(f'groups that are not a pair (left alone): {len(skipped_groups)}')
    print('the row to remove is also:')
    print(f'  in an expansion: {sum(1 for p in plan if p["lose_in_expansion"])}')
    print(f'  in a collection reference: {sum(1 for p in plan if p["lose_in_reference"])}')
    print(f'  a mapping source: {sum(1 for p in plan if p["mappings_from"])}')
    print(f'  a mapping target: {sum(1 for p in plan if p["mappings_to"])}')
    print(f'  a hierarchy parent of other concepts: {sum(1 for p in plan if p["hierarchy_as_parent"])}')
    print(f'  a hierarchy child (its own parent links): {sum(1 for p in plan if p["hierarchy_as_child"])}')
    print(f'  in another version too: {sum(1 for p in plan if p["other_sources"])}')


def remove_members(conn, version_id, ids, batch_size):
    removed = 0
    for start in range(0, len(ids), batch_size):
        batch = ids[start:start + batch_size]
        with conn:  # one transaction per batch
            with conn.cursor() as cur:
                cur.execute(REMOVE_SQL, {'version_id': version_id, 'ids': batch})
                removed += cur.rowcount
        print(f'  db: {removed}/{len(ids)} memberships removed')
    return removed


def sync_es(es, index, ids, version_label):
    actions = ({'_op_type': 'update', '_index': index, '_id': i, 'retry_on_conflict': 5,
                'script': {'lang': 'painless', 'source': ES_REMOVE_VERSION, 'params': {'v': version_label}}}
               for i in ids)
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
    parser.add_argument('--head-id', type=int, required=True, help='HEAD source id (LOINC: 32)')
    parser.add_argument('--version-id', type=int, required=True, help='the repo version to clean (LOINC 2.81: 6885 on Staging)')
    parser.add_argument('--apply', action='store_true', help='write; without it nothing changes')
    parser.add_argument('--include-both-used', action='store_true', help='also resolve pairs where both rows are used (keeps newest)')
    parser.add_argument('--skip-linked', action='store_true',
                        help='leave pairs whose removed row is a mapping source/target or a hierarchy parent')
    parser.add_argument('--cover-aware', action='store_true',
                        help='keep the newer row only if it has all the older one\'s hierarchy and mappings, else the older')
    parser.add_argument('--no-swap', action='store_true',
                        help='with --cover-aware, never flip to the older row: such pairs are left alone')
    parser.add_argument('--keep-active-vo', action='store_true',
                        help='when one VO of a pair is retired and the other is not, keep the active VO\'s row')
    parser.add_argument('--prefer-active', action='store_true',
                        help='when one row of a pair is retired and the other is not, keep the active one')
    parser.add_argument('--expect-pairs', type=int, help='abort unless the plan has exactly this many pairs')
    parser.add_argument('--index', default='concepts', help='ES index or alias (default: concepts)')
    parser.add_argument('--batch-size', type=int, default=500)
    parser.add_argument('--csv', help='write the plan, with usage, to this file')
    parser.add_argument('--no-es', action='store_true', help='skip the ES update (reindex yourself)')
    args = parser.parse_args()

    conn = pg_connect()
    with conn.cursor() as cur:
        cur.execute(
            'SELECT id, version, mnemonic, organization_id, user_id FROM sources WHERE id IN (%s, %s)',
            (args.version_id, args.head_id))
        found = {row[0]: row for row in cur.fetchall()}
    if args.version_id not in found or args.head_id not in found:
        sys.exit('version or head source not found')
    head, version = found[args.head_id], found[args.version_id]
    if head[1] != 'HEAD':
        sys.exit(f'--head-id {args.head_id} is version {head[1]}, not HEAD')
    if version[1] == 'HEAD':
        sys.exit('refusing to run on a HEAD: pass a released version id')
    if version[2:] != head[2:]:
        sys.exit(f'source {args.version_id} is not a version of source {args.head_id} (mnemonic/owner differ)')
    version_label = version[1]
    print(f'version {args.version_id} = {version_label}')

    plan, conflicts, skipped_groups = build_plan(conn, args.head_id, args.version_id, args.include_both_used, args.skip_linked, args.cover_aware, not args.no_swap, args.prefer_active, args.keep_active_vo)
    summarise(plan, conflicts, skipped_groups)
    if args.csv and plan:
        with open(args.csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=list(plan[0]))
            writer.writeheader()
            writer.writerows(plan)
        print(f'plan written to {args.csv}')
    if args.expect_pairs is not None and len(plan) != args.expect_pairs:
        sys.exit(f'aborting: plan has {len(plan)} pairs, expected {args.expect_pairs}')
    if not args.apply:
        print('dry run: nothing written (use --apply)')
        return

    ids = [p['lose_id'] for p in plan]
    removed = remove_members(conn, args.version_id, ids, args.batch_size)
    remaining = len(build_plan(conn, args.head_id, args.version_id, args.include_both_used, args.skip_linked, args.cover_aware, not args.no_swap, args.prefer_active, args.keep_active_vo)[0])
    print(f'removed {removed} memberships; plan now has {remaining} pairs')
    if ids and not args.no_es:
        failed = sync_es(es_client(), args.index, ids, version_label)
        if failed:
            sys.exit(f'{failed} ES updates failed: a re-run will not retry them, fix those ids by hand')
    print('now recalculate the version counts and checksums (see the docstring)')


if __name__ == '__main__':
    main()
