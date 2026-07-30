# -*- coding: utf-8 -*-
"""
Backfill `expansion_url` expansion by expansion, in priority order.

Three sets, processed in order, each ordered by created_at DESC (newest first):

    SET 1  default expansions   -- expansion.uri == collection_version.expansion_uri
    SET 2  created in last 6 months (and not default)
    SET 3  everything else

The sets partition every expansion exactly once. After each set it logs picked / done /
left, so at any point you know what has been covered and what has not.

Usage (inside the api container, or any env with DJANGO_SETTINGS_MODULE set):
    python tools/backfill_expansion_url_ordered.py --dry-run
    python tools/backfill_expansion_url_ordered.py
    python tools/backfill_expansion_url_ordered.py --set 1 --skip-complete
    python tools/backfill_expansion_url_ordered.py --months 3 --log-file /tmp/backfill.log

Exits non-zero if anything was left incomplete.
"""
import argparse
import os
import sys
import time
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

import django  # noqa: E402
django.setup()

from django.db.models import F, Q  # noqa: E402
from django.utils import timezone  # noqa: E402
from elasticsearch_dsl.connections import connections  # noqa: E402

from core.collections.models import Expansion  # noqa: E402
from core.common.es import ESScript  # noqa: E402
from core.concepts.documents import ConceptDocument  # noqa: E402
from core.mappings.documents import MappingDocument  # noqa: E402

FIELD = 'expansion_url'
RESOURCES = (
    # (related manager, ES index, document, prefetch, select_related)
    ('concepts', 'concepts', ConceptDocument, ['sources', 'names', 'descriptions'],
     ['parent', 'parent__organization', 'parent__user', 'created_by', 'updated_by']),
    ('mappings', 'mappings', MappingDocument, ['sources'],
     ['parent', 'parent__organization', 'parent__user', 'created_by', 'updated_by']),
)

LOG = None


def log(line=''):
    print(line, flush=True)
    if LOG:
        LOG.write(line + '\n')
        LOG.flush()


def es_count(index, uri):
    return connections.get_connection().count(index=index, query={'term': {FIELD: uri}})['count']


def assert_keyword_mapping():
    """A wrongly-typed or missing mapping cannot be fixed later without a full reindex."""
    es = connections.get_connection()
    for _, index, _, _, _ in RESOURCES:
        mapping = es.indices.get_field_mapping(
            index=index, fields=FIELD).get(index, {}).get('mappings', {})
        if FIELD not in mapping:
            sys.exit(
                f"ABORT: index '{index}' has no '{FIELD}' mapping. PUT it as keyword first, "
                f'or dynamic mapping will create text+keyword, which cannot be changed or removed.'
            )
        definition = mapping[FIELD]['mapping'][FIELD]
        if definition.get('type') != 'keyword' or 'fields' in definition:
            sys.exit(
                f"ABORT: {index}.{FIELD} is {definition!r}, expected keyword. "
                f'A field type cannot be changed in place -- the index needs recreating.'
            )
        log(f'  mapping OK: {index}.{FIELD} = keyword')


def append_only(expansion, queryset, index, refresh=True):
    """Append this expansion's collection fields to docs ALREADY in ES.

    Same painless script batch_index uses, but without the
    full_index_missing_docs_or_raise fallback -- so docs absent from ES are left alone
    instead of being full-indexed (which refetches from the DB and recomputes embeddings).
    The script is idempotent: it only adds values not already present.
    """
    from elasticsearch.helpers import bulk  # noqa: PLC0415

    params = expansion._get_resources_index_collection_fields()  # pylint: disable=protected-access
    # Materialise ids, then stream actions. Expansions can hold 100k+ members, so building
    # the full action list would balloon memory.
    ids = list(queryset.values_list('id', flat=True))
    if not ids:
        return

    def actions():
        for resource_id in ids:
            yield {
                '_op_type': 'update', '_index': index, '_id': resource_id, 'retry_on_conflict': 3,
                'script': {'source': ESScript.APPEND_COLLECTION_FIELDS_SCRIPT, 'params': params},
            }

    es = connections.get_connection()
    bulk(es, actions(), chunk_size=2000,
         raise_on_error=False, raise_on_exception=False, refresh=False)
    if refresh:
        # Forces visibility so the inline verify count can read these writes. On a
        # multi-node cluster this is a synchronous fan-out across every shard copy and
        # costs ~1s+, which at two per expansion dominates the whole run -- see
        # --no-refresh, which drops it and verifies in one pass at the end instead.
        es.indices.refresh(index=index)


