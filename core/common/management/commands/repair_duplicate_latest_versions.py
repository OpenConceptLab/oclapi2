from django.core.management import BaseCommand
from django.db import transaction
from django.db.models import Count, F

from core.concepts.models import Concept
from core.mappings.models import Mapping


class Command(BaseCommand):
    help = ('Finds concepts and mappings with more than one version flagged latest, keeps the one with the highest id '
            'as latest and demotes the others, as a new version would have. Every repaired concept and mapping is '
            'listed.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        for model in (Concept, Mapping):
            repaired = self.repair(model, options['dry_run'])
            verb = 'would be repaired' if options['dry_run'] else 'repaired'
            self.stdout.write(f'{len(repaired)} {model.__name__.lower()}(s) {verb}')
            for line in repaired:
                self.stdout.write(f'  {verb}: {line}')

    @staticmethod
    def get_versioned_object_ids(model):
        return list(
            model.objects.filter(is_latest_version=True).exclude(id=F('versioned_object_id')).order_by().values(
                'versioned_object_id').annotate(count=Count('id')).filter(count__gt=1).values_list(
                'versioned_object_id', flat=True)
        )

    def repair(self, model, dry_run=False):
        repaired = []
        parents = {}
        for versioned_object_id in self.get_versioned_object_ids(model):
            line = self.repair_one(model, versioned_object_id, dry_run)
            if line:
                repaired.append(line[0])
                parents[line[1].id] = line[1]
        if not dry_run:
            for parent in parents.values():
                if model is Concept:
                    parent.update_concepts_count()
                else:
                    parent.update_mappings_count()
        return repaired

    @staticmethod
    def repair_one(model, versioned_object_id, dry_run=False):
        with transaction.atomic():
            head = model.objects.select_for_update().filter(id=versioned_object_id).first()
            if not head:
                return None
            latest, *stale = list(
                head.versions.filter(is_latest_version=True).order_by('-id')) or [None]
            if not stale:
                return None
            line = f"{head.uri} keeps {latest.version}, demotes {', '.join(version.version for version in stale)}"
            if dry_run:
                return line, head.parent
            parent = head.parent
            for version in stale:
                version.unmark_latest_version(True, parent)
            latest.update_versioned_object()
            transaction.on_commit(lambda: [version.index() for version in [latest, *stale]])
        return line, parent
