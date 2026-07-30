# -*- coding: utf-8 -*-
"""
Benchmarks expansion evaluation: existing persisted expansion vs on-the-fly.

Measures wall-clock time and DB query count for:
  A) Existing path: seed_children() — evaluates all collection references and
     persists concepts/mappings to Expansion M2M tables.
  B) On-the-fly path (Path 1, full): re-evaluates every ref from source data,
     batching simple code-only refs by source version, evaluating cascade/filter
     refs individually. No DB writes.
  C) On-the-fly path (Path 2, per-repo): user provides specific repo version URIs;
     only refs matching those sources are re-evaluated, others use cached ref.concepts.

All writes in paths A are rolled back. Paths B/C are purely read-only.

Usage (inside the api container, or any env with DJANGO_SETTINGS_MODULE set):
    python tools/benchmark_expansion.py <collection_uri> [--repo-version URI] [--runs N]

Examples:
    python tools/benchmark_expansion.py /orgs/MyOrg/collections/MyCol/v1.0/
    python tools/benchmark_expansion.py /orgs/MyOrg/collections/MyCol/HEAD/ --runs 3
    python tools/benchmark_expansion.py /orgs/MyOrg/collections/MyCol/v1.0/ \\
        --repo-version /orgs/MyOrg/sources/MySrc/v2.0/,/orgs/MyOrg/sources/Other/v1.0/
"""
import argparse
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

import django  # noqa: E402
django.setup()

from django.db import connection, transaction  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402

from core.collections.models import Collection, CollectionReference, Expansion, ExpansionParameters  # noqa: E402
from core.common.models import ConceptContainerModel  # noqa: E402
from core.concepts.models import Concept  # noqa: E402
from core.mappings.models import Mapping  # noqa: E402
from django.db.models import F  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_collection_version(uri):
    """Resolve a collection URI string to a Collection version object."""
    version, _ = Collection.resolve_reference_expression(uri)
    if not version.id:
        print(f'ERROR: Could not resolve collection version: {uri}')
        sys.exit(1)
    return version


def resolve_repo_versions(uris_csv):
    """Resolve comma-separated repo URIs to Source/Collection version objects."""
    versions = []
    for uri in (u.strip() for u in uris_csv.split(',') if u.strip()):
        version, _ = ConceptContainerModel.resolve_reference_expression(uri)
        if version.id:
            versions.append(version)
        else:
            print(f'  WARNING: Could not resolve repo version: {uri}')
    return versions


def separator(label):
    width = 70
    print()
    print('=' * width)
    print(f'  {label}')
    print('=' * width)


def report(label, elapsed, n_queries, n_concepts, n_mappings):
    print(f'  {label}')
    print(f'    elapsed     : {elapsed:.3f}s')
    print(f'    db queries  : {n_queries}')
    print(f'    concepts    : {n_concepts}')
    print(f'    mappings    : {n_mappings}')


# ---------------------------------------------------------------------------
# Path A: existing seed_children (with rollback)
# ---------------------------------------------------------------------------

def bench_existing_expansion(collection_version, run_index):
    label = f'[A] Existing — seed_children (run {run_index})'
    print(f'\n  Starting {label} ...')

    try:
        with transaction.atomic():
            with CaptureQueriesContext(connection) as ctx:
                t0 = time.perf_counter()

                expansion = Expansion(
                    mnemonic=f'__bench_{run_index}__',
                    collection_version=collection_version,
                )
                expansion.save()
                expansion.seed_children(index=False)

                elapsed = time.perf_counter() - t0
                n_queries = len(ctx.captured_queries)
                n_concepts = expansion.concepts.count()
                n_mappings = expansion.mappings.count()

            raise transaction.TransactionManagementError('benchmark rollback')  # always rollback
    except transaction.TransactionManagementError:
        pass

    report(label, elapsed, n_queries, n_concepts, n_mappings)
    return elapsed, n_queries, n_concepts, n_mappings


# ---------------------------------------------------------------------------
# Path B: on-the-fly full (Path 1)
# ---------------------------------------------------------------------------

