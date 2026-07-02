# -*- coding: utf-8 -*-
"""
Benchmarks Expansion.batch_index (new partial ES update) vs the previous
batch_index_full (full document re-index) for a collection expansion.

Usage (inside the api container):
    python tools/benchmark_batch_index.py <collection_uri> [--runs N]

Examples:
    python tools/benchmark_batch_index.py /users/jamlung/collections/gigantic-collection/1/
    python tools/benchmark_batch_index.py /orgs/PEPFAR-MER/collections/MER_FY22/v1.0/ --runs 3
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

import django  # noqa: E402
django.setup()

from core.collections.models import Collection, Expansion  # noqa: E402
from core.common.models import BaseModel  # noqa: E402
from core.concepts.documents import ConceptDocument  # noqa: E402
from core.mappings.documents import MappingDocument  # noqa: E402


def resolve_expansion(collection_uri):
    version, _ = Collection.resolve_reference_expression(collection_uri)
    if not version.id:
        print(f'ERROR: Could not resolve: {collection_uri}')
        sys.exit(1)
    expansion = version.expansion
    if not expansion:
        print(f'ERROR: No expansion for {collection_uri}')
        sys.exit(1)
    return version, expansion


def bench_full(expansion, run_index):
    label = f'[A] Full re-index (run {run_index})'
    print(f'\n  {label} ...')

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
    print(f'    total    : {total:.3f}s')
    return total


def bench_partial(expansion, run_index):
    label = f'[B] Partial update (run {run_index})'
    print(f'\n  {label} ...')

    t0 = time.perf_counter()
    expansion.batch_index(expansion.concepts, ConceptDocument)
    concept_elapsed = time.perf_counter() - t0

    t0 = time.perf_counter()
    expansion.batch_index(expansion.mappings, MappingDocument)
    mapping_elapsed = time.perf_counter() - t0

    total = concept_elapsed + mapping_elapsed
    print(f'    concepts : {concept_elapsed:.3f}s')
    print(f'    mappings : {mapping_elapsed:.3f}s')
    print(f'    total    : {total:.3f}s')
    return total


def avg(values):
    return sum(values) / len(values) if values else 0


def main():
    parser = argparse.ArgumentParser(description='Benchmark batch_index approaches')
    parser.add_argument('collection_uri', help='Collection version URI')
    parser.add_argument('--runs', type=int, default=2)
    args = parser.parse_args()

    version, expansion = resolve_expansion(args.collection_uri)
    n_concepts = expansion.concepts.count()
    n_mappings = expansion.mappings.count()

    print(f'\nCollection : {version.uri}')
    print(f'Expansion  : {expansion.mnemonic}')
    print(f'Concepts   : {n_concepts}')
    print(f'Mappings   : {n_mappings}')

    results_full = []
    results_partial = []

    for i in range(1, args.runs + 1):
        print(f'\n{"="*60}\n  Run {i} / {args.runs}\n{"="*60}')
        results_full.append(bench_full(expansion, i))
        results_partial.append(bench_partial(expansion, i))

    print(f'\n{"="*60}\n  SUMMARY\n{"="*60}')
    print(f'  [A] Full re-index  avg: {avg(results_full):.3f}s  '
          f'(min {min(results_full):.3f}s  max {max(results_full):.3f}s)')
    print(f'  [B] Partial update avg: {avg(results_partial):.3f}s  '
          f'(min {min(results_partial):.3f}s  max {max(results_partial):.3f}s)')
    if results_full and results_partial:
        speedup = avg(results_full) / avg(results_partial)
        print(f'  Speedup: {speedup:.1f}x')


if __name__ == '__main__':
    main()