def build_sets(months):
    """Three querysets that partition every expansion, each newest-first."""
    is_default = Q(uri=F('collection_version__expansion_uri'))
    cutoff = timezone.now() - timedelta(days=months * 30)
    recent = Q(created_at__gte=cutoff)
    order = F('created_at').desc(nulls_last=True)

    return [
        ('SET 1  default expansions',
         Expansion.objects.filter(is_default).order_by(order)),
        (f'SET 2  created in last {months} months',
         Expansion.objects.filter(recent).exclude(is_default).order_by(order)),
        ('SET 3  the rest',
         Expansion.objects.exclude(recent).exclude(is_default).order_by(order)),
    ]


def do_expansion(expansion, args):
    """Index and verify one expansion.

    Returns (status, detail) where status is:
      EMPTY -- no concepts and no mappings, nothing to index (NOT the same as done)
      DONE  -- every resource with members has ES count >= Postgres count
      WROTE -- --no-refresh: written but NOT verified here; use the gate script
      LEFT  -- at least one resource is short, or errored
    """
    done, detail, had_work, wrote_only = True, [], False, False
    for related, index, document, prefetch, select_related in RESOURCES:
        queryset = getattr(expansion, related)
        want = queryset.count()
        if not want:
            continue
        had_work = True
        before = es_count(index, expansion.uri)
        if args.skip_complete and before >= want:
            detail.append(f'{related}={before}/{want} skip')
            continue
        if not args.dry_run:
            try:
                if args.skip_missing_docs:
                    append_only(expansion, queryset, index, refresh=not args.no_refresh)
                else:
                    expansion.batch_index(
                        queryset, document, prefetch=prefetch, select_related=select_related)
            except Exception as exc:  # pylint: disable=broad-except
                detail.append(f'{related} ERROR {exc.__class__.__name__}: {exc}')
                done = False
                continue
        if args.no_refresh and not args.dry_run:
            # Without a forced refresh the post-write count reads stale, so don't pretend
            # to verify. Report what was written and let the gate script check coverage in
            # one pass at the end.
            wrote_only = True
            detail.append(f'{related} wrote {want}')
            continue
        got = before if args.dry_run else es_count(index, expansion.uri)
        detail.append(f'{related}={got}/{want}')
        if got < want:
            done = False
    if not had_work:
        return 'EMPTY', 'no concepts, no mappings'
    if not done:
        return 'LEFT', ', '.join(detail)
    return ('WROTE' if wrote_only else 'DONE'), ', '.join(detail)


def run_set(name, queryset, args):
    """Process one set, logging picked / done / left."""
    picked = queryset.count()
    log(f'\n{name}')
    log(f'  picked : {picked:,}')
    if not picked:
        log('  empty  : 0\n  done   : 0\n  left   : 0')
        return 0, 0, 0

    if args.limit:
        queryset = queryset[:args.limit]
        log(f'  limited to {args.limit:,}')

    # Materialise ids and close the query immediately. Do NOT use queryset.iterator():
    # it holds a Postgres server-side named cursor open for the whole run, and a single
    # long expansion (100k+ members) is enough for the connection to be recycled, which
    # kills the cursor with InvalidCursorName partway through.
    ids = list(queryset.values_list('id', flat=True))

    done = left = empty = wrote = seen = 0
    started = time.time()
    for expansion_id in ids:
        expansion = Expansion.objects.filter(id=expansion_id).first()
        if expansion is None:
            continue          # deleted while we were running
        seen += 1
        status, detail = do_expansion(expansion, args)
        if status == 'EMPTY':
            empty += 1
        elif status == 'DONE':
            done += 1
        elif status == 'WROTE':
            wrote += 1
        else:
            left += 1
            log(f'  LEFT   id={expansion.id:<8} {detail}  {expansion.uri}')
        if seen % args.report_every == 0:
            rate = seen / max(time.time() - started, 0.001)
            log(
                f'  ...{seen:,}/{picked:,}  done={done:,} wrote={wrote:,} left={left:,} '
                f'empty={empty:,}  {rate:.1f}/s  '
                f'eta {(picked - seen) / max(rate, 0.001) / 60:.0f}m'
            )

    elapsed = time.time() - started
    log(f'  empty  : {empty:,}   (no members -- nothing to index)')
    log(f'  done   : {done:,}   (verified: ES count >= Postgres count)')
    if wrote:
        log(f'  wrote  : {wrote:,}   (--no-refresh: NOT verified here -- run the gate script)')
    log(f'  left   : {left:,}')
    log(f'  time   : {elapsed / 60:.1f}m ({elapsed:.1f}s)')
    return done, left, empty


