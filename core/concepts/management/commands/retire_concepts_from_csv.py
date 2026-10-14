import csv
import time

from django.core.management import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F

from core.concepts.models import Concept
from core.users.models import UserProfile


class Command(BaseCommand):
    help = (
        'Retires the exact versioned objects listed in a CSV (column `retire_vo`) the way the retire API does: each gets '
        'a new latest version with retired=true, and older versions keep their frozen state. Unlike '
        'DELETE .../concepts/<mnemonic>/, which resolves a code to one versioned object, this targets by id, so it can '
        'retire one of two twin versioned objects that share a code (OpenConceptLab/ocl_online#297). '
        'Dry run unless --apply is given.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--csv', required=True, help='CSV with a `retire_vo` column of versioned object ids.')
        parser.add_argument('--head-id', type=int, required=True, help='The HEAD source the concepts must belong to.')
        parser.add_argument('--username', required=True, help='The user the new versions are created by.')
        parser.add_argument('--comment', default='Superseded with latest import')
        parser.add_argument('--retire-reason', default=None)
        parser.add_argument('--limit', type=int, help='Retire only the first N (a trial run to inspect).')
        parser.add_argument(
            '--settle', type=int, default=0,
            help='Seconds to wait after each retire (the hierarchy follow-up runs as a task) before checking that '
                 'the twin versioned object and the parents are unchanged; stops at the first change.')
        parser.add_argument(
            '--allow-parent-changes', action='store_true',
            help='With --settle, do not stop when a parent gets a new latest version; the twin check still applies.')
        parser.add_argument('--expect', type=int, help='Abort unless exactly this many concepts would be retired.')
        parser.add_argument('--apply', action='store_true', help='Retire for real; without it nothing changes.')

    def handle(self, *args, **options):
        with open(options['csv'], newline='', encoding='utf-8') as f:
            ids = sorted({int(row['retire_vo']) for row in csv.DictReader(f) if row.get('retire_vo')})
        user = UserProfile.objects.filter(username=options['username']).first()
        if not user:
            raise CommandError(f'user {options["username"]} not found')

        todo, skipped = [], {}
        concepts = Concept.objects.in_bulk(ids)
        for vo_id in ids:
            concept = concepts.get(vo_id)
            if not concept:
                skipped[vo_id] = 'not found'
            elif concept.id != concept.versioned_object_id:
                skipped[vo_id] = 'not a versioned object'
            elif concept.parent_id != options['head_id']:
                skipped[vo_id] = f'belongs to source {concept.parent_id}'
            elif concept.retired:
                skipped[vo_id] = 'already retired'
            else:
                todo.append(concept)

        self.stdout.write(f'ids in CSV: {len(ids)}; to retire: {len(todo)}; skipped: {len(skipped)}')
        for reason in sorted(set(skipped.values())):
            self.stdout.write(f'  {reason}: {sum(1 for r in skipped.values() if r == reason)}')
        if options['expect'] is not None and len(todo) != options['expect']:
            raise CommandError(f'aborting: {len(todo)} would be retired, expected {options["expect"]}')
        if not options['apply']:
            self.stdout.write('dry run: nothing retired (use --apply)')
            return

        if options['limit']:
            todo = todo[:options['limit']]
        failed = 0
        for position, concept in enumerate(todo, start=1):
            before = self.snapshot(concept)
            errors = self.retire_by_id(concept, user, options['comment'], options['retire_reason'])
            if options['settle']:
                time.sleep(options['settle'])
                after = self.snapshot(concept, refresh_of=before)
                if options['allow_parent_changes']:
                    after['parents'] = before['parents']
                changed = self.changes(before, after)
                if changed:
                    raise CommandError(f'stopped after {concept.mnemonic} ({concept.id}): {changed}')
            if errors:
                failed += 1
                self.stderr.write(f'{concept.mnemonic} ({concept.id}): {errors}')
            if position % 200 == 0:
                self.stdout.write(f'  retired {position}/{len(todo)}')
        if todo:
            Concept.objects.filter(id=todo[0].id).first().parent.update_concepts_count()
        self.stdout.write(f'retired {len(todo) - failed}, failed {failed}')
        if failed:
            raise CommandError(f'{failed} concepts could not be retired')

    @staticmethod
    def retire_by_id(concept, user, comment, reason):
        """
        What Concept.retire does (a new retired latest version, older versions untouched), done by ids only: no URI or
        mnemonic lookup, and none of the hierarchy tasks, which resolve parents and children by URI and can land on the
        other twin. Parent links are carried over by id instead: the new version gets the previous latest version's
        parents, and becomes the parent of the previous latest version's children.
        """
        latest = concept.get_latest_version() or concept.get_last_version()
        if not latest:
            return {'__all__': ['no version found']}
        parent = latest.parent
        with transaction.atomic():
            parents = list(latest.parent_concepts.all())
            children = list(Concept.objects.filter(parent_concepts=latest))
            new_version = latest.clone()
            new_version._index = False  # pylint: disable=protected-access
            new_version.retired = True
            new_version.comment = comment
            if reason is not None:
                new_version.retire_reason = reason
            new_version.created_by = new_version.updated_by = user
            latest.unmark_latest_version(False, parent)
            new_version.is_latest_version = True
            new_version.save()
            new_version.parent_concepts.set(parents)
            new_version.post_version_create(parent, None)
            new_version.set_checksums()
            for child in children:
                child.parent_concepts.add(new_version)
            transaction.on_commit(lambda: (latest.index(), new_version.index()))
        return {}

    @staticmethod
    def snapshot(concept, refresh_of=None):
        """What a retire must not touch: the twin versioned object (same code, same HEAD) and the parents."""
        twins = Concept.objects.filter(
            parent_id=concept.parent_id, mnemonic=concept.mnemonic, id=F('versioned_object_id')
        ).exclude(id=concept.id)
        state = {'twins': {}, 'parents': {}}
        for twin in twins:
            latest = twin.get_latest_version()
            state['twins'][twin.id] = (
                latest.id if latest else None,
                latest.parent_concepts.count() if latest else 0,
                latest.child_concepts.count() if latest else 0,
                twin.versions.count(),
            )
        latest = concept.get_latest_version() if refresh_of is None else None
        parent_ids = refresh_of['parent_ids'] if refresh_of else (
            list(latest.parent_concepts.values_list('versioned_object_id', flat=True)) if latest else [])
        state['parent_ids'] = parent_ids
        for parent in Concept.objects.filter(id__in=parent_ids):
            parent_latest = parent.get_latest_version()
            state['parents'][parent.id] = parent_latest.id if parent_latest else None
        return state

    @staticmethod
    def changes(before, after):
        changed = []
        if before['twins'] != after['twins']:
            changed.append(f'twin changed {before["twins"]} -> {after["twins"]}')
        if before['parents'] != after['parents']:
            changed.append(f'a parent got a new latest version {before["parents"]} -> {after["parents"]}')
        return '; '.join(changed)
