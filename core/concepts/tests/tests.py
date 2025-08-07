from unittest.mock import patch, ANY

import factory
from pydash import omit

from core.collections.models import CollectionReference
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.constants import OPENMRS_VALIDATION_SCHEMA, HEAD, ACCESS_TYPE_EDIT, ACCESS_TYPE_VIEW
from core.common.tests import OCLTestCase
from core.concepts.constants import (
    OPENMRS_MUST_HAVE_EXACTLY_ONE_PREFERRED_NAME,
    OPENMRS_FULLY_SPECIFIED_NAME_UNIQUE_PER_SOURCE_LOCALE, OPENMRS_AT_LEAST_ONE_FULLY_SPECIFIED_NAME,
    OPENMRS_PREFERRED_NAME_UNIQUE_PER_SOURCE_LOCALE, OPENMRS_SHORT_NAME_CANNOT_BE_PREFERRED,
    SHORT, INDEX_TERM, OPENMRS_NAMES_EXCEPT_SHORT_MUST_BE_UNIQUE, OPENMRS_ONE_FULLY_SPECIFIED_NAME_PER_LOCALE,
    OPENMRS_NO_MORE_THAN_ONE_SHORT_NAME_PER_LOCALE, CONCEPT_IS_ALREADY_RETIRED, CONCEPT_IS_ALREADY_NOT_RETIRED,
    OPENMRS_CONCEPT_CLASS, OPENMRS_DATATYPE, OPENMRS_DESCRIPTION_TYPE, OPENMRS_NAME_LOCALE)
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.serializers import ConceptListSerializer, ConceptVersionListSerializer, ConceptDetailSerializer, \
    ConceptVersionDetailSerializer, ConceptMinimalSerializer
from core.concepts.tests.factories import ConceptNameFactory, ConceptFactory, ConceptDescriptionFactory
from core.concepts.validators import ValidatorSpecifier
from core.mappings.tests.factories import MappingFactory
from core.sources.tests.factories import OrganizationSourceFactory


class LocalizedTextTest(OCLTestCase):
    def test_clone(self):
        saved_locale = ConceptNameFactory.build()
        cloned_locale = saved_locale.clone()
        self.assertEqual(
            omit(saved_locale.__dict__, ['_state', 'id', 'created_at']),
            omit(cloned_locale.__dict__, ['_state', 'id', 'created_at'])
        )


