import os
from glob import glob

from django.conf import settings
from django.core.management import BaseCommand, call_command

from core.orgs.models import Organization

FIXTURES_DIR = os.path.join(settings.BASE_DIR, 'core', 'fixtures')
BASE_ENTITIES = os.path.join(FIXTURES_DIR, 'base_entities.yaml')


class Command(BaseCommand):
    help = 'load core/fixtures on container start; base_entities.yaml only when the OCL org does not exist yet'

    def handle(self, *args, **options):
        fixtures = sorted(glob(os.path.join(FIXTURES_DIR, '*')))
        # loaddata overwrites an existing row in full, which reset the OCL org's text, logo and overview
        # to their defaults on every deploy (OpenConceptLab/ocl_issues#2911). Seed it on a new database only.
        if Organization.objects.filter(id=1).exists():
            fixtures.remove(BASE_ENTITIES)
        call_command('loaddata', *fixtures, verbosity=options['verbosity'])
