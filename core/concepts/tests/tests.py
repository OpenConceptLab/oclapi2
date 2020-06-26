from mock import patch
from pydash import omit

from core.common.tests import OCLTestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import LocalizedTextFactory, ConceptFactory
from core.sources.tests.factories import SourceFactory


class LocalizedTextTest(OCLTestCase):
    def test_clone(self):
        saved_locale = LocalizedTextFactory()
        cloned_locale = saved_locale.clone()
        self.assertEqual(
            omit(saved_locale.__dict__, ['_state', 'id', 'created_at']),
            omit(cloned_locale.clone().__dict__, ['_state', 'id', 'created_at'])
        )
        self.assertIsNone(cloned_locale.id)


class ConceptTest(OCLTestCase):
    def test_display_name(self):
        concept = ConceptFactory(names=())
        self.assertIsNone(concept.display_name)

        preferred_locale = LocalizedTextFactory(locale_preferred=True)
        concept.names.add(preferred_locale)

        self.assertEqual(concept.display_name, preferred_locale.name)

    def test_display_locale(self):
        preferred_locale = LocalizedTextFactory(locale_preferred=True)
        concept = ConceptFactory(names=(preferred_locale,))

        self.assertEqual(concept.display_locale, preferred_locale.locale)

    def test_default_name_locales(self):
        es_locale = LocalizedTextFactory(locale='es')
        en_locale = LocalizedTextFactory(locale='en')
        concept = ConceptFactory(names=(es_locale, en_locale))

        default_name_locales = concept.default_name_locales

        self.assertEqual(default_name_locales.count(), 1)
        self.assertEqual(default_name_locales.first(), en_locale)

    def test_default_description_locales(self):
        es_locale = LocalizedTextFactory(locale='es')
        en_locale = LocalizedTextFactory(locale='en')
        concept = ConceptFactory(descriptions=(es_locale, en_locale))

        default_description_locales = concept.default_description_locales

        self.assertEqual(default_description_locales.count(), 1)
        self.assertEqual(default_description_locales.first(), en_locale)

    def test_names_for_default_locale(self):
        es_locale = LocalizedTextFactory(locale='es', name='Not English')
        en_locale = LocalizedTextFactory(locale='en', name='English')
        concept = ConceptFactory(names=(es_locale, en_locale))

        self.assertEqual(concept.names_for_default_locale, [en_locale.name])

    def test_descriptions_for_default_locale(self):
        es_locale = LocalizedTextFactory(locale='es', name='Not English')
        en_locale = LocalizedTextFactory(locale='en', name='English')
        concept = ConceptFactory(descriptions=(es_locale, en_locale))

        self.assertEqual(concept.descriptions_for_default_locale, [en_locale.name])

    @patch('core.concepts.models.LocalizedText.clone')
    def test_clone(self, locale_clone_mock):
        es_locale = LocalizedTextFactory(locale='es', name='Not English')
        en_locale = LocalizedTextFactory(locale='en', name='English')

        concept = ConceptFactory(descriptions=(es_locale, en_locale), names=(en_locale,), released=True)
        cloned_concept = concept.clone()

        self.assertEqual(cloned_concept.version, '--TEMP--')
        self.assertEqual(cloned_concept.mnemonic, concept.mnemonic)
        self.assertEqual(cloned_concept.parent, concept.parent)
        self.assertEqual(len(cloned_concept.cloned_names), concept.names.count())
        self.assertEqual(len(cloned_concept.cloned_descriptions), concept.descriptions.count())
        self.assertEqual(locale_clone_mock.call_count, 3)
        self.assertTrue(cloned_concept.released)

    def test_version_for_concept(self):
        concept = ConceptFactory(released=True)
        source = SourceFactory()

        concept_version = Concept.version_for_concept(concept, 'v1.0', source)

        self.assertEqual(concept_version.parent, source)
        self.assertEqual(concept_version.version, 'v1.0')
        self.assertEqual(concept_version.created_by_id, concept.created_by_id)
        self.assertEqual(concept_version.updated_by_id, concept.updated_by_id)
        self.assertEqual(concept_version.mnemonic, concept.mnemonic)
        self.assertFalse(concept_version.released)

    def test_persist_clone(self):
        es_locale = LocalizedTextFactory(locale='es', name='Not English')
        en_locale = LocalizedTextFactory(locale='en', name='English')

        source_version0 = SourceFactory(version='v0')
        source_head = SourceFactory(
            version='HEAD', mnemonic=source_version0.mnemonic, organization=source_version0.organization
        )

        self.assertEqual(source_head.versions.count(), 2)

        concept = ConceptFactory(
            descriptions=(es_locale, en_locale),
            names=(en_locale,),
            sources=(source_version0,),
            parent=source_version0
        )
        cloned_concept = Concept.version_for_concept(concept, 'v1', source_version0)

        self.assertEqual(
            Concept.persist_clone(cloned_concept),
            dict(version_created_by='Must specify which user is attempting to create a new concept version.')
        )

        self.assertEqual(Concept.persist_clone(cloned_concept, concept.created_by), {})

        persisted_concept = Concept.objects.last()
        self.assertEqual(persisted_concept.names.count(), 1)
        self.assertEqual(persisted_concept.descriptions.count(), 2)
        self.assertEqual(persisted_concept.parent, source_version0)
        self.assertEqual(persisted_concept.sources.count(), 2)
        self.assertEqual(source_head.concepts.first().id, persisted_concept.id)
