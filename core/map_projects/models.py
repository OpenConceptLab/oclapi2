import json

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models, IntegrityError
from django.db.models import UniqueConstraint
from pydash import get

from core.common.constants import PERSIST_NEW_ERROR_MESSAGE
from core.common.models import BaseModel
from core.common.utils import get_export_service, generate_temp_version


class MapProject(BaseModel):
    name = models.TextField()
    description = models.TextField(null=True, blank=True)
    organization = models.ForeignKey(
        'orgs.Organization', on_delete=models.CASCADE, null=True, blank=True, related_name='map_projects')
    user = models.ForeignKey(
        'users.UserProfile', on_delete=models.CASCADE, null=True, blank=True, related_name='map_projects')
    input_file_name = models.TextField()
    matches = ArrayField(models.JSONField(), default=list, null=True, blank=True)
    columns = ArrayField(models.JSONField(), default=list)
    target_repo_url = models.TextField(null=True, blank=True)
    matching_algorithm = models.CharField(max_length=100, null=True, blank=True)

    OBJECT_TYPE = 'MapProject'
    mnemonic_attr = 'id'

    class Meta:
        db_table = 'map_projects'
        constraints = [
            UniqueConstraint(
                fields=['name', 'organization'],
                name="org_map_project_unique",
                condition=models.Q(user=None),
            ),
            UniqueConstraint(
                fields=['name', 'user'],
                name="user_map_project_unique",
                condition=models.Q(organization=None),
            )
        ]

    @property
    def mnemonic(self):
        return self.id

    @property
    def parent(self):
        return self.organization if self.organization_id else self.user

    @property
    def parent_id(self):
        return self.organization_id or self.user_id

    def calculate_uri(self):
        return self.parent.uri + "map-projects/" + str((self.id or generate_temp_version())) + "/"

    @property
    def _upload_dir_path(self):
        return f"map_projects/{self.id}/"

    @property
    def file_path(self):
        return self._upload_dir_path + self.input_file_name

    @property
    def file_url(self):
        if self.input_file_name:
            service = get_export_service()
            return service.url_for(self.file_path)
        return None

    def update_input_file(self, input_file):
        if input_file:
            service = get_export_service()
            file_name = input_file.name
            key = self._upload_dir_path + file_name
            result = service.upload(key=key, file_content=input_file)
            if result == 204:
                self.input_file_name = file_name
                self.save()

    @classmethod
    def persist_new(cls, instance, user, **kwargs):
        errors = {}
        persisted = False

        instance.created_by = instance.updated_by = user
        try:
            input_file = kwargs.pop('input_file', None)
            instance.input_file_name = input_file.name if input_file else None
            instance.full_clean()
            instance.save(**kwargs)
            if instance.id:
                instance.save()
                persisted = True
                instance.update_input_file(input_file)
        except IntegrityError as ex:
            errors.update({'__all__': ex.args})
        except ValidationError as ex:
            errors = get(ex, 'message_dict', {}) or get(ex, 'error_dict', {})
        finally:
            if not persisted and not errors:
                errors['non_field_errors'] = PERSIST_NEW_ERROR_MESSAGE.format(cls.__name__)
        return errors

    @classmethod
    def persist_changes(cls, instance, user, **kwargs):
        errors = {}
        try:
            input_file = kwargs.pop('input_file', None)
            instance.input_file_name = input_file.name if input_file else None
            instance.updated_by = user
            instance.full_clean()
            instance.save(**kwargs)
            if input_file:
                instance.update_input_file(input_file)
        except ValidationError as ex:
            errors.update(get(ex, 'message_dict', {}) or get(ex, 'error_dict', {}))
        except IntegrityError as ex:
            errors.update({'__all__': ex.args})

        return errors

    def delete(self, using=None, keep_parents=False):
        result = super().delete(using=using, keep_parents=keep_parents)

        self.__delete_uploaded_file()
        return result

    def __delete_uploaded_file(self):
        from core.common.tasks import delete_s3_objects
        delete_s3_objects.apply_async((self.file_path,), queue='default', permanent=False)

    @classmethod
    def format_request_data(cls, data, parent_resource=None):
        new_data = {
            key: val[0] if isinstance(val, list) and len(val) == 1 else val for key, val in data.items()
        }
        cls.format_json(new_data, 'matches')
        cls.format_json(new_data, 'columns')

        if parent_resource:
            new_data[parent_resource.resource_type.lower() + '_id'] = parent_resource.id

        file = data.get('file')
        if file:
            new_data['input_file_name'] = file.name

        return new_data

    @staticmethod
    def format_json(new_data, field):
        if field in new_data and isinstance(new_data[field], str):
            try:
                new_data[field] = json.loads(new_data[field])
            except json.JSONDecodeError:
                pass

    def soft_delete(self):
        self.delete()

    def clean(self):
        if self.matches:
            try:
                self.matches = json.loads(self.matches)
            except (json.JSONDecodeError, TypeError):
                pass
