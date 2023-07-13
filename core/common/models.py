from celery.result import AsyncResult
from celery_once import AlreadyQueued
from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, IntegrityError, transaction
from django.db.models import Value, Q, Count
from django.db.models.expressions import CombinedExpression, F
from django.utils import timezone
from django.utils.functional import cached_property
from django_elasticsearch_dsl.registries import registry
from django_elasticsearch_dsl.signals import RealTimeSignalProcessor
from elasticsearch import TransportError
from pydash import get, compact

from core.common.tasks import update_collection_active_concepts_count, update_collection_active_mappings_count, \
    delete_s3_objects
from core.common.utils import reverse_resource, reverse_resource_version, parse_updated_since_param, drop_version, \
    to_parent_uri, is_canonical_uri, get_export_service, from_string_to_date, get_truthy_values
from core.common.utils import to_owner_uri
from core.settings import DEFAULT_LOCALE
from .checksums import ChecksumModel
from .constants import (
    ACCESS_TYPE_CHOICES, DEFAULT_ACCESS_TYPE, NAMESPACE_REGEX,
    ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT, SUPER_ADMIN_USER_ID,
    HEAD, PERSIST_NEW_ERROR_MESSAGE, SOURCE_PARENT_CANNOT_BE_NONE, PARENT_RESOURCE_CANNOT_BE_NONE,
    CREATOR_CANNOT_BE_NONE, CANNOT_DELETE_ONLY_VERSION, OPENMRS_VALIDATION_SCHEMA, VALIDATION_SCHEMAS,
    DEFAULT_VALIDATION_SCHEMA, ES_REQUEST_TIMEOUT)
from .exceptions import Http400
from .fields import URIField
from .tasks import handle_save, handle_m2m_changed, seed_children_to_new_version, update_validation_schema, \
    update_source_active_concepts_count, update_source_active_mappings_count


TRUTHY = get_truthy_values()


