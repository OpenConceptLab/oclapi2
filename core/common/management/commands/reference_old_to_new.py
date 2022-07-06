from django.core.management import BaseCommand

from core.collections.models import CollectionReference
from core.collections.parsers import CollectionReferenceExpressionStringParser


class Command(BaseCommand):
    help = 'Reference Old to New Structure'

    def handle(self, *args, **options):
        for reference in CollectionReference.objects.filter(
                expression__isnull=False, system__isnull=True, valueset__isnull=True):
            parser = CollectionReferenceExpressionStringParser(expression=reference.expression)
            parser.parse()
            ref_struct = parser.to_reference_structure()[0]
            reference.reference_type = ref_struct['reference_type']
            reference.system = ref_struct['system']
            reference.version = ref_struct['version']
            reference.code = ref_struct['code']
            reference.resource_version = ref_struct['resource_version']
            reference.valueset = ref_struct['valueset']
            reference.filter = ref_struct['filter']
            reference.save()

