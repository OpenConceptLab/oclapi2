from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from core.collections.models import Collection
from core.common.constants import SUPER_ADMIN_USER_ID
from core.common.utils import get_export_service
from core.sources.models import Source


class Repository:
    @classmethod
    def get(cls, criteria):
        repo = Source.objects.filter(criteria).first()

        if not repo:
            repo = Collection.objects.filter(criteria).first()

        return repo


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