class BaseModel(models.Model):
    """
    Base model from which all resources inherit.
    Contains timestamps and is_active field for logical deletion.
    """
    class Meta:
        abstract = True
        indexes = [
            models.Index(fields=['-updated_at']),
            models.Index(fields=['-created_at']),
            models.Index(fields=['is_active']),
        ]

    id = models.BigAutoField(primary_key=True)
    public_access = models.CharField(
        max_length=16, choices=ACCESS_TYPE_CHOICES, default=DEFAULT_ACCESS_TYPE, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        'users.UserProfile',
        related_name='%(app_label)s_%(class)s_related_created_by',
        related_query_name='%(app_label)s_%(class)ss_created_by',
        on_delete=models.DO_NOTHING,
        default=SUPER_ADMIN_USER_ID,
    )
    updated_by = models.ForeignKey(
        'users.UserProfile',
        related_name='%(app_label)s_%(class)s_related_updated_by',
        related_query_name='%(app_label)s_%(class)ss_updated_by',
        on_delete=models.DO_NOTHING,
        default=SUPER_ADMIN_USER_ID,
    )
    is_active = models.BooleanField(default=True)
    extras = models.JSONField(null=True, blank=True, default=dict)
    uri = models.TextField(null=True, blank=True)
    _index = True

    @property
    def model_name(self):
        return self.__class__.__name__

    @property
    def app_name(self):
        return self.__module__.split('.')[1]

    def index(self):
        if not get(settings, 'TEST_MODE', False):
            handle_save.delay(self.app_name, self.model_name, self.id)

    @property
    def should_index(self):
        if getattr(self, '_index', None) is not None:
            return self._index
        return True

    def soft_delete(self):
        if self.is_active:
            self.is_active = False
            self.save()

    def undelete(self):
        if not self.is_active:
            self.is_active = True
            self.save()

    @property
    def is_versioned(self):
        return False

    @property
    def public_can_view(self):
        return self.public_access.lower() in [ACCESS_TYPE_EDIT.lower(), ACCESS_TYPE_VIEW.lower()]

    @property
    def public_can_edit(self):
        return self.public_access.lower() == ACCESS_TYPE_EDIT.lower()

    @property
    def resource_type(self):
        return get(self, 'OBJECT_TYPE')

    @property
    def resource_version_type(self):
        return get(self, 'OBJECT_VERSION_TYPE') or self.resource_type

    @property
    def url(self):
        if self.uri:
            return self.uri

        return self.calculate_uri()

    def calculate_uri(self):
        if self.is_versioned and not self.is_head:
            uri = reverse_resource_version(self, self.view_name)
        else:
            uri = reverse_resource(self, self.view_name)

        return uri

    @property
    def view_name(self):
        return self.get_default_view_name()

    def get_default_view_name(self):
        entity_name = self.__class__.__name__.lower()

        if self.is_versioned and not self.is_head:
            return f"{entity_name}-version-detail"

        return f"{entity_name}-detail"

    @classmethod
    def pause_indexing(cls):
        cls.toggle_indexing(False)

    @classmethod
    def resume_indexing(cls):
        if not get(settings, 'TEST_MODE', False):
            cls.toggle_indexing(True)   # pragma: no cover

    @staticmethod
    def toggle_indexing(state=True):
        settings.ELASTICSEARCH_DSL_AUTO_REFRESH = state
        settings.ELASTICSEARCH_DSL_AUTOSYNC = state
        settings.ES_SYNC = state

    @staticmethod
    def get_exact_or_criteria(attr, values):
        criteria = Q()

        if isinstance(values, str):
            values = values.split(',')

        for value in values:
            criteria = criteria | Q(**{f'{attr}': value})

        return criteria

    @staticmethod
    def batch_index(queryset, document):
        count = queryset.count()
        batch_size = 1000
        offset = 0
        limit = batch_size
        while offset < count:
            print(f"Indexing {offset}-{min([limit, count])}/{count}")
            document().update(queryset.order_by('-id')[offset:limit], parallel=True)
            offset = limit
            limit += batch_size

    @staticmethod
    @transaction.atomic
    def batch_delete(queryset):
        for batch in queryset.iterator(chunk_size=1000):
            batch.delete()


