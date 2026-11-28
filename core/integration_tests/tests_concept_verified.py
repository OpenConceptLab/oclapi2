from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.integration_tests.tests_repo_verification import VALID
from core.sources.tests.factories import OrganizationSourceFactory


class ConceptIsVerifiedTest(OCLTestCase):
    """A concept is verified while it's in its source's latest release and that release has a record (ocl_issues#3027)"""

    def setUp(self):
        super().setUp()
        self.head = OrganizationSourceFactory()
        self.concept = ConceptFactory(parent=self.head)
        self.concept.sources.add(self.head)

    def release(self, version, verification=None, concepts=()):
        release = OrganizationSourceFactory(
            mnemonic=self.head.mnemonic, organization=self.head.organization, version=version, released=True,
            verification=verification)
        for concept in concepts:
            concept.sources.add(release)
        return release

    def fresh(self):
        return type(self.concept).objects.get(id=self.concept.id)

    def test_in_a_verified_latest_release(self):
        self.release('v1', VALID, [self.concept])
        self.assertTrue(self.fresh().is_verified)

    def test_in_a_latest_release_with_no_record(self):
        self.release('v1', None, [self.concept])
        self.assertFalse(self.fresh().is_verified)

    def test_not_in_the_verified_release(self):
        self.release('v1', VALID)
        self.assertFalse(self.fresh().is_verified)

    def test_a_newer_release_without_a_record(self):
        self.release('v1', VALID, [self.concept])
        self.release('v2', None, [self.concept])
        self.assertFalse(self.fresh().is_verified)

    def test_on_a_list_page_a_version_with_no_record_needs_no_query(self):
        release = self.release('v1', None, [self.concept])
        concept = self.fresh()
        concept._latest_source_version = release  # pylint: disable=protected-access
        with self.assertNumQueries(0):
            self.assertFalse(concept.is_verified_in_list)
