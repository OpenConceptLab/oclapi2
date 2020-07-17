from django.core.exceptions import ValidationError
from django.db.models import QuerySet

from core.collections.models import CollectionReference, Collection
from core.collections.tests.factories import CollectionFactory
from core.collections.utils import is_mapping, is_concept, drop_version, is_version_specified, \
    get_concept_by_expression
from core.common.tests import OCLTestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.sources.tests.factories import SourceFactory


class CollectionTest(OCLTestCase):
    def test_is_versioned(self):
        self.assertTrue(Collection().is_versioned)

    def test_add_references(self):
        collection = CollectionFactory()

        self.assertEqual(collection.concepts.count(), 0)
        self.assertEqual(collection.references.count(), 0)

        source = SourceFactory()
        concept = ConceptFactory(parent=source, sources=[source])
        concept_expression = concept.uri

        collection.add_references([concept_expression])

        self.assertEqual(collection.concepts.count(), 1)
        self.assertEqual(collection.references.count(), 1)
        self.assertEqual(collection.references.first().expression, concept_expression)
        self.assertEqual(collection.concepts.first(), concept)

    def test_seed_concepts(self):
        collection1 = CollectionFactory()
        collection2 = CollectionFactory(
            version='v1', mnemonic=collection1.mnemonic, organization=collection1.organization
        )

        self.assertTrue(collection1.is_head)
        self.assertFalse(collection2.is_head)

        source = SourceFactory()
        concept = ConceptFactory(parent=source, sources=[source])
        concept_expression = concept.uri

        collection1.add_references([concept_expression])

        self.assertEqual(collection1.concepts.count(), 1)
        self.assertEqual(collection2.concepts.count(), 0)

        collection2.seed_concepts()

        self.assertEqual(collection1.concepts.count(), 1)
        self.assertEqual(collection2.concepts.count(), 1)

    def test_seed_references(self):
        collection1 = CollectionFactory()
        collection2 = CollectionFactory(
            version='v1', mnemonic=collection1.mnemonic, organization=collection1.organization
        )

        self.assertTrue(collection1.is_head)
        self.assertFalse(collection2.is_head)

        source = SourceFactory()
        concept = ConceptFactory(parent=source, sources=[source])
        concept_expression = concept.uri

        collection1.add_references([concept_expression])

        self.assertEqual(collection1.references.count(), 1)
        self.assertEqual(collection2.references.count(), 0)

        collection2.seed_references()

        self.assertEqual(collection1.references.count(), 1)
        self.assertEqual(collection2.references.count(), 1)
        self.assertEqual(collection1.references.first().expression, collection2.references.first().expression)
        self.assertNotEqual(collection1.references.first().id, collection2.references.first().id)


class CollectionReferenceTest(OCLTestCase):
    def test_invalid_expression(self):
        reference = CollectionReference(expression='')

        with self.assertRaises(ValidationError) as ex:
            reference.full_clean()

        self.assertEqual(
            ex.exception.message_dict,
            {
                'expression': ['This field cannot be blank.'],
                'collection': ['This field cannot be null.'],
                'detail': ['Expression specified is not valid.']
            }
        )

    def test_reference_type(self):
        reference = CollectionReference(expression='')
        self.assertFalse(reference.reference_type)

        reference = CollectionReference(expression=None)
        self.assertFalse(reference.reference_type)

        reference = CollectionReference(
            expression='/parent/parent-mnemonic/sources/source-mnemonic/concepts/concept-mnemonic/'
        )
        self.assertEqual(reference.reference_type, 'concepts')

    def test_reference_as_concept_version(self):
        collection = CollectionFactory()
        concept = ConceptFactory()
        expression = concept.uri

        reference = CollectionReference(expression=expression, collection=collection)
        reference.full_clean()

        self.assertEqual(len(reference.concepts), 1)
        self.assertTrue(isinstance(reference.concepts[0], Concept))
        self.assertEqual(reference.concepts[0].id, concept.id)

    def test_get_concepts(self):
        reference = CollectionReference()
        reference.expression = '/unknown/uri/'

        unknown_expression_concepts = reference.get_concepts()

        self.assertTrue(isinstance(unknown_expression_concepts, QuerySet))
        self.assertFalse(unknown_expression_concepts.exists())

        concept1 = ConceptFactory()
        reference.expression = concept1.uri

        concepts = reference.get_concepts()

        self.assertTrue(isinstance(concepts, QuerySet))
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first(), concept1)

        concept2 = ConceptFactory(parent=concept1.parent, version='v1', mnemonic=concept1.mnemonic)
        reference.expression = concept1.uri + 'versions/'

        concepts = reference.get_concepts().order_by('created_at')

        self.assertTrue(isinstance(concepts, QuerySet))
        self.assertEqual(concepts.count(), 2)
        self.assertEqual(concepts.first(), concept1)
        self.assertEqual(concepts.last(), concept2)


class CollectionUtilsTest(OCLTestCase):
    def test_is_mapping(self):
        self.assertFalse(is_mapping(None))
        self.assertFalse(is_mapping(''))
        self.assertFalse(is_mapping('orgs/org-1/sources/source-1/mapping/'))

        self.assertTrue(is_mapping('orgs/org-1/sources/source-1/mappings/'))
        self.assertTrue(is_mapping('users/user-1/sources/source-1/mappings/'))
        self.assertTrue(is_mapping('users/user-1/collections/coll-1/mappings/'))
        self.assertTrue(is_mapping('/mappings/'))

    def test_is_concept(self):
        self.assertFalse(is_concept(None))
        self.assertFalse(is_concept(''))
        self.assertFalse(is_concept('orgs/org-1/sources/source-1/concept/'))

        self.assertTrue(is_concept('orgs/org-1/sources/source-1/concepts/'))
        self.assertTrue(is_concept('users/user-1/sources/source-1/concepts/'))
        self.assertTrue(is_concept('users/user-1/collections/coll-1/concepts/'))
        self.assertTrue(is_concept('/concepts/'))

    def test_drop_version(self):
        concept_head = ConceptFactory()
        concept_v1 = ConceptFactory(parent=concept_head.parent, version='v1', mnemonic=concept_head.mnemonic)

        self.assertTrue('/v1/' in concept_v1.uri)
        self.assertTrue('/v1/' not in drop_version(concept_v1.uri))

    def test_is_version_specified(self):
        concept_head = ConceptFactory()
        concept_v1 = ConceptFactory(parent=concept_head.parent, version='v1', mnemonic=concept_head.mnemonic)

        self.assertTrue(is_version_specified(concept_v1.uri))
        self.assertFalse(is_version_specified(concept_head.uri))

    def test_get_concept_by_expression(self):
        concept_head = ConceptFactory()
        concept_v1 = ConceptFactory(parent=concept_head.parent, version='v1', mnemonic=concept_head.mnemonic)

        self.assertEqual(get_concept_by_expression(concept_head.uri), concept_head)
        self.assertEqual(get_concept_by_expression(concept_v1.uri), concept_v1)
        self.assertIsNone(get_concept_by_expression('/foobar/'))
