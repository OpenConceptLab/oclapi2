from django.db import transaction, IntegrityError
from django.core.exceptions import ValidationError

from core.common.constants import HEAD
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.sources.models import Source
from core.sources.tests.factories import SourceFactory
from core.users.tests.factories import UserProfileFactory


class SourceTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.new_source = SourceFactory.build(organization=None)
        self.user = UserProfileFactory()

    def test_is_versioned(self):
        self.assertTrue(Source().is_versioned)

    def test_persist_new_positive(self):
        kwargs = {
            'parent_resource': self.user
        }
        errors = Source.persist_new(self.new_source, self.user, **kwargs)

        source = Source.objects.get(name=self.new_source.name)
        self.assertEqual(len(errors), 0)
        self.assertTrue(Source.objects.filter(name=self.new_source.name).exists())
        self.assertEqual(source.num_versions, 1)
        self.assertEqual(source.get_latest_version(), source)
        self.assertEqual(source.version, 'HEAD')
        self.assertFalse(source.released)
        self.assertEqual(
            source.uri,
            '/users/{username}/sources/{source}/'.format(username=self.user.username, source=source.mnemonic)
        )

    def test_persist_new_negative__no_parent(self):
        errors = Source.persist_new(self.new_source, self.user)

        self.assertEqual(errors, {'parent': 'Parent resource cannot be None.'})
        self.assertFalse(Source.objects.filter(name=self.new_source.name).exists())

    def test_persist_new_negative__no_owner(self):
        kwargs = {
            'parent_resource': self.user
        }

        errors = Source.persist_new(self.new_source, None, **kwargs)

        self.assertEqual(errors, {'created_by': 'Creator cannot be None.'})
        self.assertFalse(Source.objects.filter(name=self.new_source.name).exists())

    def test_persist_new_negative__no_name(self):
        kwargs = {
            'parent_resource': self.user
        }
        self.new_source.name = None

        errors = Source.persist_new(self.new_source, self.user, **kwargs)

        self.assertEqual(errors, {'name': ['This field cannot be null.']})
        self.assertFalse(Source.objects.filter(name=self.new_source.name).exists())

    def test_persist_changes_positive(self):
        kwargs = {
            'parent_resource': self.user
        }
        errors = Source.persist_new(self.new_source, self.user, **kwargs)
        self.assertEqual(len(errors), 0)
        saved_source = Source.objects.get(name=self.new_source.name)

        name = saved_source.name

        self.new_source.name = "%s_prime" % name
        self.new_source.source_type = 'Reference'

        errors = Source.persist_changes(self.new_source, self.user, **kwargs)
        updated_source = Source.objects.get(mnemonic=self.new_source.mnemonic)

        self.assertEqual(len(errors), 0)
        self.assertEqual(updated_source.num_versions, 1)
        self.assertEqual(updated_source.head, updated_source)
        self.assertEqual(updated_source.name, self.new_source.name)
        self.assertEqual(updated_source.source_type, 'Reference')
        self.assertEqual(
            updated_source.uri,
            '/users/{username}/sources/{source}/'.format(username=self.user.username, source=updated_source.mnemonic)
        )

    def test_persist_changes_negative__repeated_mnemonic(self):
        kwargs = {
            'parent_resource': self.user
        }
        source1 = SourceFactory(organization=None, user=self.user, mnemonic='source-1', version=HEAD)
        source2 = SourceFactory(organization=None, user=self.user, mnemonic='source-2', version=HEAD)

        source2.mnemonic = source1.mnemonic

        with transaction.atomic():
            errors = Source.persist_changes(source2, self.user, **kwargs)
        self.assertEqual(len(errors), 1)
        self.assertTrue('__all__' in errors)

    def test_source_version_create_positive(self):
        source = SourceFactory()
        self.assertEqual(source.num_versions, 1)

        source_version = Source(
            name='version1',
            mnemonic=source.mnemonic,
            version='version1',
            released=True,
            created_by=source.created_by,
            updated_by=source.updated_by,
            organization=source.organization
        )
        source_version.full_clean()
        source_version.save()

        self.assertEqual(source.num_versions, 2)
        self.assertEqual(source.organization.mnemonic, source_version.parent_resource)
        self.assertEqual(source.organization.resource_type, source_version.parent_resource_type)
        self.assertEqual(source_version, source.get_latest_version())
        self.assertEqual(
            source_version.uri,
            '/orgs/{org}/sources/{source}/{version}/'.format(
                org=source_version.organization.mnemonic,
                source=source_version.mnemonic, version=source_version.version
            )
        )

    def test_source_version_create_negative__same_version(self):
        source = SourceFactory()
        self.assertEqual(source.num_versions, 1)
        SourceFactory(
            name='version1', mnemonic=source.mnemonic, version='version1', organization=source.organization
        )
        self.assertEqual(source.num_versions, 2)

        with transaction.atomic():
            source_version = Source(
                name='version1',
                version='version1',
                mnemonic=source.mnemonic,
                organization=source.organization
            )
            with self.assertRaises(IntegrityError):
                source_version.full_clean()
                source_version.save()
            self.assertIsNone(source_version.id)

        self.assertEqual(source.num_versions, 2)

    def test_source_version_create_positive__same_version(self):
        source = SourceFactory()
        self.assertEqual(source.num_versions, 1)
        SourceFactory(
            name='version1', mnemonic=source.mnemonic, version='version1', organization=source.organization
        )
        source2 = SourceFactory()
        self.assertEqual(source2.num_versions, 1)
        SourceFactory(
            name='version1', mnemonic=source2.mnemonic, version='version1', organization=source2.organization
        )
        self.assertEqual(source2.num_versions, 2)

    def test_persist_new_version(self):
        source = SourceFactory(version=HEAD)
        concept = ConceptFactory(mnemonic='concept1', parent=source)

        self.assertEqual(source.concepts_set.count(), 1)  # parent-child
        self.assertEqual(source.concepts.count(), 1)
        self.assertEqual(concept.sources.count(), 1)
        self.assertTrue(source.is_latest_version)

        version1 = SourceFactory.build(
            name='version1', version='v1', mnemonic=source.mnemonic, organization=source.organization
        )
        Source.persist_new_version(version1, source.created_by)
        source.refresh_from_db()

        self.assertFalse(source.is_latest_version)
        self.assertEqual(source.concepts_set.count(), 1)  # parent-child
        self.assertEqual(source.concepts.count(), 1)
        self.assertTrue(version1.is_latest_version)
        self.assertEqual(version1.concepts.count(), 1)
        self.assertEqual(version1.concepts_set.count(), 0)  # no direct child

    def test_source_version_delete(self):
        source = SourceFactory(version=HEAD)
        concept = ConceptFactory(mnemonic='concept1', version=HEAD, sources=[source], parent=source)

        self.assertTrue(source.is_latest_version)
        self.assertEqual(concept.sources.count(), 1)

        version1 = SourceFactory.build(
            name='version1', version='v1', mnemonic=source.mnemonic, organization=source.organization
        )
        Source.persist_new_version(version1, source.created_by)
        source.refresh_from_db()

        self.assertEqual(concept.sources.count(), 2)
        self.assertTrue(version1.is_latest_version)
        self.assertFalse(source.is_latest_version)

        source_versions = Source.objects.filter(
            mnemonic=source.mnemonic,
            version='v1',
        )
        self.assertTrue(source_versions.exists())
        self.assertEqual(version1.concepts.count(), 1)

        version1.delete()
        source.refresh_from_db()

        self.assertFalse(Source.objects.filter(
            version='v1',
            mnemonic=source.mnemonic,
        ).exists())
        self.assertTrue(source.is_latest_version)
        self.assertEqual(concept.sources.count(), 1)

        with self.assertRaises(ValidationError) as ex:
            source.delete()

        self.assertEqual(
            ex.exception.message_dict,
            {
                'detail': ['Cannot delete only version.'],
            }
        )


    def test_child_count_updates(self):
        source = SourceFactory(version=HEAD)
        self.assertEqual(source.active_concepts, 0)
        datetime_format = '%m/%d/%Y %H:%M:%s'
        self.assertTrue(
            source.updated_at.strftime(
                datetime_format
            ) == source.last_concept_update.strftime(
                datetime_format
            ) == source.last_child_update.strftime(
                datetime_format
            )
        )

        concept = ConceptFactory(sources=[source], parent=source)

        source.save()

        self.assertEqual(source.active_concepts, 1)
        self.assertEqual(source.last_concept_update, concept.updated_at)
        self.assertEqual(source.last_child_update, source.last_concept_update)

    def test_source_active_inactive_should_affect_children(self):
        source = SourceFactory(is_active=True)
        concept = ConceptFactory(parent=source, is_active=True)

        source.is_active = False
        source.save()
        concept.refresh_from_db()

        self.assertFalse(source.is_active)
        self.assertFalse(concept.is_active)

        source.is_active = True
        source.save()
        concept.refresh_from_db()

        self.assertTrue(source.is_active)
        self.assertTrue(concept.is_active)