def _evaluate_refs_on_the_fly(include_refs, exclude_refs, forced_system_versions=None):
    """
    Core on-the-fly evaluation — mirrors the planned Expansion.evaluate_refs_on_the_fly().
    Returns (concept_ids set, mapping_ids set).
    """
    from core.common.utils import decode_string  # local import to match models.py pattern

    _system_version_cache = {}

    def resolve_sv(ref):
        return ref._get_resolved_system_version(_system_version_cache)  # pylint: disable=protected-access

    def eval_ref(ref, forced_svs=None):
        if forced_svs:
            sv = next((s for s in forced_svs if ref.can_compute_against_system_version(s)), None) or resolve_sv(ref)
        else:
            sv = resolve_sv(ref)
        if ref.is_mapping:
            return Concept.objects.none(), ref.get_mappings(sv)
        return ref.get_concepts(sv)

    all_concept_ids = set()
    all_mapping_ids = set()

    if not forced_system_versions:
        # --- Path 1: batch code-only refs by (source_version, resource_type) ---
        # key: (sv.id, 'concepts'|'mappings') → [sv, [ref]]
        code_only_by_source = {}
        complex_refs = []

        for ref in include_refs:
            sv = resolve_sv(ref)
            if ref.code and not ref.cascade and not ref.filter and sv and not ref.valueset:
                rtype = 'mappings' if ref.is_mapping else 'concepts'
                bucket = code_only_by_source.setdefault((sv.id, rtype), [sv, rtype, []])
                bucket[2].append(ref)
            else:
                complex_refs.append((ref, sv))

        for sv, rtype, refs_in_bucket in code_only_by_source.values():
            codes = [decode_string(r.code) for r in refs_in_bucket]
            rel = sv.mappings if rtype == 'mappings' else sv.concepts
            qs = rel.filter(mnemonic__in=codes)
            if sv.is_head and rtype == 'concepts':
                qs = qs.filter(id=F('versioned_object_id'))
            if rtype == 'mappings':
                all_mapping_ids.update(qs.values_list('id', flat=True))
            else:
                all_concept_ids.update(qs.values_list('id', flat=True))

        for ref, sv in complex_refs:
            if ref.is_mapping:
                mappings = ref.get_mappings(sv)
                all_mapping_ids.update(mappings.values_list('id', flat=True))
            else:
                concepts, mappings = ref.get_concepts(sv)
                all_concept_ids.update(concepts.values_list('id', flat=True))
                all_mapping_ids.update(mappings.values_list('id', flat=True))

        for ref in exclude_refs:
            sv = resolve_sv(ref)
            if ref.is_mapping:
                mappings = ref.get_mappings(sv)
                all_mapping_ids -= set(mappings.values_list('id', flat=True))
            else:
                concepts, _ = ref.get_concepts(sv)
                all_concept_ids -= set(concepts.values_list('id', flat=True))

    else:
        # --- Path 2: re-evaluate all refs, using forced versions where they match ---
        for ref in include_refs:
            concepts, mappings = eval_ref(ref, forced_system_versions)
            all_concept_ids.update(concepts.values_list('id', flat=True))
            all_mapping_ids.update(mappings.values_list('id', flat=True))

        for ref in exclude_refs:
            concepts, mappings = eval_ref(ref, forced_system_versions)
            all_concept_ids -= set(concepts.values_list('id', flat=True))
            all_mapping_ids -= set(mappings.values_list('id', flat=True))

    return all_concept_ids, all_mapping_ids


def bench_on_the_fly_full(collection_version, run_index):
    label = f'[B] On-the-fly Path 1 — full re-evaluate (run {run_index})'
    print(f'\n  Starting {label} ...')

    include_refs = list(collection_version.references.filter(include=True))
    exclude_refs = list(collection_version.references.exclude(include=True))

    with CaptureQueriesContext(connection) as ctx:
        t0 = time.perf_counter()
        concept_ids, mapping_ids = _evaluate_refs_on_the_fly(include_refs, exclude_refs)
        elapsed = time.perf_counter() - t0

    report(label, elapsed, len(ctx.captured_queries), len(concept_ids), len(mapping_ids))
    return elapsed, len(ctx.captured_queries), len(concept_ids), len(mapping_ids)


def bench_on_the_fly_per_repo(collection_version, repo_versions, run_index):
    label = f'[C] On-the-fly Path 2 — per-repo version (run {run_index})'
    print(f'\n  Starting {label} ...')
    print(f'    repo_versions: {[v.uri for v in repo_versions]}')

    include_refs = list(collection_version.references.filter(include=True))
    exclude_refs = list(collection_version.references.exclude(include=True))

    with CaptureQueriesContext(connection) as ctx:
        t0 = time.perf_counter()
        concept_ids, mapping_ids = _evaluate_refs_on_the_fly(
            include_refs, exclude_refs, forced_system_versions=repo_versions)
        elapsed = time.perf_counter() - t0

    report(label, elapsed, len(ctx.captured_queries), len(concept_ids), len(mapping_ids))
    return elapsed, len(ctx.captured_queries), len(concept_ids), len(mapping_ids)


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

def avg(values):
    return sum(values) / len(values) if values else 0