def main():
    global LOG  # pylint: disable=global-statement
    parser = argparse.ArgumentParser()
    parser.add_argument('--set', type=int, choices=[1, 2, 3], help='run only this set')
    parser.add_argument('--months', type=int, default=6, help='recency window for set 2')
    parser.add_argument('--dry-run', action='store_true', help='report only, no indexing')
    parser.add_argument('--skip-complete', action='store_true',
                        help='skip resources whose ES count already matches Postgres')
    parser.add_argument('--skip-missing-docs', action='store_true',
                        help='only append to docs already in ES; do not full-index missing '
                             'ones (no embedding recompute). Docs absent from ES stay absent, '
                             'so those expansions report LEFT -- that is drift, not failure.')
    parser.add_argument('--no-refresh', action='store_true',
                        help='skip the forced per-expansion ES refresh AND the inline verify '
                             'count. On a multi-node cluster the refresh is a synchronous '
                             'fan-out costing ~1s+, two per expansion, which dominates the '
                             'run. Expansions report WROTE instead of DONE -- verify coverage '
                             'afterwards with the gate script. Requires --skip-missing-docs.')
    parser.add_argument('--limit', type=int, default=0, help='cap expansions per set')
    parser.add_argument('--report-every', type=int, default=200)
    parser.add_argument('--log-file', help='tee output to this file')
    args = parser.parse_args()

    if args.log_file:
        LOG = open(args.log_file, 'a', encoding='utf-8')  # pylint: disable=consider-using-with

    if args.no_refresh and not args.skip_missing_docs:
        sys.exit('--no-refresh requires --skip-missing-docs (it only affects the append path).')

    started = time.time()
    log(f'=== expansion_url backfill{" [DRY RUN]" if args.dry_run else ""}'
        f'{" [NO-REFRESH: verify with the gate script]" if args.no_refresh else ""} '
        f'at {timezone.now().isoformat()} ===')
    assert_keyword_mapping()

    sets = build_sets(args.months)
    total = Expansion.objects.count()
    counts = [(name, qs.count()) for name, qs in sets]
    log(f'\n{total:,} expansion(s) total')
    for name, count in counts:
        log(f'  {name:40} {count:>8,}')
    covered = sum(count for _, count in counts)
    if covered != total:
        log(f'  WARNING sets cover {covered:,} of {total:,} -- {total - covered:,} unaccounted')

    if args.set:
        sets = [sets[args.set - 1]]

    grand_done = grand_left = grand_empty = 0
    for name, queryset in sets:
        done, left, empty = run_set(name, queryset, args)
        grand_done += done
        grand_left += left
        grand_empty += empty

    elapsed = time.time() - started
    log(f'\n=== TOTAL  done={grand_done:,}  left={grand_left:,}  '
        f'empty={grand_empty:,}  {elapsed / 60:.1f}m ===')
    log(f'    {grand_done + grand_left:,} expansion(s) had members and needed indexing; '
        f'{grand_empty:,} had none.')
    if grand_left:
        log('left > 0 -- do NOT flip the query filter yet; re-run for the LEFT ids above.')
    if LOG:
        LOG.close()
    return 1 if grand_left else 0


if __name__ == '__main__':
    sys.exit(main())