class ConceptTest(OCLTestCase):
    def test_concept(self):
        self.assertEqual(Concept().concept, '')
        self.assertEqual(Concept(mnemonic='foobar').concept, 'foobar')

    def test_get_search_document(self):
        self.assertEqual(Concept.get_search_document(), ConceptDocument)

    def test_is_versioned(self):
        self.assertTrue(Concept().is_versioned)

    def test_display_name(self):
        source = OrganizationSourceFactory(default_locale='fr', supported_locales=['fr', 'ti'])
        concept = ConceptFactory(
            parent=source, names=1, names__locale_preferred=True, names__locale='ch', names__name='ch')
        en_locale = ConceptNameFactory(locale_preferred=True, locale='en', concept=concept, name='en')

        self.assertEqual(concept.display_name, en_locale.name)  # locale preferred order by created at desc

        source.supported_locales = ['fr', 'ti', 'ch']
        source.save()
        self.assertEqual(concept.display_name, 'ch')  # locale preferred parent's supported locale

        # taking scenarios for ciel 1366 concept
        concept = ConceptFactory(
            parent=source,
            names=[
                ConceptNameFactory.build(locale_preferred=True, locale='en', name='MALARIA SMEAR, QUALITATIVE'),
                ConceptNameFactory.build(type='SHORT', locale_preferred=False, locale='en', name='malaria sm, qual'),
                ConceptNameFactory.build(locale_preferred=False, locale='en', name='Jungle fever smear'),
                ConceptNameFactory.build(locale_preferred=True, locale='fr', name='FROTTIS POUR DÉTECTER PALUDISME'),
                ConceptNameFactory.build(locale_preferred=False, locale='ht', name='tès MALARYA , kalitatif'),
                ConceptNameFactory.build(locale_preferred=False, locale='es', name='frotis de malaria (cualitativo)'),
                ConceptNameFactory.build(locale_preferred=False, locale='es', name='Frotis de paludismo'),
            ]
        )

        source.default_locale = 'en'
        source.supported_locales = ['en']
        source.save()
        self.assertEqual(concept.display_name, 'MALARIA SMEAR, QUALITATIVE')

        source.default_locale = 'fr'
        source.supported_locales = ['fr', 'en']
        source.save()
        self.assertEqual(concept.display_name, 'FROTTIS POUR DÉTECTER PALUDISME')

        source.default_locale = 'es'
        source.supported_locales = ['es']
        source.save()
        self.assertEqual(concept.display_name, 'Frotis de paludismo')

        source.default_locale = 'ht'
        source.supported_locales = ['ht', 'en']
        source.save()
        self.assertEqual(concept.display_name, 'tès MALARYA , kalitatif')

        source.default_locale = 'ti'
        source.supported_locales = ['ti']
        source.save()
        self.assertEqual(concept.display_name, 'MALARIA SMEAR, QUALITATIVE')  # system default locale = en

        source.default_locale = 'ti'
        source.supported_locales = ['ti', 'en']
        source.save()
        self.assertEqual(concept.display_name, 'MALARIA SMEAR, QUALITATIVE')

    def test_display_locale(self):
        preferred_locale = ConceptNameFactory.build(locale_preferred=True)
        concept = ConceptFactory(names=(preferred_locale,))

        self.assertEqual(concept.display_locale, preferred_locale.locale)

    def test_default_name_locales(self):
        es_locale = ConceptNameFactory.build(locale='es')
        en_locale = ConceptNameFactory.build(locale='en')
        concept = ConceptFactory(names=(es_locale, en_locale))

        default_name_locales = concept.default_name_locales

        self.assertEqual(default_name_locales.count(), 1)
        self.assertEqual(default_name_locales.first(), en_locale)

    def test_default_description_locales(self):
        es_locale = ConceptDescriptionFactory.build(locale='es')
        en_locale = ConceptDescriptionFactory.build(locale='en')
        concept = ConceptFactory(descriptions=(es_locale, en_locale))

        default_description_locales = concept.default_description_locales

        self.assertEqual(default_description_locales.count(), 1)
        self.assertEqual(default_description_locales.first(), en_locale)

    def test_names_for_default_locale(self):
        es_locale = ConceptNameFactory.build(locale='es', name='Not English')
        en_locale = ConceptNameFactory.build(locale='en', name='English')
        concept = ConceptFactory(names=(es_locale, en_locale))

        self.assertEqual(concept.names_for_default_locale, [en_locale.name])

    def test_descriptions_for_default_locale(self):
        es_locale = ConceptDescriptionFactory.build(locale='es', name='Not English')
        en_locale = ConceptDescriptionFactory.build(locale='en', name='English')
        concept = ConceptFactory(descriptions=(es_locale, en_locale))

        self.assertEqual(concept.descriptions_for_default_locale, [en_locale.name])

    def test_all_names(self):
        concept = ConceptFactory(
            names=[
                ConceptNameFactory.build(name="name1", locale='en', locale_preferred=True),
                ConceptNameFactory.build(name='name2', locale='en', type='Short')
            ]
        )

        self.assertEqual(concept.all_names, ['name1', 'name2'])

    def test_persist_new(self):
        source = OrganizationSourceFactory(version=HEAD)
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.version, str(concept.id))
        self.assertEqual(source.concepts_set.count(), 2)
        self.assertEqual(source.concepts.count(), 2)
        self.assertEqual(
            concept.uri,
            f'/orgs/{source.organization.mnemonic}/sources/{source.mnemonic}/concepts/{concept.mnemonic}/'
        )

    def test_persist_new_with_autoid_sequential(self):
        source = OrganizationSourceFactory(
            version=HEAD, autoid_concept_mnemonic='sequential', autoid_concept_external_id='sequential')
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'parent': source, 'mnemonic': None,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.mnemonic, '1')
        self.assertEqual(concept.external_id, '1')

        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'parent': source, 'mnemonic': None,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.mnemonic, '2')
        self.assertEqual(concept.external_id, '2')

        for concept in Concept.objects.filter(mnemonic='1'):
            concept.delete()

        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory),
            'parent': source,
            'mnemonic': None,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.mnemonic, '3')
        self.assertEqual(concept.external_id, '3')

        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory),
            'mnemonic': '1',
            'external_id': '1',
            'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.mnemonic, '1')
        self.assertEqual(concept.external_id, '1')

        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory),
            'mnemonic': None,
            'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.mnemonic, '4')
        self.assertEqual(concept.external_id, '4')

        source.autoid_concept_mnemonic_start_from = 100
        source.save()

        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory),
            'mnemonic': None,
            'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.mnemonic, '100')
        self.assertEqual(concept.external_id, '5')

        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory),
            'mnemonic': None,
            'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.mnemonic, '101')
        self.assertEqual(concept.external_id, '6')

    def test_persist_new_with_autoid_uuid(self):
        source = OrganizationSourceFactory(
            version=HEAD, autoid_concept_mnemonic='uuid', autoid_concept_external_id='uuid')
        concept1 = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'parent': source, 'mnemonic': None,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept1.errors, {})
        self.assertIsNotNone(concept1.id)
        self.assertTrue(len(concept1.mnemonic), 36)
        self.assertTrue(len(concept1.external_id), 36)

        concept2 = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'parent': source, 'mnemonic': None,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept2.errors, {})
        self.assertIsNotNone(concept2.id)
        self.assertTrue(len(concept2.mnemonic), 36)
        self.assertTrue(len(concept2.external_id), 36)
        self.assertIsNone(concept2.names.first().external_id)

        self.assertNotEqual(concept1.mnemonic, concept2.mnemonic)
        self.assertNotEqual(concept1.external_id, concept2.external_id)

    def test_persist_new_with_locale_autoid_uuid(self):
        source = OrganizationSourceFactory(
            version=HEAD, autoid_concept_mnemonic='uuid', autoid_concept_external_id='uuid',
            autoid_concept_name_external_id='uuid', autoid_concept_description_external_id='uuid'
        )
        concept1 = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'parent': source, 'mnemonic': None,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'descriptions': [ConceptDescriptionFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept1.errors, {})
        self.assertIsNotNone(concept1.id)
        self.assertTrue(len(concept1.mnemonic), 36)
        self.assertTrue(len(concept1.external_id), 36)
        self.assertTrue(len(concept1.names.first().external_id), 36)
        self.assertTrue(len(concept1.descriptions.first().external_id), 36)

        concept2 = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'parent': source, 'mnemonic': None,
            'names': [
                ConceptNameFactory.build(locale='en', name='English', locale_preferred=True, external_id=None)
            ],
            'descriptions': [
                ConceptDescriptionFactory.build(locale='en', name='English', locale_preferred=True, external_id=None)
            ]
        })

        self.assertEqual(concept2.errors, {})
        self.assertIsNotNone(concept2.id)
        self.assertTrue(len(concept2.mnemonic), 36)
        self.assertTrue(len(concept2.external_id), 36)
        self.assertTrue(len(concept2.names.first().external_id), 36)
        self.assertTrue(len(concept2.descriptions.first().external_id), 36)

        self.assertNotEqual(concept1.mnemonic, concept2.mnemonic)
        self.assertNotEqual(concept1.external_id, concept2.external_id)
        self.assertNotEqual(concept1.names.first().external_id, concept2.names.first().external_id)
        self.assertNotEqual(concept1.descriptions.first().external_id, concept2.descriptions.first().external_id)

        concept3 = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'parent': source, 'mnemonic': None,
            'names': [
                ConceptNameFactory.build(
                    locale='en', name='English', locale_preferred=True, external_id='name-ext-id')
            ],
            'descriptions': [
                ConceptDescriptionFactory.build(
                    locale='en', name='English', locale_preferred=True, external_id='desc-ext-id')
            ]
        })

        self.assertEqual(concept3.errors, {})
        self.assertTrue(concept3.names.first().external_id, 'name-ext-id')
        self.assertTrue(concept3.descriptions.first().external_id, 'desc-ext-id')

    def test_hierarchy_one_parent_child(self):
        parent_concept = ConceptFactory(
            names=[ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)])
        source = parent_concept.parent
        child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [parent_concept.uri]
        })

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertEqual(child_concept.errors, {})
        self.assertIsNotNone(child_concept.id)
        self.assertEqual(list(child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.get_latest_version().parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(parent_concept.child_concept_urls), [child_concept.uri])
        self.assertEqual(list(parent_concept_latest_version.child_concept_urls), [child_concept.uri])
        self.assertEqual(list(parent_concept_latest_version.prev_version.child_concept_urls), [])

        another_child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c2', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [parent_concept.uri]
        })

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertEqual(another_child_concept.errors, {})
        self.assertIsNotNone(another_child_concept.id)
        self.assertEqual(list(child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.get_latest_version().parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(another_child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(another_child_concept.get_latest_version().parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(another_child_concept.child_concept_urls), [])
        self.assertEqual(
            sorted(list(parent_concept.child_concept_urls)),
            sorted([child_concept.uri, another_child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.child_concept_urls)),
            sorted([child_concept.uri, another_child_concept.uri])
        )
        self.assertEqual(list(parent_concept_latest_version.prev_version.child_concept_urls), [child_concept.uri])

    def test_hierarchy(self):  # pylint: disable=too-many-statements
        # Av1
        parent_concept = ConceptFactory(
            mnemonic='A', names=[ConceptNameFactory.build(locale='en', name='Av1', locale_preferred=True)])
        self.assertEqual(parent_concept.versions.count(), 1)
        source = parent_concept.parent

        # Av1 -> None and Av2 -> Bv1
        child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'B', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='Bv1', locale_preferred=True)],
            'parent_concept_urls': [parent_concept.uri]
        })

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertEqual(child_concept.errors, {})
        self.assertIsNotNone(child_concept.id)
        self.assertEqual(parent_concept.versions.count(), 2)
        self.assertEqual(child_concept.versions.count(), 1)
        self.assertEqual(list(child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(parent_concept.child_concept_urls), [child_concept.uri])
        self.assertEqual(list(parent_concept_latest_version.child_concept_urls), [child_concept.uri])
        self.assertEqual(list(parent_concept_latest_version.prev_version.child_concept_urls), [])

        # Av1 -> None and Av2 -> Bv1,Bv2 and Bv2 -> Cv1
        child_child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'C', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='Cv1', locale_preferred=True)],
            'parent_concept_urls': [child_concept.uri]
        })

        self.assertEqual(child_child_concept.errors, {})
        self.assertIsNotNone(child_child_concept.id)
        self.assertEqual(parent_concept.versions.count(), 2)
        self.assertEqual(child_concept.versions.count(), 2)
        self.assertEqual(child_child_concept.versions.count(), 1)
        self.assertEqual(list(child_child_concept.parent_concept_urls), [child_concept.uri])
        self.assertEqual(list(child_child_concept.get_latest_version().parent_concept_urls), [child_concept.uri])
        self.assertEqual(list(child_child_concept.child_concept_urls), [])
        self.assertEqual(list(child_concept.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(list(child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.get_latest_version().child_concept_urls), [child_child_concept.uri])
        self.assertEqual(list(child_concept.get_latest_version().parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(parent_concept.child_concept_urls), [child_concept.uri])
        # Av1 -> None and Av2 -> Bv1,Bv2 -> Cv1 and Av3 -> Bv3,Cv2
        Concept.create_new_version_for(
            instance=child_child_concept.clone(),
            data={
                'parent_concept_urls': [parent_concept.uri],
                'names': [{'locale': 'en', 'name': 'Cv2', 'locale_preferred': True}]
            },
            user=child_child_concept.created_by
        )

        self.assertEqual(parent_concept.versions.count(), 3)
        self.assertEqual(child_concept.versions.count(), 3)
        self.assertEqual(child_child_concept.versions.count(), 2)

        child_child_latest_version = child_child_concept.get_latest_version()
        self.assertEqual(list(child_child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_child_latest_version.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_child_latest_version.prev_version.parent_concept_urls), [child_concept.url])

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertListEqual(
            sorted(list(parent_concept.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )
        self.assertEqual(list(parent_concept_latest_version.prev_version.child_concept_urls), [child_concept.url])

        child_latest_version = child_concept.get_latest_version()

        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(child_latest_version.child_concept_urls), [])

        self.assertEqual(list(child_latest_version.prev_version.child_concept_urls), [child_child_concept.uri])

        # Av1 -> None and Av2 -> Bv1,Bv2 -> Cv1 and Av3 -> Bv3,Cv2 and Av4 -> Bv4 -> Cv3
        Concept.create_new_version_for(
            instance=child_child_concept.clone(),
            data={
                'parent_concept_urls': [child_concept.uri],
                'names': [{'locale': 'en', 'name': 'Cv3', 'locale_preferred': True}]
            },
            user=child_child_concept.created_by
        )

        self.assertEqual(parent_concept.versions.count(), 4)
        self.assertEqual(child_concept.versions.count(), 4)
        self.assertEqual(child_child_concept.versions.count(), 3)

        child_child_latest_version = child_child_concept.get_latest_version()
        self.assertEqual(
            list(child_child_concept.parent_concept_urls), [child_concept.uri])
        self.assertEqual(
            list(child_child_latest_version.parent_concept_urls), [child_concept.uri])
        self.assertEqual(
            list(child_child_latest_version.prev_version.parent_concept_urls), [parent_concept.url])
        self.assertEqual(
            list(child_child_latest_version.prev_version.prev_version.parent_concept_urls), [child_concept.url])

        child_latest_version = child_concept.get_latest_version()
        self.assertEqual(list(child_concept.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(list(child_latest_version.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(
            list(child_latest_version.prev_version.child_concept_urls), []
        )
        self.assertEqual(
            list(child_latest_version.prev_version.prev_version.child_concept_urls),
            [child_child_concept.uri]
        )
        self.assertEqual(
            list(child_latest_version.prev_version.prev_version.prev_version.child_concept_urls),
            []
        )

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertListEqual(
            sorted(list(parent_concept.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.prev_version.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )
        self.assertEqual(
            list(parent_concept_latest_version.prev_version.prev_version.child_concept_urls),
            [child_concept.uri]
        )
        self.assertEqual(
            list(parent_concept_latest_version.prev_version.prev_version.prev_version.child_concept_urls),
            []
        )

        # Av1 -> None and Av2 -> Bv1,Bv2 -> Cv1 and Av3 -> Bv3,Cv2 and Av4 -> Bv4 -> Cv3 and Av4 -> Bv5 -> None and Cv4
        Concept.create_new_version_for(
            instance=child_child_concept.clone(),
            data={
                'parent_concept_urls': [],
                'names': [{'locale': 'en', 'name': 'Cv4', 'locale_preferred': True}]
            },
            user=child_child_concept.created_by
        )

        self.assertEqual(parent_concept.versions.count(), 4)
        self.assertEqual(child_concept.versions.count(), 5)
        self.assertEqual(child_child_concept.versions.count(), 4)

        child_child_latest_version = child_child_concept.get_latest_version()
        self.assertEqual(
            list(child_child_concept.parent_concept_urls), [])
        self.assertEqual(
            list(child_child_latest_version.parent_concept_urls), [])
        self.assertEqual(
            list(child_child_latest_version.prev_version.parent_concept_urls), [child_concept.uri])
        self.assertEqual(
            list(child_child_latest_version.prev_version.prev_version.parent_concept_urls), [parent_concept.url])
        self.assertEqual(
            list(child_child_latest_version.prev_version.prev_version.prev_version.parent_concept_urls),
            [child_concept.url]
        )

        child_latest_version = child_concept.get_latest_version()
        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(child_latest_version.child_concept_urls), [])
        self.assertEqual(list(child_latest_version.prev_version.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(
            list(child_latest_version.prev_version.prev_version.child_concept_urls), []
        )
        self.assertEqual(
            list(child_latest_version.prev_version.prev_version.prev_version.child_concept_urls),
            [child_child_concept.uri]
        )
        self.assertEqual(
            list(child_latest_version.prev_version.prev_version.prev_version.prev_version.child_concept_urls),
            []
        )

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertListEqual(
            sorted(list(parent_concept.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.prev_version.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )
        self.assertEqual(
            list(parent_concept_latest_version.prev_version.prev_version.child_concept_urls),
            [child_concept.uri]
        )
        self.assertEqual(
            list(parent_concept_latest_version.prev_version.prev_version.prev_version.child_concept_urls),
            []
        )

    def test_hierarchy_without_multiple_parent_versions(self):  # pylint: disable=too-many-statements
        # Av1
        parent_concept = ConceptFactory(mnemonic='A',
            names=[ConceptNameFactory.build(locale='en', name='Av1', locale_preferred=True)])
        self.assertEqual(parent_concept.versions.count(), 1)
        self.assertEqual(list(parent_concept.get_latest_version().child_concept_urls), [])
        source = parent_concept.parent

        # Av1 to Av1 -> Bv1
        child_concept = Concept.persist_new(data={
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'B', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='Bv1', locale_preferred=True)],
            'parent_concept_urls': [parent_concept.uri]
        }, create_parent_version=False)

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertEqual(child_concept.errors, {})
        self.assertIsNotNone(child_concept.id)
        self.assertEqual(parent_concept.versions.count(), 1)
        self.assertEqual(child_concept.versions.count(), 1)
        self.assertEqual(list(child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(parent_concept.child_concept_urls), [child_concept.uri])
        self.assertEqual(list(parent_concept_latest_version.child_concept_urls), [child_concept.uri])

        # Av1 to Av1 -> Bv1 to Av1 -> Bv1 -> Cv1
        child_child_concept = Concept.persist_new(data={
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'C', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='Cv1', locale_preferred=True)],
            'parent_concept_urls': [child_concept.uri]
        }, create_parent_version=False)

        self.assertEqual(child_child_concept.errors, {})
        self.assertIsNotNone(child_child_concept.id)
        self.assertEqual(parent_concept.versions.count(), 1)
        self.assertEqual(child_concept.versions.count(), 1)
        self.assertEqual(child_child_concept.versions.count(), 1)
        self.assertEqual(list(child_child_concept.parent_concept_urls), [child_concept.uri])
        self.assertEqual(list(child_child_concept.get_latest_version().parent_concept_urls), [child_concept.uri])
        self.assertEqual(list(child_child_concept.child_concept_urls), [])
        self.assertEqual(list(child_concept.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(list(child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.get_latest_version().child_concept_urls), [child_child_concept.uri])
        self.assertEqual(list(child_concept.get_latest_version().parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(parent_concept.child_concept_urls), [child_concept.uri])

        # Av1 to Av1 -> Bv1 to Av1 -> Bv1 -> Cv1 to Av1 -> Bv2,Cv2 and Bv1 -> Cv1
        Concept.create_new_version_for(
            instance=child_child_concept.clone(),
            data={
                'parent_concept_urls': [parent_concept.uri],
                'names': [{'locale': 'en', 'name': 'Cv2', 'locale_preferred': True}]
            },
            user=child_child_concept.created_by,
            create_parent_version=False
        )

        self.assertEqual(parent_concept.versions.count(), 1)
        self.assertEqual(child_concept.versions.count(), 2)
        self.assertEqual(child_child_concept.versions.count(), 2)

        child_child_latest_version = child_child_concept.get_latest_version()
        self.assertEqual(list(child_child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_child_latest_version.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_child_latest_version.prev_version.parent_concept_urls), [child_concept.url])

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertListEqual(
            sorted(list(parent_concept.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )

        child_latest_version = child_concept.get_latest_version()
        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(child_latest_version.child_concept_urls), [])
        self.assertEqual(list(child_latest_version.prev_version.child_concept_urls), [child_child_concept.uri])

        # Av1 -> Bv1 -> Cv1 to Av1 -> Bv2,Cv2 and Bv1 -> Cv1 to Av2 -> Bv2 -> Cv3 and Av1 -> Bv1, Cv2 and Bv1 -> Cv1
        Concept.create_new_version_for(
            instance=child_child_concept.clone(),
            data={
                'parent_concept_urls': [child_concept.uri],
                'names': [{'locale': 'en', 'name': 'Cv3', 'locale_preferred': True}]
            },
            user=child_child_concept.created_by,
            create_parent_version=False
        )

        self.assertEqual(parent_concept.versions.count(), 2)
        self.assertEqual(child_concept.versions.count(), 2)
        self.assertEqual(child_child_concept.versions.count(), 3)

        child_child_latest_version = child_child_concept.get_latest_version()
        self.assertEqual(
            list(child_child_concept.parent_concept_urls), [child_concept.uri])
        self.assertEqual(
            list(child_child_latest_version.parent_concept_urls), [child_concept.uri])
        self.assertEqual(
            list(child_child_latest_version.prev_version.parent_concept_urls), [parent_concept.url])
        self.assertEqual(
            list(child_child_latest_version.prev_version.prev_version.parent_concept_urls), [child_concept.url])

        child_latest_version = child_concept.get_latest_version()
        self.assertEqual(list(child_concept.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(list(child_latest_version.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(
            list(child_latest_version.prev_version.child_concept_urls), [child_child_concept.uri]
        )
        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertListEqual(
            sorted(list(parent_concept.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.prev_version.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )

        # Av1 -> Bv1 -> Cv1 to Av1 -> Bv2,Cv2 and Bv1 -> Cv1 to Av2 -> Bv2 -> Cv3 and Av1 -> Bv1, Cv2 and Bv1 -> Cv1 to
        # Av2 -> Bv3 and Bv2 -> Cv3 and Av1 -> Bv1, Cv2 and Bv1 -> Cv1 and Cv4
        Concept.create_new_version_for(
            instance=child_child_concept.clone(),
            data={
                'parent_concept_urls': [],
                'names': [{'locale': 'en', 'name': 'Cv4', 'locale_preferred': True}]
            },
            user=child_child_concept.created_by,
            create_parent_version=False
        )

        self.assertEqual(parent_concept.versions.count(), 2)
        self.assertEqual(child_concept.versions.count(), 3)
        self.assertEqual(child_child_concept.versions.count(), 4)

        child_child_latest_version = child_child_concept.get_latest_version()
        self.assertEqual(
            list(child_child_concept.parent_concept_urls), [])
        self.assertEqual(
            list(child_child_latest_version.parent_concept_urls), [])
        self.assertEqual(
            list(child_child_latest_version.prev_version.parent_concept_urls), [child_concept.uri])
        self.assertEqual(
            list(child_child_latest_version.prev_version.prev_version.parent_concept_urls), [parent_concept.url])
        self.assertEqual(
            list(child_child_latest_version.prev_version.prev_version.prev_version.parent_concept_urls),
            [child_concept.url]
        )

        child_latest_version = child_concept.get_latest_version()
        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(child_latest_version.child_concept_urls), [])
        self.assertEqual(list(child_latest_version.prev_version.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(
            list(child_latest_version.prev_version.prev_version.child_concept_urls), [child_child_concept.uri]
        )

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertListEqual(
            sorted(list(parent_concept.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.prev_version.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )

    def test_clone(self):
        en_locale = ConceptNameFactory.build(locale='en', name='English')
        es_locale_description = ConceptDescriptionFactory.build(locale='es', name='Not English')
        en_locale_description = ConceptDescriptionFactory.build(locale='en', name='English')

        concept = ConceptFactory(
            descriptions=(es_locale_description, en_locale_description), names=(en_locale,), released=True)
        cloned_concept = concept.clone()

        self.assertTrue(cloned_concept.version.startswith('--TEMP--'))
        self.assertEqual(cloned_concept.mnemonic, concept.mnemonic)
        self.assertEqual(cloned_concept.parent, concept.parent)
        self.assertEqual(len(cloned_concept.cloned_names), concept.names.count())
        self.assertEqual(len(cloned_concept.cloned_descriptions), concept.descriptions.count())
        self.assertTrue(cloned_concept.released)

    def test_version_for_concept(self):
        concept = ConceptFactory(released=True)
        source = OrganizationSourceFactory()

        concept_version = Concept.version_for_concept(concept, 'v1.0', source)

        self.assertEqual(concept_version.parent, source)
        self.assertEqual(concept_version.version, 'v1.0')
        self.assertEqual(concept_version.created_by_id, concept.created_by_id)
        self.assertEqual(concept_version.updated_by_id, concept.updated_by_id)
        self.assertEqual(concept_version.mnemonic, concept.mnemonic)
        self.assertFalse(concept_version.released)

    def test_save_as_new_version(self):
        es_description = ConceptDescriptionFactory.build(locale='es', name='Not English')
        en_description = ConceptDescriptionFactory.build(locale='en', name='English')
        en_name = ConceptNameFactory.build(locale='en', name='English')

        source_head = OrganizationSourceFactory(version=HEAD)
        source_version0 = OrganizationSourceFactory(
            version='v0', mnemonic=source_head.mnemonic, organization=source_head.organization
        )

        self.assertEqual(source_head.versions.count(), 2)

        concept = ConceptFactory(
            descriptions=(es_description, en_description),
            names=(en_name,),
            parent=source_head
        )
        source_version0.concepts.add(concept)
        cloned_concept = Concept.version_for_concept(concept, 'v1', source_head)
        cloned_concept.datatype = 'foobar'

        self.assertEqual(cloned_concept.save_as_new_version(concept.created_by), {})

        persisted_concept = Concept.objects.filter(
            mnemonic=cloned_concept.mnemonic, version=cloned_concept.version
        ).first()
        self.assertEqual(persisted_concept.names.count(), 1)
        self.assertEqual(persisted_concept.descriptions.count(), 2)
        self.assertEqual(persisted_concept.parent, source_head)
        self.assertEqual(persisted_concept.sources.count(), 1)
        self.assertEqual(
            persisted_concept.uri,
            f'/orgs/{source_head.organization.mnemonic}/sources/{source_head.mnemonic}/'
            f'concepts/{persisted_concept.mnemonic}/{persisted_concept.version}/'
        )
        self.assertEqual(
            persisted_concept.version_url, persisted_concept.uri
        )

    def test_retire(self):
        source = OrganizationSourceFactory(version=HEAD)
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })
        concept_v1 = concept.clone()
        concept_v1.datatype = 'foobar'
        concept_v1.save_as_new_version(concept.created_by)
        concept_v1 = Concept.objects.order_by('-created_at').first()
        concept.refresh_from_db()

        self.assertEqual(concept.versions.count(), 2)
        self.assertFalse(concept.retired)
        self.assertFalse(concept.is_latest_version)
        self.assertTrue(concept.is_versioned_object)
        self.assertTrue(concept_v1.is_latest_version)

        concept_v1.retire(concept_v1.created_by, 'Forceful retirement')  # concept will become old/prev version
        concept.refresh_from_db()
        concept_v1.refresh_from_db()

        self.assertFalse(concept_v1.is_latest_version)
        self.assertEqual(concept.versions.count(), 3)
        self.assertTrue(concept.retired)
        latest_version = concept.get_latest_version()
        self.assertTrue(latest_version.retired)
        self.assertEqual(latest_version.comment, 'Forceful retirement')

        self.assertEqual(
            concept.retire(concept.created_by),
            {'__all__': CONCEPT_IS_ALREADY_RETIRED}
        )

    def test_unretire(self):
        source = OrganizationSourceFactory(version=HEAD)
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source, 'retired': True,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })
        concept_v1 = concept.clone()
        concept_v1.datatype = 'foobar'
        concept_v1.save_as_new_version(concept.created_by)
        concept_v1 = Concept.objects.order_by('-created_at').first()
        concept.refresh_from_db()

        self.assertEqual(concept.versions.count(), 2)
        self.assertTrue(concept.retired)
        self.assertFalse(concept.is_latest_version)
        self.assertTrue(concept.is_versioned_object)
        self.assertTrue(concept_v1.is_latest_version)

        concept_v1.unretire(concept.created_by, 'World needs you!')  # concept will become old/prev version
        concept.refresh_from_db()
        concept_v1.refresh_from_db()

        self.assertFalse(concept_v1.is_latest_version)
        self.assertEqual(concept.versions.count(), 3)
        self.assertFalse(concept.retired)
        latest_version = concept.get_latest_version()
        self.assertFalse(latest_version.retired)
        self.assertEqual(latest_version.comment, 'World needs you!')

        self.assertEqual(
            concept.unretire(concept.created_by),
            {'__all__': CONCEPT_IS_ALREADY_NOT_RETIRED}
        )

    def test_concept_access_changes_with_source(self):
        source = OrganizationSourceFactory(version=HEAD)
        self.assertEqual(source.public_access, ACCESS_TYPE_EDIT)
        concept = ConceptFactory(parent=source, public_access=ACCESS_TYPE_EDIT)

        self.assertEqual(concept.public_access, ACCESS_TYPE_EDIT)

        source.public_access = ACCESS_TYPE_VIEW
        source._should_update_public_access = True  # pylint: disable=protected-access
        source.save()
        concept.refresh_from_db()

        self.assertEqual(source.public_access, ACCESS_TYPE_VIEW)
        self.assertEqual(source.public_access, concept.public_access)

    def test_get_latest_versions_for_queryset(self):  # pylint: disable=too-many-locals
        self.assertEqual(Concept.get_latest_versions_for_queryset(Concept.objects.none()).count(), 0)

        source1 = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source1, mnemonic='common-name-1')
        concept1_latest = concept1.get_latest_version()
        ConceptFactory(version='v1', parent=source1, is_latest_version=False, mnemonic=concept1.mnemonic)

        concept2 = ConceptFactory(parent=source1)
        concept2_latest = concept2.get_latest_version()
        ConceptFactory(version='v1', parent=source1, is_latest_version=False, mnemonic=concept2.mnemonic)

        concept3 = ConceptFactory(parent=source1, mnemonic='common-name-2')
        concept3_latest = concept3.get_latest_version()
        ConceptFactory(version='v1', parent=source1, is_latest_version=False, mnemonic=concept3.mnemonic)

        source2 = OrganizationSourceFactory()

        concept4 = ConceptFactory(parent=source2, mnemonic='common-name-1')
        concept4_latest = concept4.get_latest_version()
        ConceptFactory(version='v1', parent=source2, is_latest_version=False, mnemonic=concept4.mnemonic)

        concept5 = ConceptFactory(parent=source2)
        concept5_latest = concept5.get_latest_version()
        ConceptFactory(version='v1', parent=source2, is_latest_version=False, mnemonic=concept5.mnemonic)

        concept6 = ConceptFactory(parent=source2, mnemonic='common-name-2')
        concept6_latest = concept6.get_latest_version()
        ConceptFactory(version='v1', parent=source2, is_latest_version=False, mnemonic=concept6.mnemonic)

        latest_versions = Concept.get_latest_versions_for_queryset(Concept.objects.filter(parent=source1))

        self.assertEqual(latest_versions.count(), 3)
        self.assertEqual(
            list(latest_versions.order_by('created_at')),
            [concept1_latest, concept2_latest, concept3_latest]
        )

        latest_versions = Concept.get_latest_versions_for_queryset(Concept.objects.filter(parent=source2))

        self.assertEqual(latest_versions.count(), 3)
        self.assertEqual(
            list(latest_versions.order_by('created_at')),
            [concept4_latest, concept5_latest, concept6_latest]
        )

        latest_versions = Concept.get_latest_versions_for_queryset(Concept.objects.filter(mnemonic='common-name-1'))

        self.assertEqual(latest_versions.count(), 2)
        self.assertEqual(
            list(latest_versions.order_by('created_at')),
            [concept1_latest, concept4_latest]
        )

        latest_versions = Concept.get_latest_versions_for_queryset(
            Concept.objects.filter(mnemonic='common-name-2', version='v1')
        )

        self.assertEqual(latest_versions.count(), 2)
        self.assertEqual(
            list(latest_versions.order_by('created_at')),
            [concept3_latest, concept6_latest]
        )

    def test_custom_validation_schema(self):
        from core.sources.models import Source
        self.assertEqual(
            Concept(parent=Source(custom_validation_schema='foobar')).custom_validation_schema,
            'foobar'
        )

    def test_get_mappings(self):   # pylint: disable=too-many-locals
        source1 = OrganizationSourceFactory()
        source2 = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source1)
        concept2 = ConceptFactory(parent=source1)
        concept3 = ConceptFactory(parent=source2)
        concept4 = ConceptFactory(parent=source2)

        mapping1 = MappingFactory(from_concept=concept1, to_concept=concept2, parent=source1)
        mapping2 = MappingFactory(from_concept=concept1, to_concept=concept3, parent=source1)
        mapping3 = MappingFactory(from_concept=concept1, to_concept=concept3, parent=source2)
        mapping4 = MappingFactory(from_concept=concept4, to_concept=concept1, parent=source1)
        mapping5 = MappingFactory(from_concept=concept4, to_concept=concept1, parent=source2)
        MappingFactory(from_concept=concept1, to_concept=concept2, parent=source2)

        mappings = concept1.get_unidirectional_mappings()
        self.assertCountEqual(list(mappings), [mapping2, mapping1])

        mappings = concept1.get_indirect_mappings()
        self.assertCountEqual(list(mappings), [mapping4])

        mappings = concept1.get_bidirectional_mappings()
        self.assertCountEqual(list(mappings), [mapping4, mapping2, mapping1])

        mappings = concept2.get_unidirectional_mappings()
        self.assertEqual(mappings.count(), 0)

        mappings = concept2.get_indirect_mappings()
        self.assertCountEqual(list(mappings), [mapping1])

        mappings = concept3.get_unidirectional_mappings()
        self.assertEqual(mappings.count(), 0)

        mappings = concept3.get_indirect_mappings()
        self.assertCountEqual(list(mappings), [mapping3])

        mappings = concept4.get_unidirectional_mappings()
        self.assertCountEqual(list(mappings), [mapping5])

        mappings = concept4.get_indirect_mappings()
        self.assertEqual(mappings.count(), 0)

    def test_get_parent_and_owner_filters_from_uri(self):
        self.assertEqual(Concept.get_parent_and_owner_filters_from_uri(None), {})
        self.assertEqual(Concept.get_parent_and_owner_filters_from_uri(''), {})
        self.assertEqual(Concept.get_parent_and_owner_filters_from_uri('/bar/'), {})
        self.assertEqual(Concept.get_parent_and_owner_filters_from_uri('/concepts/'), {})
        self.assertEqual(Concept.get_parent_and_owner_filters_from_uri('/concepts/concept1/'), {})

        self.assertEqual(
            Concept.get_parent_and_owner_filters_from_uri('/users/foo/sources/bar/concepts/'),
            {
                'parent__mnemonic': 'bar',
                'parent__user__username': 'foo'
            }
        )
        self.assertEqual(
            Concept.get_parent_and_owner_filters_from_uri('/users/foo/sources/bar/concepts/concept1/'),
            {
                'parent__mnemonic': 'bar',
                'parent__user__username': 'foo'
            }
        )
        self.assertEqual(
            Concept.get_parent_and_owner_filters_from_uri('/orgs/foo/sources/bar/concepts/concept1/'),
            {
                'parent__mnemonic': 'bar',
                'parent__organization__mnemonic': 'foo'
            }
        )

    def test_get_hierarchy_path(self):
        parent_concept = ConceptFactory()
        self.assertEqual(parent_concept.get_hierarchy_path(), [])

        child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': parent_concept.parent,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [parent_concept.uri]
        })

        self.assertEqual(parent_concept.get_hierarchy_path(), [])
        self.assertEqual(child_concept.get_hierarchy_path(), [parent_concept.uri])

        child_child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c2', 'parent': parent_concept.parent,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [child_concept.uri]
        })

        self.assertEqual(parent_concept.get_hierarchy_path(), [])
        self.assertEqual(child_concept.get_hierarchy_path(), [parent_concept.uri])
        self.assertEqual(child_child_concept.get_hierarchy_path(), [parent_concept.uri, child_concept.uri])

    def test_child_concept_queryset(self):
        parent_concept = ConceptFactory()
        self.assertEqual(parent_concept.child_concept_queryset().count(), 0)
        self.assertEqual(parent_concept.parent_concept_urls, [])

        child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': parent_concept.parent,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [parent_concept.uri]
        })
        self.assertEqual(
            list(parent_concept.child_concept_queryset().values_list('uri', flat=True)), [child_concept.uri])
        self.assertEqual(
            list(child_concept.child_concept_queryset().values_list('uri', flat=True)), [])
        self.assertEqual(child_concept.parent_concept_urls, [parent_concept.uri])

        child_child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c2', 'parent': parent_concept.parent,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [child_concept.uri]
        })
        self.assertEqual(
            list(parent_concept.child_concept_queryset().values_list('uri', flat=True)), [child_concept.uri])
        self.assertEqual(
            list(child_concept.child_concept_queryset().values_list('uri', flat=True)), [child_child_concept.uri])
        self.assertEqual(
            list(child_child_concept.child_concept_queryset().values_list('uri', flat=True)), [])
        self.assertEqual(child_child_concept.parent_concept_urls, [child_concept.uri])
        self.assertEqual(parent_concept.children_concepts_count, 1)

    def test_parent_concept_queryset(self):
        parent_concept = ConceptFactory()
        self.assertEqual(parent_concept.parent_concept_queryset().count(), 0)
        self.assertEqual(parent_concept.parent_concept_urls, [])

        child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': parent_concept.parent,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [parent_concept.uri]
        })
        self.assertEqual(
            list(parent_concept.parent_concept_queryset().values_list('uri', flat=True)), [])
        self.assertEqual(
            list(child_concept.parent_concept_queryset().values_list('uri', flat=True)), [parent_concept.uri])
        self.assertEqual(child_concept.parent_concept_urls, [parent_concept.uri])

        child_child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c2', 'parent': parent_concept.parent,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [child_concept.uri]
        })
        self.assertEqual(
            list(parent_concept.parent_concept_queryset().values_list('uri', flat=True)), [])
        self.assertEqual(
            list(child_concept.parent_concept_queryset().values_list('uri', flat=True)), [parent_concept.uri])
        self.assertEqual(
            list(child_child_concept.parent_concept_queryset().values_list('uri', flat=True)), [child_concept.uri])
        self.assertEqual(child_child_concept.parent_concept_urls, [child_concept.uri])
        self.assertEqual(child_child_concept.parent_concepts_count, 1)

    def test_has_children(self):
        concept = ConceptFactory()

        self.assertFalse(concept.has_children)

        concept2 = ConceptFactory()
        concept2.parent_concepts.add(concept)

        self.assertTrue(concept.has_children)
        self.assertFalse(concept2.has_children)

    def test_get_serializer_class(self):
        self.assertEqual(Concept.get_serializer_class(), ConceptListSerializer)
        self.assertEqual(Concept.get_serializer_class(version=True), ConceptVersionListSerializer)
        self.assertEqual(Concept.get_serializer_class(verbose=True), ConceptDetailSerializer)
        self.assertEqual(Concept.get_serializer_class(verbose=True, version=True), ConceptVersionDetailSerializer)
        self.assertEqual(Concept.get_serializer_class(brief=True), ConceptMinimalSerializer)

    def test_from_uri_queryset_for_source_and_source_version(self):
        source = OrganizationSourceFactory()
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
        })
        self.assertEqual(concept.versions.count(), 1)

        concepts = Concept.from_uri_queryset(source.uri + 'concepts/')
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept.get_latest_version().id)

        source_version1 = OrganizationSourceFactory(
            version='v1', mnemonic=source.mnemonic, organization=source.organization)
        source_version1.seed_concepts(index=False)
        self.assertEqual(source_version1.concepts.count(), 1)

        concepts = Concept.from_uri_queryset(source_version1.uri + 'concepts/')
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept.get_latest_version().id)

        source_version2 = OrganizationSourceFactory(
            version='v2', mnemonic=source.mnemonic, organization=source.organization)

        cloned_concept = Concept.version_for_concept(concept, 'v1', source)
        cloned_concept.datatype = 'foobar'
        cloned_concept.save_as_new_version(concept.created_by)

        self.assertEqual(concept.versions.count(), 2)

        concept_v1 = concept.get_latest_version()
        self.assertTrue(concept_v1.is_latest_version)
        concepts = Concept.from_uri_queryset(concept_v1.version_url)
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept_v1.id)

        concept_prev_version = concept_v1.prev_version
        self.assertFalse(concept_prev_version.is_latest_version)
        concepts = Concept.from_uri_queryset(concept_prev_version.version_url)
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept_prev_version.id)

        concepts = Concept.from_uri_queryset(source.uri + 'concepts/')
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept_v1.id)

        concepts = Concept.from_uri_queryset(source_version2.uri + 'concepts/')

        self.assertEqual(concepts.count(), 0)

        concepts = Concept.from_uri_queryset(source_version1.uri + 'concepts/')
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept_prev_version.id)

    def test_from_uri_queryset_for_collection_and_collection_version(self):
        source = OrganizationSourceFactory()
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
        })
        source_version1 = OrganizationSourceFactory(
            version='v1', mnemonic=source.mnemonic, organization=source.organization)
        source_version1.seed_concepts(index=False)
        OrganizationSourceFactory(
            version='v2', mnemonic=source.mnemonic, organization=source.organization)
        cloned_concept = Concept.version_for_concept(concept, 'v1', source)
        cloned_concept.datatype = 'foobar'
        cloned_concept.save_as_new_version(concept.created_by)
        self.assertEqual(concept.versions.count(), 2)

        concept_v1 = concept.get_latest_version()
        collection = OrganizationCollectionFactory()
        reference = CollectionReference(
            expression=concept_v1.version_url, collection=collection, system=concept_v1.parent.uri, version='HEAD',
            code=concept_v1.mnemonic, resource_version=concept_v1.version
        )
        reference.clean()
        reference.save()

        collection_version1 = OrganizationCollectionFactory(
            version='v1', mnemonic=collection.mnemonic, organization=collection.organization)
        collection_version1.seed_references()
        expansion = ExpansionFactory(collection_version=collection_version1)
        expansion.seed_children(index=False)

        self.assertEqual(expansion.concepts.count(), 1)

        concepts = Concept.from_uri_queryset(expansion.uri + 'concepts/')
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept_v1.id)

        concepts = Concept.from_uri_queryset(collection_version1.uri + 'concepts/')
        self.assertEqual(concepts.count(), 0)

        collection_version1.expansion_uri = expansion.uri
        collection_version1.save()

        concepts = Concept.from_uri_queryset(collection_version1.uri + 'concepts/')
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept_v1.id)

        concepts = Concept.from_uri_queryset(collection.uri + 'concepts/')
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept_v1.id)

    def test_cascade_as_hierarchy(self):
        source = OrganizationSourceFactory()
        root = ConceptFactory(parent=source, mnemonic='root')
        root_child = ConceptFactory(parent=source, mnemonic='root-child')
        root_child.parent_concepts.add(root)
        root_child_child1 = ConceptFactory(parent=source, mnemonic='root-child-child1')
        root_child_child1.parent_concepts.add(root_child)
        root_child_child2 = ConceptFactory(parent=source, mnemonic='root-child-child2')
        root_child_child2.parent_concepts.add(root_child)
        root_child_child2_child = ConceptFactory(parent=source, mnemonic='root-child-child2-child')
        root_child_child2_child.parent_concepts.add(root_child_child2)

        root_cascaded = root.cascade_as_hierarchy(root.sources.filter(version='HEAD').first())

        self.assertTrue(isinstance(root_cascaded, Concept))
        self.assertEqual(root_cascaded.uri, root.uri)

        root_cascaded_children = root_cascaded.cascaded_entries
        self.assertEqual(len(root_cascaded_children['concepts']), 1)
        self.assertEqual(root_cascaded_children['mappings'].count(), 0)

        root_child_cascaded = root_cascaded_children['concepts'][0]
        root_child_cascaded_children = root_child_cascaded.cascaded_entries
        self.assertEqual(len(root_child_cascaded_children['concepts']), 2)
        self.assertEqual(root_child_cascaded_children['mappings'].count(), 0)

        root_child_child1_cascaded_children = [
            child for child in root_child_cascaded_children['concepts'] if child.mnemonic == 'root-child-child1'
        ][0].cascaded_entries
        root_child_child2_cascaded_children = [
            child for child in root_child_cascaded_children['concepts'] if child.mnemonic == 'root-child-child2'
        ][0].cascaded_entries

        self.assertEqual(len(root_child_child1_cascaded_children['concepts']), 0)
        self.assertEqual(len(root_child_child1_cascaded_children['mappings']), 0)

        self.assertEqual(len(root_child_child2_cascaded_children['concepts']), 1)
        self.assertEqual(len(root_child_child2_cascaded_children['mappings']), 0)

    def test_cascade_as_hierarchy_reverse(self):
        source = OrganizationSourceFactory()
        root = ConceptFactory(parent=source, mnemonic='root')
        root_child = ConceptFactory(parent=source, mnemonic='root-child')
        root_child.parent_concepts.add(root)
        root_child_child1 = ConceptFactory(parent=source, mnemonic='root-child-child1')
        root_child_child1.parent_concepts.add(root_child)
        root_child_child2 = ConceptFactory(parent=source, mnemonic='root-child-child2')
        root_child_child2.parent_concepts.add(root_child)
        root_child_child2_child = ConceptFactory(parent=source, mnemonic='root-child-child2-child')
        root_child_child2_child.parent_concepts.add(root_child_child2)

        root_child_child2_child_cascaded = root_child_child2_child.cascade_as_hierarchy(
            root_child_child2_child.sources.filter(version='HEAD').first(), reverse=True)

        self.assertEqual(root_child_child2_child_cascaded.uri, root_child_child2_child.uri)
        root_child_child2_child_cascaded_entries = root_child_child2_child_cascaded.cascaded_entries
        self.assertEqual(len(root_child_child2_child_cascaded_entries['concepts']), 1)
        self.assertEqual(len(root_child_child2_child_cascaded_entries['mappings']), 0)
        self.assertEqual(root_child_child2_child_cascaded_entries['concepts'][0].url, root_child_child2.url)
        self.assertEqual(
            root_child_child2_child_cascaded_entries['concepts'][0].cascaded_entries['concepts'][0].url,
            root_child.url
        )
        self.assertEqual(
            root_child_child2_child_cascaded_entries['concepts'][0].cascaded_entries['concepts'][
                0].cascaded_entries['concepts'][0].url,
            root.url
        )

    @patch('core.common.checksums.ChecksumBase.generate')
    def test_checksum(self, checksum_generate_mock):
        checksum_generate_mock.side_effect = [
            'standard-checksum', 'smart-checksum'
        ]
        concept = ConceptFactory()

        self.assertEqual(concept.checksums, {})
        self.assertEqual(concept.checksum, 'standard-checksum')
        self.assertEqual(
            concept.checksums,
            {
                'standard': 'standard-checksum',
                'smart': 'smart-checksum',
            }
        )
        checksum_generate_mock.assert_called()

    def test_get_checksums(self):
        parent = OrganizationSourceFactory()
        concept = ConceptFactory(parent=parent)
        ConceptDescriptionFactory(concept=concept)
        ConceptNameFactory(concept=concept)
        MappingFactory(from_concept=concept, parent=parent)

        checksums = concept.get_checksums()
        concept.refresh_from_db()

        self.assertEqual(
            checksums,
            {'standard': ANY, 'smart': ANY}
        )
        self.assertTrue(checksums['standard'] == concept.checksums['standard'] == concept.checksum)
        self.assertTrue(checksums['smart'] == concept.checksums['smart'])

    def test_properties(self):
        source = OrganizationSourceFactory(properties=[])
        concept1 = ConceptFactory(parent=source, concept_class='Diagnosis', datatype='N/A')

        concept1.refresh_from_db()

        for _concept in [concept1, concept1.get_latest_version()]:
            self.assertEqual(_concept.extras, {})
            self.assertEqual(_concept.properties, [])
            self.assertEqual(_concept.datatype, 'N/A')
            self.assertEqual(_concept.concept_class, 'Diagnosis')

        source.properties = [
            {
                "code": "concept_class",
                "description": "Type of concept",
                "type": "code",   # e.g. from /orgs/OCL/collections/Classes/
                "include_in_concept_summary": True
            },
            {
                "code": "datatype",
                "description": "Type of data captured for this concept",
                "type": "code",   # e.g. from /orgs/OCL/collections/Datatypes/
                "include_in_concept_summary": True
            },
            {
                "code": "units",
                "description": "Units of measurement",
                "type": "string"
            }
        ]
        source.save()

        concept2 = ConceptFactory(parent=source, concept_class='Diagnosis', datatype='N/A')
        concept3 = ConceptFactory(
            parent=source, concept_class='Diagnosis', datatype='N/A', extras={'foo': 'bar', 'units': 'parts/microliter'}
        )

        concept2.refresh_from_db()
        concept3.refresh_from_db()

        for _concept in [concept2, concept2.get_latest_version()]:
            self.assertEqual(_concept.extras, {})
            self.assertEqual(
                _concept.properties,
                [
                    {'code': 'concept_class', 'valueCode': 'Diagnosis'},
                    {'code': 'datatype', 'valueCode': 'N/A'},
                    {'code': 'units', 'valueString': None}
                ]
            )
            self.assertEqual(
                _concept.summary_properties,
                [
                    {'code': 'concept_class', 'valueCode': 'Diagnosis'},
                    {'code': 'datatype', 'valueCode': 'N/A'},
                ]
            )
        for _concept in [concept3, concept3.get_latest_version()]:
            self.assertEqual(
                _concept.extras,
                {'foo': 'bar', 'units': 'parts/microliter'})
            self.assertEqual(
                _concept.properties,
                [
                    {'code': 'concept_class', 'valueCode': 'Diagnosis'},
                    {'code': 'datatype', 'valueCode': 'N/A'},
                    {'code': 'units', 'valueString': 'parts/microliter'}
                ]
            )
            self.assertEqual(
                _concept.summary_properties,
                [
                    {'code': 'concept_class', 'valueCode': 'Diagnosis'},
                    {'code': 'datatype', 'valueCode': 'N/A'},
                ]
            )


class OpenMRSConceptValidatorTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.create_lookup_concept_classes()

    def test_concept_class_is_valid_attribute_negative(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new(
            {
                'mnemonic': 'concept1',
                'version': HEAD,
                'parent': source,
                'concept_class': 'XYZQWERT',
                'datatype': 'None',
                'names': [ConceptNameFactory.build(name='Grip', locale='es', locale_preferred=True)]
            }
        )

        self.assertEqual(concept.errors, {'concept_class': [OPENMRS_CONCEPT_CLASS]})

    def test_data_type_is_valid_attribute_negative(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new(
            {
                'mnemonic': 'concept1',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'XYZWERRTR',
                'names': [ConceptNameFactory.build(name='Grip', locale='es', locale_preferred=True)]
            }
        )
        self.assertEqual(
            concept.errors,
            {'data_type': [OPENMRS_DATATYPE]}
        )

    def test_description_type_is_valid_attribute_negative(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new(
            {
                'mnemonic': 'concept1',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [ConceptNameFactory.build(locale_preferred=True)],
                'descriptions': [ConceptDescriptionFactory.build(type='XYZWERRTR')]
            }
        )

        self.assertEqual(
            concept.errors,
            {'descriptions': [OPENMRS_DESCRIPTION_TYPE]}
        )

    def test_name_locale_is_valid_attribute_negative(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new(
            {
                'mnemonic': 'concept1',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [ConceptNameFactory.build(locale_preferred=True, locale='FOOBAR')],
                'descriptions': [ConceptDescriptionFactory.build(locale_preferred=True, type='Definition')]
            }
        )

        self.assertEqual(
            concept.errors,
            {'names': [OPENMRS_NAME_LOCALE]}
        )

    def test_description_locale_is_valid_attribute_negative(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new(
            {
                'mnemonic': 'concept1',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [ConceptNameFactory.build(locale_preferred=True)],
                'descriptions': [ConceptDescriptionFactory.build(locale_preferred=True, locale='FOOBAR')]
            }
        )
        self.assertEqual(
            concept.errors,
            {'descriptions': [OPENMRS_DESCRIPTION_TYPE]}
        )

    def test_concept_should_have_exactly_one_preferred_name_per_locale(self):
        name_en1 = ConceptNameFactory.build(name='PreferredName1', locale_preferred=True)
        name_en2 = ConceptNameFactory.build(name='PreferredName2', locale_preferred=True)
        name_tr = ConceptNameFactory.build(name='PreferredName3', locale="tr", locale_preferred=True)
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [name_en1, name_en2, name_tr]
            }
        )

        self.assertEqual(
            concept.errors,
            {
                'names': [
                    OPENMRS_MUST_HAVE_EXACTLY_ONE_PREFERRED_NAME + ': PreferredName2 (locale: en, preferred: yes)']
            }
        )

    def test_concepts_should_have_unique_fully_specified_name_per_locale(self):
        name_fully_specified1 = ConceptNameFactory.build(name='FullySpecifiedName1')

        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)
        concept1_data = {
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [name_fully_specified1]
        }
        concept2_data = {
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c2', 'parent': source,
            'names': [name_fully_specified1]
        }
        concept1 = Concept.persist_new(concept1_data)
        concept2 = Concept.persist_new(concept2_data)

        self.assertEqual(concept1.errors, {})
        self.assertEqual(
            concept2.errors,
            {
                'names': [OPENMRS_FULLY_SPECIFIED_NAME_UNIQUE_PER_SOURCE_LOCALE +
                          ': FullySpecifiedName1 (locale: en, preferred: no)']
            }
        )

    def test_at_least_one_fully_specified_name_per_concept_negative(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name='Fully Specified Name 1', locale='tr', type='Short'),
                    ConceptNameFactory.build(name='Fully Specified Name 2', locale='en', type='Short')
                ]
            }
        )
        self.assertEqual(
            concept.errors,
            {'names': [OPENMRS_AT_LEAST_ONE_FULLY_SPECIFIED_NAME]}
        )

    def test_duplicate_preferred_name_per_source_should_fail(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)
        concept1 = Concept.persist_new(
            {
                'mnemonic': 'concept1',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(
                        name='Concept Non Unique Preferred Name', locale='en',
                        locale_preferred=True, type='Fully Specified'
                    ),
                ]
            }
        )
        concept2 = Concept.persist_new(
            {
                'mnemonic': 'concept2',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(
                        name='Concept Non Unique Preferred Name', locale='en', locale_preferred=True, type='None'
                    ),
                    ConceptNameFactory.build(
                        name='any name', locale='en', locale_preferred=False, type='Fully Specified'
                    ),
                ]
            }
        )

        self.assertEqual(concept1.errors, {})
        self.assertEqual(
            concept2.errors,
            {
                'names': [OPENMRS_PREFERRED_NAME_UNIQUE_PER_SOURCE_LOCALE +
                          ': Concept Non Unique Preferred Name (locale: en, preferred: yes)']
            }
        )

    def test_unique_preferred_name_per_locale_within_concept_negative(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept1',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(
                        name='Concept Non Unique Preferred Name', locale='es',
                        locale_preferred=True, type='FULLY_SPECIFIED'
                    ),
                    ConceptNameFactory.build(
                        name='Concept Non Unique Preferred Name', locale='es',
                        locale_preferred=True, type='FULLY_SPECIFIED'
                    ),
                ]
            }
        )

        self.assertEqual(
            concept.errors,
            {'names': ['A concept may not have more than one preferred name (per locale): '
                       'Concept Non Unique Preferred Name (locale: es, preferred: yes)']}
        )

    def test_a_preferred_name_can_not_be_a_short_name(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="ShortName", locale_preferred=True, type="Short", locale='fr'),
                    ConceptNameFactory.build(name='Fully Specified Name'),
                ]
            }
        )
        self.assertEqual(
            concept.errors,
            {
                'names': [OPENMRS_SHORT_NAME_CANNOT_BE_PREFERRED + ': ShortName (locale: fr, preferred: yes)']
            }
        )

    def test_a_preferred_name_can_not_be_an_index_search_term(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)
        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="IndexTermName", locale_preferred=True, type=INDEX_TERM),
                    ConceptNameFactory.build(name='Fully Specified Name'),
                ]
            }
        )
        self.assertEqual(
            concept.errors,
            {
                'names': [OPENMRS_SHORT_NAME_CANNOT_BE_PREFERRED + ': IndexTermName (locale: en, preferred: yes)']
            }
        )

    def test_a_name_can_be_equal_to_a_short_name(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="aName", type=SHORT),
                    ConceptNameFactory.build(name='aName'),
                ]
            }
        )

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)

    def test_a_name_should_be_unique(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="aName"),
                    ConceptNameFactory.build(name='aName'),
                ]
            }
        )
        self.assertEqual(
            concept.errors,
            {
                'names': [OPENMRS_NAMES_EXCEPT_SHORT_MUST_BE_UNIQUE]
            }
        )

    def test_only_one_fully_specified_name_per_locale(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="fully specified name1", locale='en'),
                    ConceptNameFactory.build(name='fully specified name2', locale='en'),
                    ConceptNameFactory.build(name='fully specified name3', locale='fr'),
                ]
            }
        )
        self.assertEqual(
            concept.errors,
            {
                'names': [OPENMRS_ONE_FULLY_SPECIFIED_NAME_PER_LOCALE +
                          ': fully specified name2 (locale: en, preferred: no)']
            }
        )

    def test_no_more_than_one_short_name_per_locale(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="fully specified name1", locale='en', type='Short'),
                    ConceptNameFactory.build(name='fully specified name2', locale='en', type='Short'),
                    ConceptNameFactory.build(name='fully specified name3', locale='fr'),
                ]
            }
        )
        self.assertEqual(
            concept.errors,
            {
                'names': [OPENMRS_NO_MORE_THAN_ONE_SHORT_NAME_PER_LOCALE +
                          ': fully specified name2 (locale: en, preferred: no)']
            }
        )

    def test_locale_preferred_name_uniqueness_doesnt_apply_to_shorts(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="mg", locale='en', locale_preferred=True),
                    ConceptNameFactory.build(name='mg', locale='en', type='Short'),
                ]
            }
        )
        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)

    def test_external_id_length(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new({
            'mnemonic': 'concept',
            'version': HEAD,
            'parent': source,
            'external_id': '1' * 37,
            'concept_class': 'Diagnosis',
            'datatype': 'None',
            'names': [
                ConceptNameFactory.build(name="mg", locale='en', locale_preferred=True)
            ]
        })
        self.assertEqual(concept.errors, {'external_id': ['Concept External ID cannot be more than 36 characters.']})
        self.assertIsNone(concept.id)

        concept = Concept.persist_new({
            'mnemonic': 'concept',
            'version': HEAD,
            'parent': source,
            'external_id': '1' * 36,
            'concept_class': 'Diagnosis',
            'datatype': 'None',
            'names': [
                ConceptNameFactory.build(name="mg", locale='en', locale_preferred=True),
            ]
        })
        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)

        concept1 = Concept.persist_new({
            'mnemonic': 'concept1',
            'version': HEAD,
            'parent': source,
            'external_id': '1' * 10,
            'concept_class': 'Diagnosis',
            'datatype': 'None',
            'names': [
                ConceptNameFactory.build(name="mg1", locale='en', locale_preferred=True),
            ]
        })
        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept1.id)

    def test_names_external_id_length(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new({
            'mnemonic': 'concept',
            'version': HEAD,
            'parent': source,
            'external_id': '1' * 36,
            'concept_class': 'Diagnosis',
            'datatype': 'None',
            'names': [
                ConceptNameFactory.build(name="mg", locale='en', locale_preferred=True, external_id='2' * 37),
            ]
        })
        self.assertEqual(
            concept.errors,
            {
                "names": ["Concept name's External ID cannot be more than 36 characters.: "
                          "mg (locale: en, preferred: yes)"],
            }
        )
        self.assertIsNone(concept.id)

    def test_description_external_id_length(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'external_id': '1' * 36,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="mg", locale='en', locale_preferred=True, external_id='2' * 36),
                ],
                'descriptions': [
                    ConceptDescriptionFactory.build(name="mg", locale='en', external_id='2' * 37),
                ]
            }
        )
        self.assertEqual(
            concept.errors,
            {
                "descriptions": ["Concept description's External ID cannot be more than 36 characters.: "
                                 "mg (locale: ""en, preferred: no)"],
            }
        )
        self.assertIsNone(concept.id)


class ValidatorSpecifierTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.create_lookup_concept_classes()

    def test_specifier_should_initialize_openmrs_validator_with_reference_values(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)
        expected_reference_values = {
            'DescriptionTypes': ['None', 'FULLY_SPECIFIED', 'Definition'],
            'Datatypes': ['None', 'N/A', 'Numeric', 'Coded', 'Text'],
            'Classes': ['Diagnosis', 'Drug', 'Test', 'Procedure'],
            'Locales': ['en', 'es', 'fr', 'tr', 'Abkhazian', 'English'],
            'NameTypes': ['FULLY_SPECIFIED', 'Fully Specified', 'Short', 'SHORT', 'INDEX_TERM', 'Index Term', 'None']}

        validator = ValidatorSpecifier().with_validation_schema(
            OPENMRS_VALIDATION_SCHEMA
        ).with_repo(source).with_reference_values().get()

        actual_reference_values = validator.reference_values

        self.assertEqual(sorted(expected_reference_values['Datatypes']), sorted(actual_reference_values['Datatypes']))
        self.assertEqual(sorted(expected_reference_values['Classes']), sorted(actual_reference_values['Classes']))
        self.assertEqual(sorted(expected_reference_values['Locales']), sorted(actual_reference_values['Locales']))
        self.assertEqual(sorted(expected_reference_values['NameTypes']), sorted(actual_reference_values['NameTypes']))
        self.assertEqual(
            sorted(expected_reference_values['DescriptionTypes']), sorted(actual_reference_values['DescriptionTypes'])
        )
