from itertools import chain

from dirtyfields import DirtyFieldsMixin
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from core.collections.models import Collection
from core.common.constants import SUPER_ADMIN_USER_ID, LIST_DEFAULT_LIMIT
from core.common.utils import get_export_service, to_int
from core.sources.models import Source


class CountableRepoList(list):
    def count(self):
        return len(self)


class Repository:
    @classmethod
    def get(cls, criteria):
        repo = Source.objects.filter(criteria).first()

        if not repo:
            repo = Collection.objects.filter(criteria).first()

        return repo

    @classmethod
    def get_base_querysets(cls, params, exclude_retired=True):
        sources = Source.get_base_queryset(params.copy())
        collections = Collection.get_base_queryset(params.copy())

        if exclude_retired:
            sources = sources.exclude(retired=True)
            collections = collections.exclude(retired=True)

        return sources, collections

    @classmethod
    def merge_querysets(cls, sources, collections, limit, page):
        total_count = sources.count() + collections.count()

        limit = to_int(limit, LIST_DEFAULT_LIMIT) or LIST_DEFAULT_LIMIT
        if limit > 1000:
            limit = LIST_DEFAULT_LIMIT
        window = to_int(page, 1) * limit

        windowed_sources = sources.order_by('-updated_at')[:window]
        windowed_collections = collections.order_by('-updated_at')[:window]
        merged = sorted(
            chain(windowed_sources, windowed_collections), key=lambda repo: repo.updated_at, reverse=True
        )
        return CountableRepoList(merged), total_count


class RepoExternalExport(models.Model):
    class Meta:
        db_table = 'repo_external_exports'
        unique_together = ('resource_type', 'resource_id', 'key')

    key = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    file_path = models.CharField(max_length=512)

    resource_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    resource_id = models.PositiveIntegerField()
    resource = GenericForeignKey('resource_type', 'resource_id')

    created_by = models.ForeignKey(
        'users.UserProfile', default=SUPER_ADMIN_USER_ID, on_delete=models.SET_DEFAULT,
        related_name='%(app_label)s_%(class)s_related_created_by',
        related_query_name='%(app_label)s_%(class)ss_created_by',
    )
    updated_by = models.ForeignKey(
        'users.UserProfile', default=SUPER_ADMIN_USER_ID, on_delete=models.SET_DEFAULT,
        related_name='%(app_label)s_%(class)s_related_updated_by',
        related_query_name='%(app_label)s_%(class)ss_updated_by',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def uri(self):
        return f"{self.resource.uri}export/{self.key}/"

    @property
    def file_url(self):
        return get_export_service().url_for(self.file_path)

    @property
    def filename(self):
        return self.file_path.split('/')[-1] if self.file_path else None

    @classmethod
    def upsert(cls, repo_version, key, file, user, description=None):  # pylint: disable=too-many-arguments
        instance = repo_version.external_exports.filter(key=key).first()
        is_create = instance is None

        file_path = repo_version.get_external_export_path(key, file.name)
        if instance and instance.file_path != file_path:
            get_export_service().remove(instance.file_path)

        get_export_service().upload(
            key=file_path, file_content=file,
            headers={'content-type': file.content_type},
            metadata={'ContentType': file.content_type}
        )

        if is_create:
            instance = cls(resource=repo_version, key=key, created_by=user)

        if description:
            instance.description = description

        instance.file_path = file_path
        instance.updated_by = user
        instance.save()
        return instance, is_create


class VersionChangelog(DirtyFieldsMixin, models.Model):  # persisted changelog/comparison for a version pair
    JSON_FORMAT = 'json'
    MD_FORMAT = 'markdown'

    class Meta:
        db_table = 'version_changelogs'
        unique_together = ('version1_url', 'version2_url')
        constraints = [
            models.CheckConstraint(
                check=~models.Q(version1_url=models.F('version2_url')),
                name='version_changelog_version1_url_ne_version2_url',
            ),
        ]

    version1_url = models.TextField()
    version2_url = models.TextField()
    changelog = models.JSONField(default=dict)
    comparison = models.JSONField(default=dict)
    extras = models.JSONField(default=dict)

    created_by = models.ForeignKey(
        'users.UserProfile', default=SUPER_ADMIN_USER_ID, on_delete=models.SET_DEFAULT,
        related_name='%(app_label)s_%(class)s_related_created_by',
        related_query_name='%(app_label)s_%(class)ss_created_by',
    )
    updated_by = models.ForeignKey(
        'users.UserProfile', default=SUPER_ADMIN_USER_ID, on_delete=models.SET_DEFAULT,
        related_name='%(app_label)s_%(class)s_related_updated_by',
        related_query_name='%(app_label)s_%(class)ss_updated_by',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def changelog_md(self):
        from core.sources.changelog_markdown import ChangelogMarkdownGenerator
        return ChangelogMarkdownGenerator(self.changelog).generate()

    @classmethod
    def find_or_build(cls, version1, version2):
        if version1.url == version2.url:
            raise ValueError('Cannot build a changelog between a version and itself.')
        return cls.objects.filter(
            version1_url=version1.url, version2_url=version2.url
        ).first() or cls(
            version1_url=version1.url, version2_url=version2.url,
            created_by=version2.created_by, updated_by=version2.updated_by
        )


class VersionChecksumMap(models.Model):  # persisted mnemonic->checksum map for one version
    # No DirtyFieldsMixin here on purpose: for a JSONField holding hundreds of thousands of
    # entries, its snapshot-on-load and is_dirty() comparison each cost real seconds -- more than
    # rebuilding the map from scratch would. The caller already knows whether it changed anything
    # (get_checksum_map only calls save() right after it just set a field), so no dirty-tracking is needed.
    class Meta:
        db_table = 'version_checksum_maps'

    version_url = models.TextField(unique=True)
    concepts_map = models.JSONField(default=dict)  # {'active': {mnemonic: {checksums, id}}, 'retired': {...}}
    mappings_map = models.JSONField(default=dict)
    extras = models.JSONField(default=dict)

    created_by = models.ForeignKey(
        'users.UserProfile', default=SUPER_ADMIN_USER_ID, on_delete=models.SET_DEFAULT,
        related_name='%(app_label)s_%(class)s_related_created_by',
        related_query_name='%(app_label)s_%(class)ss_created_by',
    )
    updated_by = models.ForeignKey(
        'users.UserProfile', default=SUPER_ADMIN_USER_ID, on_delete=models.SET_DEFAULT,
        related_name='%(app_label)s_%(class)s_related_updated_by',
        related_query_name='%(app_label)s_%(class)ss_updated_by',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def find_or_build(cls, version, only=None):
        # only lets a caller that needs just one of concepts_map/mappings_map skip fetching and
        # JSON-decoding the other one, which can be many times larger.
        queryset = cls.objects.filter(version_url=version.url)
        if only:
            queryset = queryset.only(*only, 'updated_at')
        return queryset.first() or cls(
            version_url=version.url, created_by=version.created_by, updated_by=version.created_by
        )
