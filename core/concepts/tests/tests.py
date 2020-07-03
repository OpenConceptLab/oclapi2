import factory
from mock import patch
from pydash import omit

from core.common.constants import CUSTOM_VALIDATION_SCHEMA_OPENMRS, HEAD
from core.common.tests import OCLTestCase
from core.concepts.constants import (
    OPENMRS_MUST_HAVE_EXACTLY_ONE_PREFERRED_NAME,
    OPENMRS_FULLY_SPECIFIED_NAME_UNIQUE_PER_SOURCE_LOCALE, OPENMRS_AT_LEAST_ONE_FULLY_SPECIFIED_NAME,
    OPENMRS_PREFERRED_NAME_UNIQUE_PER_SOURCE_LOCALE, OPENMRS_SHORT_NAME_CANNOT_BE_PREFERRED,
    SHORT, INDEX_TERM, OPENMRS_NAMES_EXCEPT_SHORT_MUST_BE_UNIQUE, OPENMRS_ONE_FULLY_SPECIFIED_NAME_PER_LOCALE,
    OPENMRS_NO_MORE_THAN_ONE_SHORT_NAME_PER_LOCALE, CONCEPT_IS_ALREADY_RETIRED, CONCEPT_IS_ALREADY_NOT_RETIRED,
    OPENMRS_CONCEPT_CLASS, OPENMRS_DATATYPE, OPENMRS_DESCRIPTION_TYPE, OPENMRS_NAME_LOCALE, OPENMRS_DESCRIPTION_LOCALE)
from core.concepts.models import Concept
from core.concepts.tests.factories import LocalizedTextFactory, ConceptFactory
from core.concepts.validators import ValidatorSpecifier
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

    def test_persist_new(self):
        source = SourceFactory(version=HEAD)
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [LocalizedTextFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.version, HEAD)
        self.assertEqual(source.concepts_set.count(), 1)
        self.assertEqual(source.concepts.count(), 1)

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

    def test_retire(self):
        source = SourceFactory(version=HEAD)
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [LocalizedTextFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.versions.count(), 1)
        self.assertFalse(concept.retired)
        self.assertTrue(concept.is_head)

        concept.retire(concept.created_by, 'Forceful retirement')  # concept will become old/prev version
        concept.refresh_from_db()

        self.assertFalse(concept.is_head)
        self.assertEqual(concept.versions.count(), 2)
        self.assertFalse(concept.retired)
        latest_version = concept.get_latest_version()
        self.assertTrue(latest_version.retired)
        self.assertEqual(latest_version.comment, 'Forceful retirement')

        self.assertEqual(
            concept.retire(concept.created_by),
            {'__all__': CONCEPT_IS_ALREADY_RETIRED}
        )

    def test_unretire(self):
        source = SourceFactory(version=HEAD)
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source, 'retired': True,
            'names': [LocalizedTextFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.versions.count(), 1)
        self.assertTrue(concept.retired)
        self.assertTrue(concept.is_head)

        concept.unretire(concept.created_by, 'World needs you!')  # concept will become old/prev version
        concept.refresh_from_db()

        self.assertFalse(concept.is_head)
        self.assertEqual(concept.versions.count(), 2)
        self.assertTrue(concept.retired)
        latest_version = concept.get_latest_version()
        self.assertFalse(latest_version.retired)
        self.assertEqual(latest_version.comment, 'World needs you!')

        self.assertEqual(
            concept.unretire(concept.created_by),
            {'__all__': CONCEPT_IS_ALREADY_NOT_RETIRED}
        )


