from unittest.mock import Mock

from core.bundles.models import Bundle
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.mappings.tests.factories import MappingFactory
from core.sources.tests.factories import OrganizationSourceFactory


class BundleTest(OCLTestCase):
    def test_clone(self):
        clone_from_source = OrganizationSourceFactory(mnemonic='cloneFrom')
        clone_to_source = OrganizationSourceFactory(mnemonic='cloneTo')
        concept_to_clone = ConceptFactory(parent=clone_from_source)
        cloned_concept = ConceptFactory(parent=clone_to_source, mnemonic=concept_to_clone.mnemonic)
        mapping = MappingFactory(parent=clone_to_source)
        clone_to_source.clone_with_cascade = Mock(return_value=([cloned_concept], [mapping]))
        user = clone_to_source.created_by

        bundle = Bundle.clone(
            concept_to_clone, clone_from_source, clone_to_source, user,
            'https://foo-url', True, cascadeLevels=2
        )

        self.assertEqual(bundle.total, 2)
        self.assertEqual(bundle.concepts, [cloned_concept])
        self.assertEqual(bundle.mappings, [mapping])
        self.assertEqual(len(bundle.entries), 2)
        self.assertEqual(bundle.root, cloned_concept)
        clone_to_source.clone_with_cascade.assert_called_once_with(
            concept_to_clone, user, cascade_levels=2, repo_version=clone_from_source,
            map_types='', exclude_map_types=None, cascade_mappings=True, cascade_hierarchy=True,
            include_retired=False, reverse=False, return_map_types='*', equivalency_map_types=None,
            source_mappings=False, source_to_concepts=True
        )

    def test_clone_skips_unsaved_resources_from_clone_results(self):
        clone_from_source = OrganizationSourceFactory(mnemonic='cloneFrom')
        clone_to_source = OrganizationSourceFactory(mnemonic='cloneTo')
        concept_to_clone = ConceptFactory(parent=clone_from_source)
        unsaved_concept = ConceptFactory.build(parent=clone_to_source)
        cloned_concept = ConceptFactory(parent=clone_to_source, mnemonic='2')
        mapping = MappingFactory(parent=clone_to_source, from_concept=cloned_concept, to_concept=concept_to_clone)
        clone_to_source.clone_with_cascade = Mock(return_value=([unsaved_concept, cloned_concept], [mapping]))

        bundle = Bundle.clone(
            concept_to_clone, clone_from_source, clone_to_source, clone_to_source.created_by,
            'https://foo-url', True
        )

        self.assertEqual(bundle.root, cloned_concept)
        self.assertEqual(bundle.concepts, [cloned_concept])
        self.assertEqual(bundle.mappings, [mapping])
        self.assertEqual(bundle.total, 2)
        self.assertEqual(len(bundle.entries), 2)

    def test_clone_uses_added_concept_as_root_when_mnemonic_changes(self):
        clone_from_source = OrganizationSourceFactory(mnemonic='cloneFrom')
        clone_to_source = OrganizationSourceFactory(mnemonic='cloneTo')
        concept_to_clone = ConceptFactory(parent=clone_from_source, mnemonic='concept-to-clone')
        cloned_concept = ConceptFactory(parent=clone_to_source, mnemonic='1')
        clone_to_source.clone_with_cascade = Mock(return_value=([cloned_concept], []))

        bundle = Bundle.clone(
            concept_to_clone, clone_from_source, clone_to_source, clone_to_source.created_by,
            'https://foo-url', True
        )

        self.assertEqual(bundle.root, cloned_concept)
        self.assertEqual(bundle.total, 1)
        self.assertEqual(len(bundle.entries), 1)