def print_summary(results_a, results_b, results_c):
    separator('SUMMARY (averages across runs)')

    def row(tag, runs):
        if not runs:
            return
        times, queries, concepts, mappings = zip(*runs)
        print(f'  {tag}')
        print(f'    avg time    : {avg(times):.3f}s  (min {min(times):.3f}s  max {max(times):.3f}s)')
        print(f'    avg queries : {avg(queries):.0f}')
        print(f'    concepts    : {concepts[0]}  mappings: {mappings[0]}')

    row('[A] Existing seed_children', results_a)
    row('[B] On-the-fly full       ', results_b)
    row('[C] On-the-fly per-repo   ', results_c)

    if results_a and results_b:
        speedup = avg([r[0] for r in results_a]) / avg([r[0] for r in results_b])
        print(f'\n  B vs A speedup: {speedup:.1f}x')
    if results_a and results_c:
        speedup = avg([r[0] for r in results_a]) / avg([r[0] for r in results_c])
        print(f'  C vs A speedup: {speedup:.1f}x')


# ---------------------------------------------------------------------------
# Ref distribution analysis
# ---------------------------------------------------------------------------

def analyse_refs(collection_version):
    separator('Reference Distribution Analysis')
    refs = collection_version.references.all()
    total = refs.count()
    include_count = refs.filter(include=True).count()
    exclude_count = total - include_count

    code_refs = refs.filter(include=True, code__isnull=False).count()
    cascade_refs = refs.filter(include=True).exclude(cascade__isnull=True).exclude(cascade='').exclude(cascade={}).count()
    filter_refs = refs.filter(include=True).exclude(filter__isnull=True).exclude(filter=[]).count()
    valueset_refs = refs.filter(include=True).exclude(valueset__isnull=True).exclude(valueset=[]).count()

    # Count unique resolved source versions (approximate via distinct system+version)
    system_versions = refs.filter(include=True).exclude(system__isnull=True).values_list('system', 'version', 'namespace').distinct()

    print(f'  Collection   : {collection_version.uri}')
    print(f'  Total refs   : {total}  (include={include_count}, exclude={exclude_count})')
    print(f'  Code refs    : {code_refs}  (batchable per source version in Path 1)')
    print(f'  Cascade refs : {cascade_refs}  (evaluated individually, O(N))')
    print(f'  Filter refs  : {filter_refs}  (evaluated individually via ES)')
    print(f'  Valueset refs: {valueset_refs}')
    print(f'  Unique (system, version) combinations: {system_versions.count()}')

    # Source version grouping estimate for Path 1
    _cache = {}
    include_refs = list(refs.filter(include=True)[:500])  # sample first 500 for speed
    sv_counts = defaultdict(int)
    for ref in include_refs:
        sv = ref._get_resolved_system_version(_cache)  # pylint: disable=protected-access
        sv_counts[sv.uri if sv else 'unresolved'] += 1
    print(f'\n  Source version distribution (sample of {len(include_refs)} include refs):')
    for uri, count in sorted(sv_counts.items(), key=lambda x: -x[1])[:10]:
        print(f'    {count:>6}  {uri}')
    if len(sv_counts) > 10:
        print(f'    ... and {len(sv_counts) - 10} more')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Benchmark expansion evaluation paths')
    parser.add_argument('collection_uri', help='Collection version URI, e.g. /orgs/O/collections/C/v1.0/')
    parser.add_argument('--repo-version', dest='repo_version', default='',
                        help='Comma-separated repo version URIs for Path 2 benchmark')
    parser.add_argument('--runs', type=int, default=1, help='Number of benchmark runs (default: 1)')
    parser.add_argument('--skip-existing', action='store_true',
                        help='Skip Path A (existing seed_children) — useful for read-only envs')
    parser.add_argument('--analyse-only', action='store_true',
                        help='Only print reference distribution analysis, no timing')
    args = parser.parse_args()

    collection_version = resolve_collection_version(args.collection_uri)
    repo_versions = resolve_repo_versions(args.repo_version) if args.repo_version else []

    analyse_refs(collection_version)

    if args.analyse_only:
        return

    results_a, results_b, results_c = [], [], []

    for i in range(1, args.runs + 1):
        separator(f'Run {i} / {args.runs}')

        if not args.skip_existing:
            results_a.append(bench_existing_expansion(collection_version, i))

        results_b.append(bench_on_the_fly_full(collection_version, i))

        if repo_versions:
            results_c.append(bench_on_the_fly_per_repo(collection_version, repo_versions, i))

    print_summary(results_a, results_b, results_c)


if __name__ == '__main__':
    main()
