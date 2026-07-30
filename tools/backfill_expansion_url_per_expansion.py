# -*- coding: utf-8 -*-
"""
Backfill `expansion_url` one expansion at a time, verifying each before moving on.

Trades throughput for operational certainty. Each expansion is a self-contained unit:
index it, immediately re-count it against Postgres, record the outcome. When the run ends
you have a per-expansion answer to "is this indexed or not", not an aggregate.

Runs `Expansion.batch_index` SYNCHRONOUSLY rather than through Celery -- the same
production code path, so behaviour matches normal indexing exactly, but without the
`AlreadyQueued` dedup in `Expansion.index_concepts`, which catches the exception and
deletes the Task row, silently dropping the enqueue and leaving nothing to audit.

Because `batch_index` appends via the painless script, docs absent from ES get
full-indexed by `full_index_missing_docs_or_raise`. That repairs index drift, but it
recomputes embeddings and is by far the slowest part of a run. `--skip-missing-docs`
leaves drift alone and only appends to docs already in ES.

Contrast with tools/backfill_expansion_url.py, which drives one grouped SQL join and is
~7x fewer ES ops but gives no per-expansion completion state.

Usage (inside the api container, or any env with DJANGO_SETTINGS_MODULE set):
    python tools/backfill_expansion_url_per_expansion.py --dry-run
    python tools/backfill_expansion_url_per_expansion.py --skip-complete \\
        --state-file /tmp/backfill.jsonl
    python tools/backfill_expansion_url_per_expansion.py --skip-complete --start-id 63455

Exits non-zero if any expansion finished incomplete, so it can gate a deploy step.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

import django  # noqa: E402
django.setup()

from elasticsearch_dsl.connections import connections  # noqa: E402

from core.collections.models import Expansion  # noqa: E402
from core.common.es import ESScript  # noqa: E402
from core.concepts.documents import ConceptDocument  # noqa: E402
from core.mappings.documents import MappingDocument  # noqa: E402

FIELD = 'expansion_url'

SPECS = {
    'concepts': {
        'document': ConceptDocument,
        'index': 'concepts',
        'related': 'concepts',
        'prefetch': ['sources', 'names', 'descriptions'],
        'select_related': ['parent', 'parent__organization', 'parent__user', 'created_by', 'updated_by'],
    },
    'mappings': {
        'document': MappingDocument,
        'index': 'mappings',
        'related': 'mappings',
        'prefetch': ['sources'],
        'select_related': ['parent', 'parent__organization', 'parent__user', 'created_by', 'updated_by'],
    },
}
STATUS_KEY = {'OK': 'ok', 'SHORT': 'short', 'EMPTY': 'empty', 'ERROR': 'error'}


def es_count(index, uri):
    return connections.get_connection().count(index=index, query={'term': {FIELD: uri}})['count']


def assert_keyword_mapping(index):
    """Refuse to index into a wrongly-typed or absent mapping -- it is unrecoverable."""
    es = connections.get_connection()
    mapping = es.indices.get_field_mapping(index=index, fields=FIELD).get(index, {}).get('mappings', {})
    if FIELD not in mapping:
        sys.exit(
            f"ABORT: {index} has no '{FIELD}' mapping. PUT it as keyword BEFORE indexing:\n"
            f'  curl -X PUT "$ES/{index}/_mapping" -H "Content-Type: application/json" '
            f'-d \'{{"properties":{{"{FIELD}":{{"type":"keyword"}}}}}}\'\n'
            f'Indexing first lets dynamic mapping create text+keyword, which cannot be '
            f'changed or deleted afterwards.'
        )
    definition = mapping[FIELD]['mapping'][FIELD]
    if definition.get('type') != 'keyword' or 'fields' in definition:
        sys.exit(
            f'ABORT: {index}.{FIELD} is {definition!r}, expected keyword.\n'
            f'A field type cannot be changed in place -- this index needs recreating.'
        )


def append_only(expansion, queryset, index):
    """batch_index without the full-index fallback for docs missing from ES."""
    from elasticsearch.helpers import bulk

    params = expansion._get_resources_index_collection_fields()  # pylint: disable=protected-access
    actions = [
        {
            '_op_type': 'update', '_index': index, '_id': resource_id, 'retry_on_conflict': 3,
            'script': {'source': ESScript.APPEND_COLLECTION_FIELDS_SCRIPT, 'params': params},
        }
        for resource_id in queryset.values_list('id', flat=True)
    ]
    if actions:
        bulk(connections.get_connection(), actions,
             raise_on_error=False, raise_on_exception=False, refresh=True)


def process(expansion, resource, args):
    """Index one expansion's resources, then verify. Returns (status, record)."""
    spec = SPECS[resource]
    want = getattr(expansion, spec['related']).count()
    if not want:
        return 'NONE', None

    got_before = es_count(spec['index'], expansion.uri)
    if args.skip_complete and got_before >= want:
        return 'DONE', None

    began = time.time()
    error = None
    if not args.dry_run:
        try:
            queryset = getattr(expansion, spec['related'])
            if args.skip_missing_docs:
                append_only(expansion, queryset, spec['index'])
            else:
                expansion.batch_index(
                    queryset, spec['document'],
                    prefetch=spec['prefetch'], select_related=spec['select_related'],
                )
        except Exception as exc:  # pylint: disable=broad-except
            error = f'{exc.__class__.__name__}: {exc}'

    got = got_before if args.dry_run else es_count(spec['index'], expansion.uri)
    if error:
        status = 'ERROR'
    elif got >= want:
        status = 'OK'
    elif got:
        status = 'SHORT'
    else:
        status = 'EMPTY'

    return status, {
        'expansion_id': expansion.id, 'uri': expansion.uri, 'resource': resource,
        'want': want, 'got_before': got_before, 'got': got, 'status': status,
        'seconds': round(time.time() - began, 2), 'error': error,
    }


