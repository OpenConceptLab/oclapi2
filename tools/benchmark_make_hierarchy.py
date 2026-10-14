# -*- coding: utf-8 -*-
"""
Benchmarks make_hierarchy (old per-child implementation vs bulk) against a {parent_uri: [child_uris]} json file.

Every run happens inside a transaction that is rolled back, so the database is left untouched. Celery dispatch of
the ES m2m signal is replaced with a counter. Before each run the existing links of the sampled parents are
deleted, and the recreated link set is compared with the original one and between the implementations.

Usage (inside the api container):
    python tools/benchmark_make_hierarchy.py loinc-2.82/hierarchy_only.json --parents 100 [--impl old,new]
"""
import argparse
import json
import os
import sys
import time
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')


def old_make_hierarchy(concept_map):
    from core.concepts.models import Concept

    for parent_concept_uri, child_concept_urls in concept_map.items():
        parent_concept = Concept.objects.filter(uri=parent_concept_uri).first()
        if parent_concept:
            parent_latest = parent_concept.get_latest_version()
            if parent_latest:
                for child_concept in Concept.objects.filter(uri__in=child_concept_urls):
                    child_concept.parent_concepts.add(parent_latest)
                    child_latest = child_concept.get_latest_version()
                    if child_latest:
                        child_latest.parent_concepts.add(parent_latest)


def main():
    import django
    django.setup()
    from django.db import connection, transaction
    from core.common import tasks
    from core.concepts.models import Concept, HierarchicalConcepts

    parser = argparse.ArgumentParser()
    parser.add_argument('file')
    parser.add_argument('--parents', type=int, default=100)
    parser.add_argument('--impl', default='old,new')
    args = parser.parse_args()

    with open(args.file, encoding='utf-8') as f:
        full = json.load(f)
    concept_map = dict(list(full.items())[:args.parents])
    children = sum(len(v) for v in concept_map.values())
    print(f'sample: {len(concept_map)} parents, {children} children')

    results = {}
    for impl in args.impl.split(','):
        fn = old_make_hierarchy if impl == 'old' else tasks.make_hierarchy.run
        dispatched = mock.Mock()
        with mock.patch.object(tasks.handle_m2m_changed, 'apply_async', dispatched), \
                mock.patch.object(tasks.handle_save, 'apply_async', dispatched):
            try:
                with transaction.atomic():
                    parent_ids = [
                        c.get_latest_version().id for c in Concept.objects.filter(uri__in=list(concept_map))
                        if c.get_latest_version()]
                    before = set(HierarchicalConcepts.objects.filter(
                        parent_id__in=parent_ids).values_list('child_id', 'parent_id'))
                    HierarchicalConcepts.objects.filter(parent_id__in=parent_ids).delete()
                    queries = []
                    with connection.execute_wrapper(lambda ex, *a: queries.append(1) or ex(*a)):
                        start = time.perf_counter()
                        fn(concept_map)
                        elapsed = time.perf_counter() - start
                    after = set(HierarchicalConcepts.objects.filter(
                        parent_id__in=parent_ids).values_list('child_id', 'parent_id'))
                    results[impl] = after
                    print(f'{impl}: {elapsed:.2f}s, {len(queries)} queries, {dispatched.call_count} ES tasks queued, '
                          f'{len(after)} links (originally {len(before)}; missing {len(before - after)}, '
                          f'extra {len(after - before)})')
                    raise RuntimeError('rollback')
            except RuntimeError as e:
                if str(e) != 'rollback':
                    raise
    if len(results) == 2:
        print('old and new produce identical links:', results['old'] == results['new'])


if __name__ == '__main__':
    main()