class OpenMRSConceptValidatorTest(OCLTestCase):
    def setUp(self):
        self.create_lookup_concept_classes()

    def test_concept_class_is_valid_attribute_negative(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        concept = Concept.persist_new(
            dict(
                mnemonic='concept1', version=HEAD, name='concept1', parent=source,
                concept_class='XYZQWERT', datatype='None',
                names=[LocalizedTextFactory.build(name='Grip', locale='es', locale_preferred=True)]
            )
        )

        self.assertEqual(
            concept.errors,
            dict(concept_class=[OPENMRS_CONCEPT_CLASS])
        )

    def test_data_type_is_valid_attribute_negative(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        concept = Concept.persist_new(
            dict(
                mnemonic='concept1', version=HEAD, name='concept1', parent=source,
                concept_class='Diagnosis', datatype='XYZWERRTR',
                names=[LocalizedTextFactory.build(name='Grip', locale='es', locale_preferred=True)]
            )
        )
        self.assertEqual(
            concept.errors,
            dict(data_type=[OPENMRS_DATATYPE])
        )

    def test_description_type_is_valid_attribute_negative(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        concept = Concept.persist_new(
            dict(
                mnemonic='concept1', version=HEAD, name='concept1', parent=source,
                concept_class='Diagnosis', datatype='None',
                names=[LocalizedTextFactory.build(locale_preferred=True)],
                descriptions=[LocalizedTextFactory.build(type='XYZWERRTR')]
            )
        )

        self.assertEqual(
            concept.errors,
            dict(descriptions=[OPENMRS_DESCRIPTION_TYPE])
        )

    def test_name_locale_is_valid_attribute_negative(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        concept = Concept.persist_new(
            dict(
                mnemonic='concept1', version=HEAD, name='concept1', parent=source,
                concept_class='Diagnosis', datatype='None',
                names=[LocalizedTextFactory.build(locale_preferred=True, locale='FOOBAR')],
                descriptions=[LocalizedTextFactory.build(locale_preferred=True)]
            )
        )

        self.assertEqual(
            concept.errors,
            dict(names=[OPENMRS_NAME_LOCALE])
        )

    def test_description_locale_is_valid_attribute_negative(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        concept = Concept.persist_new(
            dict(
                mnemonic='concept1', version=HEAD, name='concept1', parent=source,
                concept_class='Diagnosis', datatype='None',
                names=[LocalizedTextFactory.build(locale_preferred=True)],
                descriptions=[LocalizedTextFactory.build(locale_preferred=True, locale='FOOBAR')]
            )
        )
        self.assertEqual(
            concept.errors,
            dict(descriptions=[OPENMRS_DESCRIPTION_LOCALE])
        )

    def test_concept_should_have_exactly_one_preferred_name_per_locale(self):
        name_en1 = LocalizedTextFactory.build(name='PreferredName1', locale_preferred=True)
        name_en2 = LocalizedTextFactory.build(name='PreferredName2', locale_preferred=True)
        name_tr = LocalizedTextFactory.build(name='PreferredName3', locale="tr", locale_preferred=True)
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)

        concept = Concept.persist_new(
            dict(
                mnemonic='concept', version=HEAD, name='concept', parent=source,
                concept_class='Diagnosis', datatype='None', names=[name_en1, name_en2, name_tr]
            )
        )

        self.assertEqual(
            concept.errors,
            dict(names=[OPENMRS_MUST_HAVE_EXACTLY_ONE_PREFERRED_NAME + ': PreferredName2 (locale: en, preferred: yes)'])
        )

    def test_concepts_should_have_unique_fully_specified_name_per_locale(self):
        name_fully_specified1 = LocalizedTextFactory.build(name='FullySpecifiedName1')

        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS, version=HEAD)
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
            dict(names=[OPENMRS_FULLY_SPECIFIED_NAME_UNIQUE_PER_SOURCE_LOCALE +
                        ': FullySpecifiedName1 (locale: en, preferred: no)'])
        )

    def test_at_least_one_fully_specified_name_per_concept_negative(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS, version=HEAD)

        concept = Concept.persist_new(
            dict(
                mnemonic='concept', version=HEAD, name='concept', parent=source,
                concept_class='Diagnosis', datatype='None', names=[
                    LocalizedTextFactory.build(name='Fully Specified Name 1', locale='tr', type='Short'),
                    LocalizedTextFactory.build(name='Fully Specified Name 2', locale='en', type='Short')
                ]
            )
        )
        self.assertEqual(
            concept.errors,
            dict(names=[OPENMRS_AT_LEAST_ONE_FULLY_SPECIFIED_NAME])
        )

    def test_duplicate_preferred_name_per_source_should_fail(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS, version=HEAD)
        concept1 = Concept.persist_new(
            dict(
                mnemonic='concept1', version=HEAD, name='concept1', parent=source,
                concept_class='Diagnosis', datatype='None', names=[
                    LocalizedTextFactory.build(
                        name='Concept Non Unique Preferred Name', locale='en',
                        locale_preferred=True, type='Fully Specified'
                    ),
                ]
            )
        )
        concept2 = Concept.persist_new(
            dict(
                mnemonic='concept2', version=HEAD, name='concept2', parent=source,
                concept_class='Diagnosis', datatype='None', names=[
                    LocalizedTextFactory.build(
                        name='Concept Non Unique Preferred Name', locale='en', locale_preferred=True, type='None'
                    ),
                    LocalizedTextFactory.build(
                        name='any name', locale='en', locale_preferred=False, type='Fully Specified'
                    ),
                ]
            )
        )

        self.assertEqual(concept1.errors, {})
        self.assertEqual(
            concept2.errors,
            dict(names=[OPENMRS_PREFERRED_NAME_UNIQUE_PER_SOURCE_LOCALE +
                        ': Concept Non Unique Preferred Name (locale: en, preferred: yes)'])
        )

    def test_unique_preferred_name_per_locale_within_concept_negative(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS, version=HEAD)

        concept = Concept.persist_new(
            dict(
                mnemonic='concept1', version=HEAD, name='concept1', parent=source,
                concept_class='Diagnosis', datatype='None', names=[
                    LocalizedTextFactory.build(
                        name='Concept Non Unique Preferred Name', locale='es',
                        locale_preferred=True, type='FULLY_SPECIFIED'
                    ),
                    LocalizedTextFactory.build(
                        name='Concept Non Unique Preferred Name', locale='es',
                        locale_preferred=True, type='FULLY_SPECIFIED'
                    ),
                ]
            )
        )

        self.assertEqual(
            concept.errors,
            {'names': ['A concept may not have more than one preferred name (per locale): '
                       'Concept Non Unique Preferred Name (locale: es, preferred: yes)']}
        )

    def test_a_preferred_name_can_not_be_a_short_name(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS, version=HEAD)

        concept = Concept.persist_new(
            dict(
                mnemonic='concept', version=HEAD, name='concept', parent=source,
                concept_class='Diagnosis', datatype='None', names=[
                    LocalizedTextFactory.build(name="ShortName", locale_preferred=True, type="Short", locale='fr'),
                    LocalizedTextFactory.build(name='Fully Specified Name'),
                ]
            )
        )
        self.assertEqual(
            concept.errors,
            dict(names=[OPENMRS_SHORT_NAME_CANNOT_BE_PREFERRED + ': ShortName (locale: fr, preferred: yes)'])
        )

    def test_a_preferred_name_can_not_be_an_index_search_term(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS, version=HEAD)
        concept = Concept.persist_new(
            dict(
                mnemonic='concept', version=HEAD, name='concept', parent=source,
                concept_class='Diagnosis', datatype='None', names=[
                    LocalizedTextFactory.build(name="IndexTermName", locale_preferred=True, type=INDEX_TERM),
                    LocalizedTextFactory.build(name='Fully Specified Name'),
                ]
            )
        )
        self.assertEqual(
            concept.errors,
            dict(names=[OPENMRS_SHORT_NAME_CANNOT_BE_PREFERRED + ': IndexTermName (locale: en, preferred: yes)'])
        )

    def test_a_name_can_be_equal_to_a_short_name(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS, version=HEAD)

        concept = Concept.persist_new(
            dict(
                mnemonic='concept', version=HEAD, name='concept', parent=source,
                concept_class='Diagnosis', datatype='None', names=[
                    LocalizedTextFactory.build(name="aName", type=SHORT),
                    LocalizedTextFactory.build(name='aName'),
                ]
            )
        )

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)

    def test_a_name_should_be_unique(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS, version=HEAD)

        concept = Concept.persist_new(
            dict(
                mnemonic='concept', version=HEAD, name='concept', parent=source,
                concept_class='Diagnosis', datatype='None', names=[
                    LocalizedTextFactory.build(name="aName"),
                    LocalizedTextFactory.build(name='aName'),
                ]
            )
        )
        self.assertEqual(
            concept.errors,
            dict(names=[OPENMRS_NAMES_EXCEPT_SHORT_MUST_BE_UNIQUE])
        )

    def test_only_one_fully_specified_name_per_locale(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS, version=HEAD)

        concept = Concept.persist_new(
            dict(
                mnemonic='concept', version=HEAD, name='concept', parent=source,
                concept_class='Diagnosis', datatype='None', names=[
                    LocalizedTextFactory.build(name="fully specified name1", locale='en'),
                    LocalizedTextFactory.build(name='fully specified name2', locale='en'),
                    LocalizedTextFactory.build(name='fully specified name3', locale='fr'),
                ]
            )
        )
        self.assertEqual(
            concept.errors,
            dict(names=[OPENMRS_ONE_FULLY_SPECIFIED_NAME_PER_LOCALE +
                        ': fully specified name2 (locale: en, preferred: no)'])
        )

    def test_no_more_than_one_short_name_per_locale(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS, version=HEAD)

        concept = Concept.persist_new(
            dict(
                mnemonic='concept', version=HEAD, name='concept', parent=source,
                concept_class='Diagnosis', datatype='None', names=[
                    LocalizedTextFactory.build(name="fully specified name1", locale='en', type='Short'),
                    LocalizedTextFactory.build(name='fully specified name2', locale='en', type='Short'),
                    LocalizedTextFactory.build(name='fully specified name3', locale='fr'),
                ]
            )
        )
        self.assertEqual(
            concept.errors,
            dict(names=[OPENMRS_NO_MORE_THAN_ONE_SHORT_NAME_PER_LOCALE +
                        ': fully specified name2 (locale: en, preferred: no)'])
        )

    def test_locale_preferred_name_uniqueness_doesnt_apply_to_shorts(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS, version=HEAD)

        concept = Concept.persist_new(
            dict(
                mnemonic='concept', version=HEAD, name='concept', parent=source,
                concept_class='Diagnosis', datatype='None', names=[
                    LocalizedTextFactory.build(name="mg", locale='en', locale_preferred=True),
                    LocalizedTextFactory.build(name='mg', locale='en', type='Short'),
                ]
            )
        )
        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)


