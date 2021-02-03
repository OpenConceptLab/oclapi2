import json
import os

from django.core.management import BaseCommand

from core.common.constants import HEAD
from core.concepts.models import Concept
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile


class Command(BaseCommand):
    help = 'import lookup values'

    def handle(self, *args, **options):
        user = UserProfile.objects.filter(username='ocladmin').get()
        org = Organization.objects.get(mnemonic='OCL')
        sources = self.create_sources(org, user)

        current_path = os.path.dirname(__file__)
        importer_confs = [
            dict(
                source=sources['Classes'],
                file=os.path.join(current_path, "../../../lookup_fixtures/concept_classes.json")
            ),
            dict(
                source=sources['Locales'],
                file=os.path.join(current_path, "../../../lookup_fixtures/locales.json")
            ),
            dict(
                source=sources['Datatypes'],
                file=os.path.join(current_path, "../../../lookup_fixtures/datatypes_fixed.json")
            ),
            dict(
                source=sources['NameTypes'],
                file=os.path.join(current_path, "../../../lookup_fixtures/nametypes_fixed.json")
            ),
            dict(
                source=sources['DescriptionTypes'],
                file=os.path.join(current_path, "../../../lookup_fixtures/description_types.json")
            ),
            dict(
                source=sources['MapTypes'],
                file=os.path.join(current_path, "../../../lookup_fixtures/maptypes_fixed.json")
            ),
        ]

        for conf in importer_confs:
            source = conf['source']
            self.create_concepts(source, conf['file'], user)

    @staticmethod
    def create_sources(org, user):
        sources = dict()

        kwargs = {
            'parent_resource': org
        }

        for source_name in ['Locales', 'Classes', 'Datatypes', 'DescriptionTypes', 'NameTypes', 'MapTypes']:
            if Source.objects.filter(organization=org, mnemonic=source_name, version=HEAD).exists():
                source = Source.objects.get(organization=org, mnemonic=source_name, version=HEAD)
            else:
                source = Source(
                    name=source_name, mnemonic=source_name, full_name=source_name, organization=org, created_by=user,
                    default_locale='en', supported_locales=['en'], updated_by=user, version=HEAD
                )
                Source.persist_new(source, user, **kwargs)

            sources[source_name] = source

        return sources

    @staticmethod
    def create_concepts(source, file, user):
        file = open(file, 'r')
        lines = file.readlines()
        created = False
        for line in lines:
            data = json.loads(line)
            mnemonic = data.pop('id', None)
            if not Concept.objects.filter(parent=source, mnemonic=mnemonic, is_latest_version=True).exists():
                data['mnemonic'] = mnemonic
                data['name'] = mnemonic
                data['parent'] = source
                Concept.persist_new(data, user)
                if not created:
                    created = True
        return created
