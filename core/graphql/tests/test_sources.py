"""Source MVP aggregates and permissions through the executable Strawberry schema."""

from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser

from core.common.constants import ACCESS_TYPE_NONE, HEAD
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.graphql.schema import schema
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.users.tests.factories import UserProfileFactory


class SourceQueryTests(OCLTestCase):
    """Use real version-scoped ORM queries while isolating the optional ES shortcut."""

    def setUp(self):
        """Create public and private sources with representative versioned contents."""
        super().setUp()
        projection = patch('core.graphql.queries.indexed_source', return_value=None)
        projection.start()
        self.addCleanup(projection.stop)
        self.org = OrganizationFactory(mnemonic='GRAPHQL')
        self.source = OrganizationSourceFactory(
            organization=self.org, mnemonic='Dictionary', name='Dictionary', canonical_url='https://example.org/codes',
        )
        self.concept = ConceptFactory(parent=self.source, concept_class='Diagnosis', datatype='Numeric')
        ConceptFactory(parent=self.source, concept_class='Test', datatype='Text')
        ConceptFactory(parent=self.source, concept_class='Ignored', datatype='Ignored', retired=True)
        self.target = OrganizationSourceFactory(name='External', mnemonic='External')
        self.mapping = MappingFactory(
            parent=self.source, from_concept=self.concept, to_source=self.target, map_type='SAME-AS',
        )
        MappingFactory(parent=self.source, from_concept=self.concept, to_source=self.target, map_type='SAME-AS')
        MappingFactory(parent=self.source, from_concept=self.concept, retired=True, map_type='RETIRED')

    def execute(self, fields, user=None, source=None, version=None, owner=None):  # pylint: disable=too-many-arguments
        """Execute source queries with an explicit principal and fresh request cache."""
        source = source or self.source
        return async_to_sync(schema.execute)(
            'query($org: String, $owner: String, $source: String!, $version: String) {'
            ' source(org: $org, owner: $owner, source: $source, version: $version) {' + fields + '} }',
            variable_values={
                'org': None if owner else source.organization.mnemonic, 'owner': owner,
                'source': source.mnemonic, 'version': version,
            },
            context_value=SimpleNamespace(user=user or AnonymousUser(), auth_status='valid' if user else 'none'),
        )

    def test_full_mvp_counts_unique_labels_and_targets(self):
        """Retired records and duplicate labels/targets do not inflate the MVP output."""
        result = self.execute('name description canonicalUrl uri mapTypes externalSources { name uri } '
                              'classes datatypes summary { activeConcepts mappings }')
        self.assertIsNone(result.errors)
        data = result.data['source']
        self.assertEqual(data['summary'], {'activeConcepts': 2, 'mappings': 2})
        self.assertEqual(data['classes'], ['Diagnosis', 'Test'])
        self.assertEqual(data['datatypes'], ['Numeric', 'Text'])
        self.assertEqual(data['mapTypes'], ['SAME-AS'])
        self.assertEqual(data['externalSources'], [{'name': self.target.name, 'uri': self.target.uri}])
        self.assertEqual(data['canonicalUrl'], self.source.canonical_url)
        self.assertEqual(data['uri'], self.source.uri)

    def test_summary_selection_does_not_query_unrequested_children(self):
        """Requesting only concept count never touches mappings or their targets."""
        with patch('core.sources.models.Source.get_mappings_queryset', side_effect=AssertionError('mappings')):
            result = self.execute('summary { activeConcepts }')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['source']['summary'], {'activeConcepts': 2})

    def test_metadata_fallback_does_not_load_any_children(self):
        """ES failures still leave metadata-only ORM queries small."""
        with patch('core.sources.models.Source.get_mappings_queryset', side_effect=AssertionError('mappings')), \
                patch('core.sources.models.Source.get_concepts_queryset', side_effect=AssertionError('concepts')):
            result = self.execute('name uri')
        self.assertIsNone(result.errors)

    def test_release_summary_uses_membership_not_head_records(self):
        """A release uses its own concept and mapping membership."""
        release = OrganizationSourceFactory(
            organization=self.org, mnemonic=self.source.mnemonic, version='v1', released=True,
        )
        self.concept.sources.add(release)
        self.mapping.sources.add(release)
        result = self.execute('uri classes summary { activeConcepts mappings }', version='v1')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['source']['summary'], {'activeConcepts': 1, 'mappings': 1})
        self.assertEqual(result.data['source']['uri'], release.uri)

    def test_private_source_permissions_cover_anonymous_outsider_member_and_staff(self):
        """Only organization members and staff can read a private organization's source."""
        self.source.public_access = ACCESS_TYPE_NONE
        self.source.save()
        outsider = UserProfileFactory()
        member = UserProfileFactory()
        self.org.members.add(member)
        staff = UserProfileFactory(is_staff=True)
        for user, allowed in ((None, False), (outsider, False), (member, True), (staff, True)):
            with self.subTest(user=getattr(user, 'username', 'anonymous')):
                result = self.execute('name summary { mappings }', user=user)
                if allowed:
                    self.assertIsNone(result.errors)
                else:
                    self.assertEqual(result.errors[0].extensions['code'], 'FORBIDDEN')
                    self.assertIsNone(result.data)

    def test_personal_private_source_owner_has_access(self):
        """Private personal repositories use user ownership rather than organization IDs."""
        owner = UserProfileFactory()
        private = UserSourceFactory(user=owner, public_access=ACCESS_TYPE_NONE)
        for user, allowed in ((owner, True), (UserProfileFactory(), False), (None, False)):
            result = self.execute('name', source=private, user=user, owner=owner.username)
            self.assertEqual(result.errors is None, allowed)

    def test_external_sources_do_not_disclose_private_targets(self):
        """A public mapping cannot expose metadata about a linked private target repository."""
        self.target.public_access = ACCESS_TYPE_NONE
        self.target.save()
        result = self.execute('externalSources { name uri }')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['source']['externalSources'], [])

    def test_missing_source_and_explicit_version_fail(self):
        """Missing explicit versions are not silently replaced with HEAD."""
        result = self.execute('name', version='missing')
        self.assertIsNotNone(result.errors)
        self.assertIn('was not found', result.errors[0].message)

    def test_empty_repository_has_empty_aggregates(self):
        """Empty sources return zero counts and lists, never fabricated labels."""
        empty = OrganizationSourceFactory(organization=self.org, version=HEAD)
        result = self.execute('classes datatypes mapTypes summary { activeConcepts mappings }', source=empty)
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['source'], {
            'classes': [], 'datatypes': [], 'mapTypes': [], 'summary': {'activeConcepts': 0, 'mappings': 0},
        })

    def test_external_targets_resolved_through_concept_also_obey_permissions(self):
        """Target concepts must not bypass repository visibility when to_source is absent."""
        target_concept = ConceptFactory(parent=self.target)
        MappingFactory(parent=self.source, from_concept=self.concept, to_concept=target_concept, to_source=None)
        self.target.public_access = ACCESS_TYPE_NONE
        self.target.save()
        result = self.execute('externalSources { name uri }')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['source']['externalSources'], [])

    def test_typename_only_summary_and_datatype_details(self):
        """Introspection-style selections still instantiate requested nested objects."""
        result = self.execute('summary { __typename }')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['source'], {'summary': {'__typename': 'SourceSummaryType'}})
        self.concept.extras = {'units': 'mg'}
        self.concept.save()
        with patch('core.graphql.queries.indexed_concepts', return_value=None):
            result = async_to_sync(schema.execute)(
                '{ concepts(org: "GRAPHQL", source: "Dictionary", conceptIds: ["' + self.concept.mnemonic + '"]) '
                '{ results { datatype { details { __typename } } metadata { __typename } } } }',
                context_value=SimpleNamespace(user=AnonymousUser(), auth_status='none'),
            )
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['concepts']['results'][0]['datatype']['details'], {
            '__typename': 'NumericDatatypeDetails',
        })

    def test_parent_permission_changes_update_projection_fields(self):
        """Source ACL propagation refreshes the indexed child visibility flag."""
        self.source.public_access = ACCESS_TYPE_NONE
        self.source._should_update_public_access = True  # pylint: disable=protected-access
        with patch.object(type(self.source), 'batch_index') as index:
            self.source.save()
        concept_update = next(call for call in index.call_args_list if call.args[1].Index.name == 'concepts')
        self.assertEqual(concept_update.kwargs['partial_doc'], {'public_can_view': False})

    def test_parent_deactivation_updates_indexed_concept_flag(self):
        """Deactivated parent repositories cannot leave active concept projections behind."""
        self.source.is_active = False
        self.source._should_update_is_active = True  # pylint: disable=protected-access
        with patch.object(type(self.source), 'batch_index') as index:
            self.source.save()
        concept_update = next(call for call in index.call_args_list if call.args[1].Index.name == 'concepts')
        self.assertEqual(concept_update.kwargs['partial_doc'], {'is_active': False})

    def test_repository_and_owner_id_collisions_do_not_grant_private_access(self):
        """Numeric IDs from distinct ownership tables must not confer access to another owner's source."""
        from core.common.permissions import CanViewConceptDictionary, user_can_view_concept_dictionary
        member = UserProfileFactory()
        self.org.members.add(member)
        # Simulate independent sequences colliding, without changing fixture primary keys.
        repository = SimpleNamespace(
            public_access=ACCESS_TYPE_NONE, id=self.org.id, user_id=member.id + 100,
            organization_id=None, parent_id=self.org.id, resource_type='Source',
        )
        self.assertFalse(user_can_view_concept_dictionary(member, repository))
        self.assertFalse(CanViewConceptDictionary().has_object_permission(
            SimpleNamespace(user=member), None, repository,
        ))