def main():  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
    parser = argparse.ArgumentParser()
    parser.add_argument('--resource', choices=sorted(SPECS) + ['both'], default='both')
    parser.add_argument('--start-id', type=int, default=0, help='resume from this expansion id')
    parser.add_argument('--limit', type=int, default=0, help='process at most N expansions')
    parser.add_argument('--dry-run', action='store_true', help='report only, no indexing')
    parser.add_argument('--skip-complete', action='store_true',
                        help='skip expansions whose ES count already matches Postgres')
    parser.add_argument('--skip-missing-docs', action='store_true',
                        help='do not full-index docs absent from ES (leaves drift, much faster)')
    parser.add_argument('--state-file', help='append one JSON record per expansion+resource')
    args = parser.parse_args()

    resources = sorted(SPECS) if args.resource == 'both' else [args.resource]
    for resource in resources:
        assert_keyword_mapping(SPECS[resource]['index'])

    expansions = Expansion.objects.order_by('id')
    if args.start_id:
        expansions = expansions.filter(id__gte=args.start_id)
    total = expansions.count()
    if args.limit:
        expansions = expansions[:args.limit]
        total = min(total, args.limit)

    print(
        f'{total:,} expansion(s) from id {args.start_id or "start"}; '
        f'resources={",".join(resources)}'
        f'{" [DRY RUN]" if args.dry_run else ""}'
        f'{" [skip-missing-docs]" if args.skip_missing_docs else ""}',
        flush=True
    )

    tallies = {r: {'ok': 0, 'short': 0, 'empty': 0, 'error': 0, 'no_members': 0, 'already': 0}
               for r in resources}
    state = open(args.state_file, 'a', encoding='utf-8') if args.state_file else None
    started = time.time()
    seen = 0
    last_id = args.start_id
    try:
        for expansion in expansions.iterator(chunk_size=200):
            seen += 1
            last_id = expansion.id
            for resource in resources:
                status, record = process(expansion, resource, args)
                if status == 'NONE':
                    tallies[resource]['no_members'] += 1
                    continue
                if status == 'DONE':
                    tallies[resource]['already'] += 1
                    continue

                tallies[resource][STATUS_KEY[status]] += 1
                if state:
                    state.write(json.dumps(record) + '\n')
                    state.flush()
                if status != 'OK':
                    print(
                        f'  {status:6} id={expansion.id:<8} {resource:8} '
                        f'want={record["want"]:<7} got={record["got"]:<7} {expansion.uri}'
                        + (f' | {record["error"]}' if record['error'] else ''),
                        flush=True
                    )

            if seen % 200 == 0:
                rate = seen / max(time.time() - started, 0.001)
                print(
                    f'  ... {seen:,}/{total:,} expansions  {rate:.1f}/s  '
                    f'eta {(total - seen) / max(rate, 0.001) / 60:.0f}m  last_id={last_id}',
                    flush=True
                )
    except KeyboardInterrupt:
        print(f'\ninterrupted after {seen:,} expansion(s); resume with --start-id {last_id}')
    finally:
        if state:
            state.close()

    elapsed = time.time() - started
    print(f'\nprocessed {seen:,} expansion(s) in {elapsed / 60:.1f}m ({elapsed:.1f}s)')
    incomplete = 0
    for resource, tally in tallies.items():
        incomplete += tally['short'] + tally['empty'] + tally['error']
        print(
            f'  {resource:8} ok={tally["ok"]:,} short={tally["short"]:,} '
            f'empty={tally["empty"]:,} error={tally["error"]:,} '
            f'already={tally["already"]:,} no_members={tally["no_members"]:,}'
        )
    if incomplete:
        print(
            f'\n{incomplete} expansion/resource pair(s) incomplete -- do NOT flip the query '
            f'filter yet.\nRe-run, or inspect the state file for the exact ids.'
        )
        return 1
    print('\nevery expansion with members verified complete')
    return 0


if __name__ == '__main__':
    sys.exit(main())