class CommonLogoModel(models.Model):
    logo_path = models.TextField(null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def logo_url(self):
        url = None
        if self.logo_path:
            url = get_export_service().public_url_for(self.logo_path)

        return url

    def upload_base64_logo(self, data, name):
        name = self.uri[1:] + name
        self.logo_path = get_export_service().upload_base64(data, name, False, True)
        self.save()


class BaseResourceModel(BaseModel, CommonLogoModel):
    """
    A base resource has a mnemonic that is unique across all objects of its type.
    A base resource may contain sub-resources.
    (An Organization is a base resource, but a Concept is not.)
    """
    mnemonic = models.CharField(max_length=255, validators=[RegexValidator(regex=NAMESPACE_REGEX)],)
    mnemonic_attr = 'mnemonic'

    class Meta:
        abstract = True
        indexes = [
            models.Index(fields=['mnemonic']),
        ] + BaseModel.Meta.indexes

    def __str__(self):
        return str(self.mnemonic)


class VersionedModel(BaseResourceModel):
    version = models.CharField(max_length=255)
    released = models.BooleanField(default=False, blank=True, null=True)
    retired = models.BooleanField(default=False)
    is_latest_version = models.BooleanField(default=True)
    name = models.TextField()
    full_name = models.TextField(null=True, blank=True)
    default_locale = models.TextField(default=DEFAULT_LOCALE, blank=True)
    supported_locales = ArrayField(models.CharField(max_length=20), null=True, blank=True)
    website = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    external_id = models.TextField(null=True, blank=True)
    custom_validation_schema = models.TextField(blank=True, null=True)

    class Meta:
        abstract = True
        indexes = [
            models.Index(fields=['retired']),
        ] + BaseResourceModel.Meta.indexes

    @property
    def is_versioned(self):
        return True

    @property
    def versioned_resource_type(self):
        return self.resource_type

    @property
    def versions(self):
        return self.__class__.objects.filter(**{self.mnemonic_attr: self.mnemonic}).order_by('-created_at')

    @property
    def active_versions(self):
        return self.versions.filter(is_active=True)

    @property
    def released_versions(self):
        return self.active_versions.filter(released=True)

    @property
    def num_versions(self):
        return self.versions.count()

    @property
    def released_versions_count(self):
        return self.versions.filter(released=True).count()

    @property
    def sibling_versions(self):
        return self.versions.exclude(id=self.id)

    @property
    def prev_version(self):
        return self.sibling_versions.filter(
            is_active=True, created_at__lte=self.created_at
        ).order_by('-created_at').first()

    @property
    def prev_version_uri(self):
        return get(self, 'prev_version.uri')

    @property
    def is_head(self):
        return self.version == HEAD

    def get_head(self):
        return self if self.is_head else self.active_versions.filter(version=HEAD).first()

    head = property(get_head)

    @property
    def versioned_object_url(self):
        return drop_version(self.uri)

    @classmethod
    def get_version(cls, mnemonic, version=HEAD, filters=None):
        if not filters:
            filters = {}
        return cls.objects.filter(**{cls.mnemonic_attr: mnemonic, **filters}, version=version).first()

    def get_latest_version(self):
        return self.active_versions.filter(is_latest_version=True).order_by('-created_at').first()

    def get_last_version(self):
        return self.active_versions.order_by('-created_at').first()

    def get_latest_released_version(self):
        return self.released_versions.order_by('-created_at').first()

    @classmethod
    def find_latest_released_version_by(cls, filters):
        return cls.objects.filter(**filters, released=True).order_by('-created_at').first()

    def get_url_kwarg(self):
        if self.is_head:
            return self.get_resource_url_kwarg()
        return self.get_version_url_kwarg()

    @property
    def versions_url(self):
        return drop_version(self.uri) + 'versions/'


class ConceptContainerModel(VersionedModel, ChecksumModel):
    """
    A sub-resource is an object that exists within the scope of its parent resource.
    Its mnemonic is unique within the scope of its parent resource.
    (A Source is a sub-resource, but an Organization is not.)
    """
    organization = models.ForeignKey('orgs.Organization', on_delete=models.CASCADE, blank=True, null=True)
    user = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, blank=True, null=True)
    _background_process_ids = ArrayField(models.CharField(max_length=255), default=list, null=True, blank=True)

    canonical_url = URIField(null=True, blank=True)
    identifier = models.JSONField(null=True, blank=True, default=dict)
    contact = models.JSONField(null=True, blank=True, default=dict)
    jurisdiction = models.JSONField(null=True, blank=True, default=dict)
    publisher = models.TextField(null=True, blank=True)
    purpose = models.TextField(null=True, blank=True)
    copyright = models.TextField(null=True, blank=True)
    revision_date = models.DateTimeField(null=True, blank=True)
    text = models.TextField(null=True, blank=True)  # for about description (markup)
    client_configs = GenericRelation(
        'client_configs.ClientConfig', object_id_field='resource_id', content_type_field='resource_type'
    )
    snapshot = models.JSONField(null=True, blank=True, default=dict)
    experimental = models.BooleanField(null=True, blank=True, default=None)
    meta = models.JSONField(null=True, blank=True)
    active_concepts = models.IntegerField(null=True, blank=True, default=None)
    active_mappings = models.IntegerField(null=True, blank=True, default=None)
    custom_validation_schema = models.CharField(
        choices=VALIDATION_SCHEMAS, default=DEFAULT_VALIDATION_SCHEMA, max_length=100
    )

    CHECKSUM_INCLUSIONS = [
        'canonical_url',
        'extras', 'released', 'retired',
        'default_locale', 'supported_locales',
        'website', 'custom_validation_schema',
    ]

    class Meta:
        abstract = True
        indexes = [
                      models.Index(fields=['version'])
                  ] + VersionedModel.Meta.indexes

    @property
    def is_collection(self):
        from core.collections.models import Collection
        return self.resource_type == Collection.OBJECT_TYPE

    @property
    def should_set_active_concepts(self):
        return self.active_concepts is None

    @property
    def should_set_active_mappings(self):
        return self.active_mappings is None

    @property
    def is_openmrs_schema(self):
        return self.custom_validation_schema == OPENMRS_VALIDATION_SCHEMA

    def update_children_counts(self, sync=False):
        self.update_concepts_count(sync)
        self.update_mappings_count(sync)

    def update_mappings_count(self, sync=False):
        try:
            if sync or get(settings, 'TEST_MODE'):
                self.set_active_mappings()
                self.save(update_fields=['active_mappings'])
            elif self.__class__.__name__ == 'Source':
                update_source_active_mappings_count.apply_async((self.id,), queue='concurrent')
            elif self.__class__.__name__ == 'Collection':
                update_collection_active_mappings_count.apply_async((self.id,), queue='concurrent')
        except AlreadyQueued:
            pass

    def update_concepts_count(self, sync=False):
        try:
            if sync or get(settings, 'TEST_MODE'):
                self.set_active_concepts()
                self.save(update_fields=['active_concepts'])
            elif self.__class__.__name__ == 'Source':
                update_source_active_concepts_count.apply_async((self.id,), queue='concurrent')
            elif self.__class__.__name__ == 'Collection':
                update_collection_active_concepts_count.apply_async((self.id,), queue='concurrent')
        except AlreadyQueued:
            pass

    @property
    def last_child_update(self):
        last_concept_update = self.last_concept_update
        last_mapping_update = self.last_mapping_update
        if last_concept_update and last_mapping_update:
            return max(last_concept_update, last_mapping_update)
        return last_concept_update or last_mapping_update or self.updated_at or timezone.now()

    def get_last_child_update_from_export_url(self, export_url):
        generic_path = self.generic_export_path(suffix=None)
        try:
            last_child_updated_at = export_url.split(generic_path)[1].split('?')[0].replace('.zip', '')
            return from_string_to_date(last_child_updated_at).isoformat()
        except:  # pylint: disable=bare-except
            return None

    @classmethod
    def get_base_queryset(cls, params):
        username = params.get('user', None)
        org = params.get('org', None)
        version = params.get('version', None)
        is_latest = params.get('is_latest', None) in TRUTHY
        updated_since = parse_updated_since_param(params)

        queryset = cls.objects.filter(is_active=True)
        if username:
            queryset = queryset.filter(cls.get_exact_or_criteria('user__username', username))
        if org:
            queryset = queryset.filter(cls.get_exact_or_criteria('organization__mnemonic', org))
        if version:
            queryset = queryset.filter(cls.get_exact_or_criteria('version', version))
        if is_latest:
            queryset = queryset.filter(is_latest_version=True)
        if updated_since:
            queryset = queryset.filter(updated_at__gte=updated_since)

        return queryset

    @property
    def concepts_url(self):
        return reverse_resource(self, 'concept-list')

    @property
    def mappings_url(self):
        return reverse_resource(self, 'mapping-list')

    @property
    def parent(self):
        parent = None
        if self.organization_id:
            parent = self.organization
        if self.user_id:
            parent = self.user

        return parent

    @property
    def parent_id(self):
        return self.organization_id or self.user_id

    @property
    def parent_url(self):
        return to_owner_uri(self.uri)

    @property
    def parent_resource(self):
        return get(self, 'parent.mnemonic')

    @property
    def parent_resource_type(self):
        return get(self, 'parent.resource_type')

    @property
    def versions(self):
        return super().versions.filter(
            organization_id=self.organization_id, user_id=self.user_id
        ).order_by('-created_at')

    def delete(self, using=None, keep_parents=False, force=False):  # pylint: disable=arguments-differ
        if self.is_head:
            self.versions.exclude(id=self.id).delete()
        elif self.is_latest_version:
            prev_version = self.prev_version
            if not force and not prev_version:
                raise ValidationError({'detail': CANNOT_DELETE_ONLY_VERSION})
            if prev_version:
                prev_version.is_latest_version = True
                prev_version.save()

        self.delete_pins()

        generic_export_path = self.generic_export_path(suffix=None)
        super().delete(using=using, keep_parents=keep_parents)
        delete_s3_objects.delay(generic_export_path)
        self.post_delete_actions()

    def post_delete_actions(self):
        pass

    def delete_pins(self):
        if self.is_head:
            from core.pins.models import Pin
            Pin.objects.filter(resource_type__model=self.resource_type.lower(), resource_id=self.id).delete()

    def get_active_concepts(self):
        return self.get_concepts_queryset().filter(is_active=True, retired=False)

    def get_active_mappings(self):
        return self.get_mappings_queryset().filter(is_active=True, retired=False)

    active_concepts_queryset = property(get_active_concepts)
    active_mappings_queryset = property(get_active_mappings)

    def has_parent_edit_access(self, user):
        if user.is_staff:
            return True

        if self.organization_id:
            return self.parent.is_member(user)

        return self.user_id == user.id

    def has_edit_access(self, user):
        if self.public_can_edit or user.is_staff:
            return True

        return self.has_parent_edit_access(user)

    @staticmethod
    def get_version_url_kwarg():
        return 'version'

    def set_parent(self, parent_resource):
        parent_resource_type = parent_resource.resource_type

        if parent_resource_type == 'Organization':
            self.organization = parent_resource
        elif parent_resource_type in ['UserProfile', 'User']:
            self.user = parent_resource

    @staticmethod
    def cascade_children_to_expansion(**kwargs):
        pass

    def update_mappings(self):
        pass

    def seed_references(self):
        pass

    @property
    def should_auto_expand(self):
        return True

    @property
    def identity_uris(self):
        return compact([self.uri, self.canonical_url])

    @classmethod
    def persist_new(cls, obj, created_by, **kwargs):
        errors = {}
        parent_resource = kwargs.pop('parent_resource', None) or obj.parent
        if not parent_resource:
            errors['parent'] = PARENT_RESOURCE_CANNOT_BE_NONE
            return errors
        obj.set_parent(parent_resource)
        user = created_by
        if not user:
            errors['created_by'] = CREATOR_CANNOT_BE_NONE
        if errors:
            return errors

        obj.created_by = user
        obj.updated_by = user
        try:
            obj.full_clean()
        except ValidationError as ex:
            errors.update(ex.message_dict)
        if errors:
            return errors

        persisted = False
        obj.version = HEAD
        try:
            obj.save(**kwargs)
            if obj.id:
                obj.post_create_actions()
            persisted = True
        except IntegrityError as ex:
            errors.update({'__all__': ex.args})
        finally:
            if not persisted:
                errors['non_field_errors'] = PERSIST_NEW_ERROR_MESSAGE.format(cls.__name__)
        return errors

    @classmethod
    def persist_new_version(cls, obj, user=None, **kwargs):
        from core.collections.serializers import CollectionDetailSerializer
        from core.sources.serializers import SourceDetailSerializer

        errors = {}

        obj.is_active = True
        sync = kwargs.pop('sync', False)
        if user:
            obj.created_by = user
            obj.updated_by = user
        repo_resource_name = obj.__class__.__name__
        serializer = SourceDetailSerializer if repo_resource_name == 'Source' else CollectionDetailSerializer
        head = obj.head
        if not head:
            errors[repo_resource_name.lower()] = 'Version Head not found.'
            return errors
        obj.snapshot = serializer(head).data
        obj.update_version_data(head)
        obj.save(**kwargs)

        is_test_mode = get(settings, 'TEST_MODE', False)
        if is_test_mode or sync:
            seed_children_to_new_version(obj.resource_type.lower(), obj.id, not is_test_mode, sync)
        else:
            seed_children_to_new_version.delay(obj.resource_type.lower(), obj.id, True, sync)

        if obj.id:
            obj.sibling_versions.update(is_latest_version=False)

        return errors

    @classmethod
    def persist_changes(cls, obj, updated_by, original_schema, **kwargs):
        errors = {}
        parent_resource = kwargs.pop('parent_resource', obj.parent)
        if not parent_resource:
            errors['parent'] = SOURCE_PARENT_CANNOT_BE_NONE

        queue_schema_update_task = obj.is_validation_necessary()

        try:
            obj.full_clean()
        except ValidationError as ex:
            errors.update(ex.message_dict)

        if errors:
            return errors

        if updated_by:
            obj.updated_by = updated_by
        try:
            if queue_schema_update_task:
                target_schema = obj.custom_validation_schema
                obj.custom_validation_schema = original_schema

            obj.save(**kwargs)

            if queue_schema_update_task:
                update_validation_schema.delay(obj.app_name, obj.id, target_schema)
        except IntegrityError as ex:
            errors.update({'__all__': ex.args})

        return errors

    def validate_child_concepts(self):
        # If source is being configured to have a validation schema
        # we need to validate all concepts
        # according to the new schema
        from core.concepts.validators import ValidatorSpecifier

        concepts = self.get_active_concepts()
        failed_concept_validations = []

        validator = ValidatorSpecifier().with_validation_schema(
            self.custom_validation_schema
        ).with_repo(self).with_reference_values().get()

        for concept in concepts:
            try:
                validator.validate(concept)
            except ValidationError as validation_error:
                concept_validation_error = {
                    'mnemonic': concept.mnemonic,
                    'url': concept.url,
                    'errors': validation_error.message_dict
                }
                failed_concept_validations.append(concept_validation_error)

        return failed_concept_validations

    def update_version_data(self, head):
        self.description = self.description or head.description
        self.name = head.name
        self.full_name = head.full_name
        self.website = head.website
        self.public_access = head.public_access
        self.supported_locales = head.supported_locales
        self.default_locale = head.default_locale
        self.external_id = head.external_id
        self.organization = head.organization
        self.user = head.user
        self.canonical_url = head.canonical_url
        self.identifier = head.identifier
        self.contact = head.contact
        self.jurisdiction = head.jurisdiction
        self.publisher = head.publisher
        self.purpose = head.purpose
        self.copyright = head.copyright
        self.revision_date = head.revision_date
        self.text = head.text
        self.experimental = head.experimental
        self.custom_validation_schema = head.custom_validation_schema
        self.extras = head.extras

    def add_processing(self, process_id):
        if self.id and process_id:
            self.__class__.objects.filter(id=self.id).update(
                _background_process_ids=CombinedExpression(
                    F('_background_process_ids'),
                    '||',
                    Value([process_id], ArrayField(models.CharField(max_length=255)))
                )
            )
        if process_id:
            self._background_process_ids.append(process_id)

    def remove_processing(self, process_id):
        if self.id and self._background_process_ids and process_id in self._background_process_ids:
            self._background_process_ids.remove(process_id)
            self.save(update_fields=['_background_process_ids'])

    @property
    def is_processing(self):
        background_ids = self._background_process_ids
        if background_ids:
            for process_id in background_ids.copy():
                if process_id:
                    res = AsyncResult(process_id)
                    if res.successful() or res.failed():
                        self.remove_processing(process_id)
                    else:
                        return True
                else:
                    self.remove_processing(process_id)

        return False

    def clear_processing(self):
        self._background_process_ids = []
        self.save(update_fields=['_background_process_ids'])

    def get_supported_locales(self):
        locales = [self.default_locale]
        if self.supported_locales:
            # to maintain the order of default locale always first
            locales += [locale for locale in self.supported_locales if locale != self.default_locale]
        return locales

    @property
    def is_exporting(self):
        is_processing = self.is_processing

        if is_processing:
            for process_id in self._background_process_ids:
                res = AsyncResult(process_id)
                task_name = res.name
                if task_name and task_name.startswith('core.common.tasks.export_'):
                    return True

        return False

    @cached_property
    def export_path(self):
        last_update = self.last_child_update.strftime('%Y%m%d%H%M%S')
        return self.generic_export_path(suffix=f"{last_update}.zip")

    def generic_export_path(self, suffix='*'):
        path = f"{self.parent_resource}/{self.mnemonic}_{self.version}."
        if suffix:
            path += suffix

        return path

    def get_export_url(self):
        service = get_export_service()
        if self.is_head:
            path = self.export_path
        else:
            path = service.get_last_key_from_path(self.generic_export_path(suffix=None)) or self.export_path
        return service.url_for(path)

    def has_export(self):
        service = get_export_service()
        if self.is_head:
            return service.exists(self.export_path)
        return service.has_path(self.generic_export_path(suffix=None))

    def can_view_all_content(self, user):
        if get(user, 'is_anonymous'):
            return False
        return get(
            user, 'is_staff'
        ) or self.public_can_view or self.user_id == user.id or self.organization.members.filter(id=user.id).exists()

    @classmethod
    def resolve_expression_to_version(cls, expression):
        url = expression
        namespace = None
        version = None
        instance = None
        if isinstance(expression, dict) and get(expression, 'url'):
            url = expression['url']
            namespace = expression.get('namespace', None)
            version = expression.get('version', None)
        if url:
            instance = cls.resolve_reference_expression(url, namespace, version)
        return instance

    @staticmethod
    def resolve_reference_expression(url, namespace=None, version=None):
        lookup_url = url
        if '|' in lookup_url:
            lookup_url, version = lookup_url.split('|')

        lookup_url = lookup_url.split('?')[0]

        is_fqdn = is_canonical_uri(lookup_url) or is_canonical_uri(url)

        criteria = models.Q(is_active=True, retired=False)
        if is_fqdn:
            resolution_url = lookup_url
            criteria &= models.Q(canonical_url=resolution_url)
            if namespace:
                criteria &= models.Q(models.Q(user__uri=namespace) | models.Q(organization__uri=namespace))
        else:
            resolution_url = to_parent_uri(lookup_url)
            criteria &= models.Q(uri=resolution_url)

        from core.sources.models import Source
        instance = Source.objects.filter(criteria).first()
        if not instance:
            from core.collections.models import Collection
            instance = Collection.objects.filter(criteria).first()

        if instance:
            if version:
                instance = instance.versions.filter(version=version).first()
            elif instance.is_head:
                instance = instance.get_latest_released_version() or instance

        if not instance:
            instance = Source()

        instance.is_fqdn = is_fqdn
        instance.resolution_url = resolution_url
        if is_fqdn and instance.id and not instance.canonical_url:
            instance.canonical_url = resolution_url

        return instance

    def clean(self):
        if not self.custom_validation_schema:
            self.custom_validation_schema = DEFAULT_VALIDATION_SCHEMA

        super().clean()

        if self.released and not self.revision_date:
            self.revision_date = timezone.now()

    @property
    def map_types_count(self):
        return self.get_active_mappings().aggregate(count=Count('map_type', distinct=True))['count']

    @property
    def concept_class_count(self):
        return self.get_active_concepts().aggregate(count=Count('concept_class', distinct=True))['count']

    @property
    def datatype_count(self):
        return self.get_active_concepts().aggregate(count=Count('datatype', distinct=True))['count']

    @property
    def retired_concepts_count(self):
        return self.get_concepts_queryset().filter(retired=True).count()

    @property
    def retired_mappings_count(self):
        return self.get_mappings_queryset().filter(retired=True).count()

    @property
    def concepts_distribution(self):
        facets = self.get_concept_facets()
        return {
            'active': self.active_concepts,
            'retired': self.retired_concepts_count,
            'concept_class': self._to_clean_facets(facets.conceptClass or []),
            'datatype': self._to_clean_facets(facets.datatype or []),
            'locale': self._to_clean_facets(facets.locale or []),
            'name_type': self._to_clean_facets(facets.nameTypes or [])
        }

    @property
    def mappings_distribution(self):
        facets = self.get_mapping_facets()

        return {
            'active': self.active_mappings,
            'retired': self.retired_mappings_count,
            'map_type': self._to_clean_facets(facets.mapType or [])
        }

    @property
    def versions_distribution(self):
        return {
            'total': self.num_versions,
            'released': self.released_versions_count
        }

    def get_concepts_extras_distribution(self):
        return self.get_distinct_extras_keys(self.get_concepts_queryset(), 'concepts')

    @staticmethod
    def get_distinct_extras_keys(queryset, resource):
        return set(queryset.exclude(retired=True).extra(
            select={'key': f"jsonb_object_keys({resource}.extras)"}).values_list('key', flat=True))

    def get_name_locales_queryset(self):
        from core.concepts.models import ConceptName
        return ConceptName.objects.filter(concept__in=self.get_active_concepts())

    @property
    def concept_names_distribution(self):
        locales = self.get_name_locales_queryset()
        locales_total = locales.distinct('locale').count()
        names_total = locales.distinct('type').count()
        return {'locales': locales_total, 'names': names_total}

    def get_name_locale_distribution(self):
        return self._get_distribution(self.get_name_locales_queryset(), 'locale')

    def get_name_type_distribution(self):
        return self._get_distribution(self.get_name_locales_queryset(), 'type')

    def get_concept_class_distribution(self):
        return self._get_distribution(self.get_active_concepts(), 'concept_class')

    def get_datatype_distribution(self):
        return self._get_distribution(self.get_active_concepts(), 'datatype')

    def get_map_type_distribution(self):
        return self._get_distribution(self.get_active_mappings(), 'map_type')

    @staticmethod
    def _get_distribution(queryset, field):
        return list(queryset.values(field).annotate(count=Count('id')).values(field, 'count').order_by('-count'))

    def get_concept_facets(self, filters=None):
        from core.concepts.search import ConceptFacetedSearch
        return self._get_resource_facets(ConceptFacetedSearch, filters)

    def get_mapping_facets(self, filters=None):
        from core.mappings.search import MappingFacetedSearch
        return self._get_resource_facets(MappingFacetedSearch, filters)

    def _get_resource_facets(self, facet_class, filters=None):
        search = facet_class('', filters=self._get_resource_facet_filters(filters))
        search.params(request_timeout=ES_REQUEST_TIMEOUT)
        try:
            facets = search.execute().facets
        except TransportError as ex:  # pragma: no cover
            raise Http400(detail='Data too large.') from ex

        return facets

    def _to_clean_facets(self, facets, remove_self=False):
        _facets = []
        for facet in facets:
            _facet = facet[:2]
            if remove_self:
                if facet[0] != self.mnemonic:
                    _facets.append(_facet)
            else:
                _facets.append(_facet)
        return _facets


class CelerySignalProcessor(RealTimeSignalProcessor):
    def handle_save(self, sender, instance, **kwargs):
        if settings.ES_SYNC and instance.__class__ in registry.get_models() and instance.should_index:
            if get(settings, 'TEST_MODE', False):
                handle_save(instance.app_name, instance.model_name, instance.id)
            else:
                handle_save.delay(instance.app_name, instance.model_name, instance.id)

    def handle_m2m_changed(self, sender, instance, action, **kwargs):
        if settings.ES_SYNC and instance.__class__ in registry.get_models() and instance.should_index:
            if get(settings, 'TEST_MODE', False):
                handle_m2m_changed(instance.app_name, instance.model_name, instance.id, action)
            else:
                handle_m2m_changed.delay(instance.app_name, instance.model_name, instance.id, action)
