import os
from glob import glob

from django.conf import settings
from django.core.management import BaseCommand, call_command

from core.orgs.models import Organization

FIXTURES_DIR = os.path.join(settings.BASE_DIR, 'core', 'fixtures')
OCL_ORG = os.path.join(FIXTURES_DIR, 'ocl_org.yaml')


class Command(BaseCommand):
    help = 'load core/fixtures on container start; ocl_org.yaml only when the OCL org does not exist yet'

    def handle(self, *args, **options):
        fixtures = sorted(glob(os.path.join(FIXTURES_DIR, '*')))
        if Organization.objects.filter(id=1).exists():
            fixtures.remove(OCL_ORG)
        call_command('loaddata', *fixtures, verbosity=options['verbosity'])
