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
        ocladmin = UserProfile.objects.filter(username='ocladmin').get()
        org_OCL = Organization.objects.get(mnemonic='OCL')
        org_ISO = Organization.objects.filter(mnemonic='ISO').first()
        if not org_ISO:
            org_ISO = Organization(name='International Organization for Standardization (ISO)', mnemonic='ISO')
            org_ISO.save()
            org_ISO.members.add(ocladmin)

        sources = self.get_or_create(org_OCL, ocladmin)
        iso_source = self.get_or_create_iso_source(org_ISO, ocladmin)

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
            self.create_concepts(source, conf['file'], ocladmin)

        self.create_concepts(
            iso_source, os.path.join(current_path, "../../../lookup_fixtures/iso_639_1_locales.json"), ocladmin
        )

    @staticmethod
    def get_or_create(org, user):
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
    def get_or_create_iso_source(org, user):
        ISO_SOURCE_ID = 'iso639-1'

        kwargs = {
            'parent_resource': org
        }

        source = Source.objects.filter(organization=org, mnemonic='iso639-1').first()
        if not source:
            source = Source(
                name='Iso6391',
                mnemonic=ISO_SOURCE_ID,
                full_name='ISO 639-1: Codes for the representation of names of languages -- Part 1: Alpha-2 code',
                description='Codes for the Representation of Names of Languages Part 1: Alpha-2 Code.'
                            ' Used as part of the IETF 3066 specification for languages '
                            'throughout the HL7 specification.',
                canonical_url='http://terminology.hl7.org/CodeSystem/iso639-1',
                organization=org,
                default_locale='en',
                version=HEAD,
                created_by=user,
                updated_by=user,
                active_mappings=0,
            )
            Source.persist_new(source, user, **kwargs)

        return source

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
        if created:
            source.active_concepts = source.concepts_set.filter(is_latest_version=True).count()
            source.save()
        return created