class ValidatorSpecifierTest(OCLTestCase):
    def test_specifier_should_initialize_openmrs_validator_with_reference_values(self):
        source = SourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS, version=HEAD)
        expected_reference_values = {
            'DescriptionTypes': ['None', 'FULLY_SPECIFIED', 'Definition'],
            'Datatypes': ['None', 'N/A', 'Numeric', 'Coded', 'Text'],
            'Classes': ['Diagnosis', 'Drug', 'Test', 'Procedure'],
            'Locales': ['en', 'es', 'fr', 'tr', 'Abkhazian', 'English'],
            'NameTypes': ['FULLY_SPECIFIED', 'Fully Specified', 'Short', 'SHORT', 'INDEX_TERM', 'Index Term', 'None']}

        validator = ValidatorSpecifier().with_validation_schema(
            CUSTOM_VALIDATION_SCHEMA_OPENMRS
        ).with_repo(source).with_reference_values().get()

        actual_reference_values = validator.reference_values

        self.assertEqual(sorted(expected_reference_values['Datatypes']), sorted(actual_reference_values['Datatypes']))
        self.assertEqual(sorted(expected_reference_values['Classes']), sorted(actual_reference_values['Classes']))
        self.assertEqual(sorted(expected_reference_values['Locales']), sorted(actual_reference_values['Locales']))
        self.assertEqual(sorted(expected_reference_values['NameTypes']), sorted(actual_reference_values['NameTypes']))
        self.assertEqual(
            sorted(expected_reference_values['DescriptionTypes']), sorted(actual_reference_values['DescriptionTypes'])
        )
