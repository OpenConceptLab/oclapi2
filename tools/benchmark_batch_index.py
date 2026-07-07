# -*- coding: utf-8 -*-
"""
Benchmarks full expansion cycle: seed_children(force_reevaluate=True) + indexing.

Compares:
  [A] Full re-index  — BaseModel.batch_index_full (previous behaviour)
  [B] Partial update — Expansion.batch_index      (new Painless-script append)

Seeding runs inline (TEST_MODE=True, no celery/Redis required).
Indexing hits the real ES container.

Usage (inside the api container):
    python tools/benchmark_batch_index.py <collection_uri> [--runs N]

Examples:
    python tools/benchmark_batch_index.py /users/jamlung/collections/gigantic-collection/1/
    python tools/benchmark_batch_index.py /orgs/PEPFAR-MER/collections/MER_FY22/v1.0/ --runs 2
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

import django  # noqa: E402
django.setup()

from django.conf import settings  # noqa: E402

from core.collections.models import Collection, Expansion  # noqa: E402
from core.common.models import BaseModel  # noqa: E402
from core.concepts.documents import ConceptDocument  # noqa: E402
from core.mappings.documents import MappingDocument  # noqa: E402


def resolve_collection_version(uri):
    version, _ = Collection.resolve_reference_expression(uri)
    if not version.id:
        print(f'ERROR: Could not resolve: {uri}')
        sys.exit(1)
    return version


def seed_expansion(collection_version, label):
    """Create and seed a new expansion inline (no celery). Returns (expansion, elapsed_seconds)."""
    print(f'\n  Seeding expansion for {label} ...')
    settings.TEST_MODE = True
    try:
        expansion = Expansion(
            mnemonic=f'__bench_{int(time.time())}__',
            collection_version=collection_version,
        )
        expansion.save()
        t0 = time.perf_counter()
        expansion.seed_children(index=False, force_reevaluate=True)
        elapsed = time.perf_counter() - t0
    finally:
        settings.TEST_MODE = False

    n_concepts = expansion.concepts.count()
    n_mappings = expansion.mappings.count()
    print(f'    seed time  : {elapsed:.3f}s')
    print(f'    concepts   : {n_concepts}')
    print(f'    mappings   : {n_mappings}')
    return expansion, elapsed, n_concepts, n_mappings


def bench_full_index(expansion, run_index):
    print(f'\n  [A] Full re-index (run {run_index}) ...')

    t0 = time.perf_counter()
    BaseModel.batch_index_full(
        single_batch=False,
        queryset=expansion.concepts,
        document=ConceptDocument,
        prefetch=['sources', 'names', 'descriptions'],
        select_related=['parent', 'parent__organization', 'parent__user', 'created_by', 'updated_by'],
        parallel=True,
    )
    concept_elapsed = time.perf_counter() - t0

    t0 = time.perf_counter()
    BaseModel.batch_index_full(
        single_batch=False,
        queryset=expansion.mappings,
        document=MappingDocument,
        prefetch=['sources'],
        select_related=['parent', 'parent__organization', 'parent__user', 'created_by', 'updated_by'],
        parallel=True,
    )
    mapping_elapsed = time.perf_counter() - t0

    total = concept_elapsed + mapping_elapsed
    print(f'    concepts : {concept_elapsed:.3f}s')
    print(f'    mappings : {mapping_elapsed:.3f}s')
    print(f'    index total : {total:.3f}s')
    return total


def bench_partial_index(expansion, run_index):
    print(f'\n  [B] Partial update (run {run_index}) ...')

    t0 = time.perf_counter()
    expansion.batch_index(expansion.concepts, ConceptDocument)
    concept_elapsed = time.perf_counter() - t0

    t0 = time.perf_counter()
    expansion.batch_index(expansion.mappings, MappingDocument)
    mapping_elapsed = time.perf_counter() - t0

    total = concept_elapsed + mapping_elapsed
    print(f'    concepts : {concept_elapsed:.3f}s')
    print(f'    mappings : {mapping_elapsed:.3f}s')
    print(f'    index total : {total:.3f}s')
    return total


def avg(values):
    return sum(values) / len(values) if values else 0


def main():
    parser = argparse.ArgumentParser(description='Benchmark seed + index for expansion')
    parser.add_argument('collection_uri', help='Collection version URI')
    parser.add_argument('--runs', type=int, default=1)
    args = parser.parse_args()

    collection_version = resolve_collection_version(args.collection_uri)
    print(f'\nCollection : {collection_version.uri}')

    results_a = []  # (seed_time, index_time)
    results_b = []

    for i in range(1, args.runs + 1):
        print(f'\n{"="*60}\n  Run {i} / {args.runs}\n{"="*60}')

        exp_a, seed_a, n_concepts, n_mappings = seed_expansion(collection_version, f'[A] run {i}')
        idx_a = bench_full_index(exp_a, i)
        results_a.append((seed_a, idx_a))
        exp_a.delete()

        exp_b, seed_b, _, _ = seed_expansion(collection_version, f'[B] run {i}')
        idx_b = bench_partial_index(exp_b, i)
        results_b.append((seed_b, idx_b))
        exp_b.delete()

    print(f'\n{"="*60}\n  SUMMARY  ({n_concepts} concepts, {n_mappings} mappings)\n{"="*60}')

    def row(tag, results):
        seeds = [r[0] for r in results]
        idxs = [r[1] for r in results]
        totals = [s + i for s, i in results]
        print(f'  {tag}')
        print(f'    seed  avg: {avg(seeds):.3f}s')
        print(f'    index avg: {avg(idxs):.3f}s')
        print(f'    total avg: {avg(totals):.3f}s  '
              f'(min {min(totals):.3f}s  max {max(totals):.3f}s)')

    row('[A] Full re-index ', results_a)
    row('[B] Partial update', results_b)

    if results_a and results_b:
        avg_total_a = avg([s + i for s, i in results_a])
        avg_total_b = avg([s + i for s, i in results_b])
        avg_idx_a = avg([i for _, i in results_a])
        avg_idx_b = avg([i for _, i in results_b])
        print(f'\n  Index speedup : {avg_idx_a / avg_idx_b:.1f}x')
        print(f'  Total speedup : {avg_total_a / avg_total_b:.1f}x')


if __name__ == '__main__':
    main()
