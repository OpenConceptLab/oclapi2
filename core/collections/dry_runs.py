import random
import time
from collections import Counter
from contextlib import contextmanager
from unittest.mock import patch

from django.db import transaction
from django.db.models import F, Q
from django.test import override_settings

from core.celery import app
from core.collections.models import Collection, CollectionReference, Expansion
from core.concepts.models import Concept
from core.sources.models import Source
from core.tasks.models import AsyncTask, QueueOnceCustomTask
from core.toggles.models import Toggle


class ExpansionDryRun:
    """
    Runs expansion changes twice, with base_env's toggles and then with the given toggles on as well,
    each in a transaction that is rolled back, and reports what the toggles change.
    No Celery task is queued and nothing is indexed.
    """
    DRY_RUN_VERSION = 'dry-run'

    def __init__(self, toggles, base_env='production'):
        self.toggles = set(toggles)
        self.toggle_column = self.get_toggle_column(base_env)
        self.blocked_tasks = Counter()

    @staticmethod
    def get_toggle_column(env):
        """The toggles table column an environment reads, as Toggle.to_dict maps it"""
        if not env or env in ['development', 'ci']:
            return 'dev'
        return 'who_staging' if env == 'who-staging' else env

    @staticmethod
    def unknown_toggles(names):
        return sorted(set(names) - set(Toggle.objects.filter(is_active=True).values_list('name', flat=True)))

    def get_toggle(self, name, toggles_on):
        if name in toggles_on:
            return True
        return Toggle.objects.filter(name=name, is_active=True).values_list(self.toggle_column, flat=True).first()

    def block_send(self, name, *args, **kwargs):  # pylint: disable=unused-argument
        self.blocked_tasks[name] += 1

    @contextmanager
    def rolled_back(self, toggles_on):
        blocked_tasks = self.blocked_tasks

        def block_task(task, *args, **kwargs):  # pylint: disable=unused-argument
            blocked_tasks[task.name] += 1

        with patch.object(Toggle, 'get', lambda name: self.get_toggle(name, toggles_on)), \
                patch.object(AsyncTask, 'apply_async', block_task), \
                patch.object(QueueOnceCustomTask, 'apply_async', block_task), \
                patch.object(app, 'send_task', self.block_send), \
                patch.object(Expansion, 'index_resources'), patch.object(Expansion, 'full_index_resources'), \
                override_settings(ES_SYNC=False), transaction.atomic():
            yield
            transaction.set_rollback(True)

    def run(self, action, toggles_on):
        """action() runs in the transaction and returns the expansion to snapshot, and any extra report fields"""
        with self.rolled_back(toggles_on):
            started_at = time.time()
            expansion, extra = action()
            seconds = round(time.time() - started_at, 2)
            expansion.refresh_from_db()
            snapshot = expansion.get_rebuild_snapshot()
        return snapshot, seconds, extra

    def compare(self, key, attribution_expansion, action, order=('off', 'on')):
        """Snapshots from each setting's first run; seconds averaged over its runs"""
        runs = {}
        for label in order:
            snapshot, seconds, extra = self.run(action, self.toggles if label == 'on' else set())
            run = runs.setdefault(label, {'snapshot': snapshot, 'seconds': [], **extra})
            run['seconds'].append(seconds)
        attribution_expansion.refresh_from_db()
        report = {
            'key': key,
            'toggles': sorted(self.toggles),
            'seconds': [round(sum(runs[label]['seconds']) / len(runs[label]['seconds']), 2) for label in ('off', 'on')],
            **attribution_expansion.diff_rebuild_snapshots(runs['off']['snapshot'], runs['on']['snapshot'])
        }
        return report, runs

    def rebuild(self, expansion):
        def action():
            _expansion = Expansion.objects.get(id=expansion.id)
            _expansion.seed_children(index=False, force_reevaluate=True)
            return _expansion, {}

        report, _ = self.compare(expansion.uri, expansion, action)
        return {'expansion': expansion.uri, **report}

    def rebuild_against_stored(self, expansion):
        """A rebuild under base_env's toggles, compared with the stored expansion"""
        with self.rolled_back(set()):
            report = expansion.dry_run_rebuild()
        expansion.refresh_from_db()
        return {'key': expansion.uri, **report}

    def add(self, collection_version, reference):
        """The reference and its members are removed, then the reference is added again as new"""
        def action():
            collection = Collection.objects.get(id=collection_version.id)
            expansion = collection.expansion
            _reference = CollectionReference.objects.get(id=reference.id)
            for field in ('concepts', 'mappings'):
                getattr(Expansion, field).through.objects.filter(**{
                    'expansion_id': expansion.id,
                    f'{field[:-1]}__versioned_object_id__in': getattr(_reference, field).values('versioned_object_id')
                }).delete()
            clone = _reference.clone(collection=collection)
            CollectionReference.objects.filter(id=_reference.id).delete()
            _, errors = collection.add_references([clone], _reference.created_by)
            return expansion, {'errors': errors}

        report, runs = self.compare(
            f'{collection_version.uri} {reference.expression}', collection_version.expansion, action)
        return {
            'collection_version': collection_version.uri, 'reference': reference.expression,
            'errors': [runs['off']['errors'], runs['on']['errors']], **report
        }

    def create_version(self, head):
        """
        A release of HEAD, seeded in this transaction by the version create task, compared with HEAD's expansion.
        Version creation locks the HEAD row, so writes to HEAD wait until each run rolls back.
        """
        def action():
            version = Collection(
                mnemonic=head.mnemonic, version=self.DRY_RUN_VERSION, organization=head.organization, user=head.user,
                released=True, autoexpand=True)
            errors = Collection.persist_new_version(version, head.created_by, sync=True)
            if errors:
                raise ValueError(errors)
            expansion = Collection.objects.get(id=version.id).expansion
            return expansion, {
                'built_with': expansion.built_with,
                'newest_not_in_source_versions': self.count_newest_not_in_source_versions(head.expansion, expansion),
            }

        head_expansion = head.expansion
        head_snapshot = head_expansion.get_rebuild_snapshot()
        report, runs = self.compare(head.uri, head_expansion, action)
        for label in ('off', 'on'):
            run = runs[label]
            report[label] = {
                'built_with': run['built_with'],
                'newest_not_in_source_versions': run['newest_not_in_source_versions'],
                'evaluated_source_versions': run['snapshot']['evaluated_source_versions'],
                'versus_head': {
                    field: dict(Counter(
                        change['status'] for change in Expansion.diff_members(
                            head_snapshot[field], run['snapshot'][field]).values()))
                    for field in ('concepts', 'mappings')
                },
            }
        return {'head': head.uri, **report}

    @staticmethod
    def count_newest_not_in_source_versions(head_expansion, expansion):
        """HEAD's concepts whose newest version isn't in the version's source version of their source"""
        versions = list(expansion.evaluated_source_versions.all())
        newest = Concept.objects.filter(
            versioned_object_id__in=head_expansion.concepts.values('versioned_object_id'), is_latest_version=True,
            parent_id__in={version.head.id for version in versions if version.head})
        held = newest.filter(sources__id__in=[version.id for version in versions]).distinct()
        return newest.count() - held.count()

    def import_references(self, collection_version, expressions, size=None):
        """Imported as the bulk importer adds references; off, on, on, off, so a warm cache favours neither"""
        def action():
            collection = Collection.objects.get(id=collection_version.id)
            added, errors = collection.add_expressions(
                {'expressions': expressions}, collection_version.created_by, False, False)
            return collection.expansion, {'added': len(added), 'errors': len(errors)}

        report, runs = self.compare(
            self.get_import_key(collection_version, len(expressions) if size is None else size),
            collection_version.expansion, action,
            order=('off', 'on', 'on', 'off'))
        return {
            'collection_version': collection_version.uri, 'size': len(expressions),
            'added': [runs['off']['added'], runs['on']['added']],
            'errors': [runs['off']['errors'], runs['on']['errors']],
            **report
        }

    @staticmethod
    def get_import_key(collection_version, size):
        return f'{collection_version.uri} {size}'

    @staticmethod
    def get_source_concept_uris(source):
        """Versionless URIs of the source's active concepts, sorted so a seeded sample repeats"""
        return sorted(Concept.objects.filter(
            parent_id=source.id, id=F('versioned_object_id'), is_active=True, retired=False
        ).values_list('uri', flat=True))

    @staticmethod
    def get_import_expressions(collection_version, source_uris, size):
        """A sample of the source URIs not yet in the collection version's expansion"""
        existing = set(collection_version.expansion.concepts.values_list(
            'versioned_object__uri', flat=True)) if collection_version.expansion_uri else set()
        uris = [uri for uri in source_uris if uri not in existing]
        return random.sample(uris, min(size, len(uris)))

    @staticmethod
    def get_heads_with_loose_locks():
        """HEADs whose default expansion locks a HEAD, unreleased or retired version, where the lock toggle matters"""
        loose = Q(version='HEAD') | Q(retired=True) | Q(released=False)
        head_expansions = Expansion.objects.filter(collection_version__version='HEAD')
        expansion_uris = {
            *head_expansions.filter(evaluated_source_versions__in=Source.objects.filter(loose)).values_list(
                'uri', flat=True),
            *head_expansions.filter(evaluated_collection_versions__in=Collection.objects.filter(loose)).values_list(
                'uri', flat=True),
        }
        return Collection.objects.filter(version='HEAD', expansion_uri__in=expansion_uris)
