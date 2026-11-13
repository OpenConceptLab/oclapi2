import json
import re
from collections import Counter

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models, IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from pydash import get

from core.common.constants import PERSIST_NEW_ERROR_MESSAGE, ACCESS_TYPE_CHOICES, ACCESS_TYPE_NONE
from core.common.models import BaseModel
from core.common.utils import get_export_service, generate_temp_version, drop_key_deep, get_url_origin, \
    get_api_base_url


PERSIST_ERROR_MESSAGE = 'The map project could not be saved.'


def default_score_configuration():
    return {'recommended': 100, 'available': 70}


class MapProject(BaseModel):
    OBJECT_TYPE = 'MapProject'
    mnemonic_attr = 'id'
    BATCH_SIZE = 50

    name = models.TextField()
    description = models.TextField(null=True, blank=True)
    # Private unless shared explicitly: only the owner, members of the owning org and staff can see a project.
    public_access_for_retired_edit = ACCESS_TYPE_NONE
    public_access = models.CharField(
        max_length=16, choices=ACCESS_TYPE_CHOICES, default=ACCESS_TYPE_NONE, blank=True
    )
    organization = models.ForeignKey(
        'orgs.Organization', on_delete=models.CASCADE, null=True, blank=True, related_name='map_projects')
    user = models.ForeignKey(
        'users.UserProfile', on_delete=models.CASCADE, null=True, blank=True, related_name='map_projects')
    input_file_name = models.TextField()
    matches = ArrayField(models.JSONField(), default=list, null=True, blank=True)
    columns = ArrayField(models.JSONField(), default=list)
    target_repo_url = models.TextField(null=True, blank=True)
    algorithms = ArrayField(models.JSONField(), default=list, null=True, blank=True)
    include_retired = models.BooleanField(default=False)
    logs = models.JSONField(default=dict, null=True, blank=True)
    score_configuration = models.JSONField(default=default_score_configuration, null=True, blank=True)
    filters = models.JSONField(default=dict, null=True, blank=True)
    candidates = models.JSONField(default=dict, null=True, blank=True)
    lookup_config = models.JSONField(default=dict, null=True, blank=True)
    analysis = models.JSONField(default=dict, null=True, blank=True)
    encoder_model = models.TextField(null=True, blank=True, default=settings.ENCODER_MODEL_NAME)
    prompt_template_key = models.TextField(null=True, blank=True)
    prompt_output_locale = models.CharField(max_length=10, null=True, blank=True)
    # Plural by design — the UI currently exposes single-select, but the
    # field is shaped for the multi-select workflow planned in a few weeks.
    # No further migration needed at that point.
    input_locales = ArrayField(models.CharField(max_length=10), null=True, blank=True, default=list)
    use_lexical_variants = models.BooleanField(default=False)
    # Endpoint tokens split out of algorithms and lookup_config; never serialized.
    credentials = models.JSONField(default=dict, null=True, blank=True)

    # Fields that define how a project matches —
    # excluding identity, results, logs, and audit metadata.
    # Used by the copy-project flow.
    CONFIGURATION_FIELDS = [
        'algorithms', 'encoder_model', 'filters', 'include_retired',
        'lookup_config', 'score_configuration', 'target_repo_url', 'prompt_template_key',
        'prompt_output_locale', 'input_locales', 'use_lexical_variants'
    ]

    # Never returned by the API, in any response or export.
    WRITE_ONLY_FIELDS = ['credentials']

    TOKEN_KEY = 'token'
    HAS_TOKEN_KEY = 'has_token'
    ALGORITHMS_CREDENTIALS_KEY = 'algorithms'
    LOOKUP_CREDENTIALS_KEY = 'lookup'
    # Tokens 0041 found only in logs, kept so they can be revoked; never used or flagged.
    LOGGED_CREDENTIALS_KEY = 'logs'

    class Meta:
        db_table = 'map_projects'

    @classmethod
    def get_readable_field_names(cls):
        return [field.attname for field in cls._meta.concrete_fields if field.name not in cls.WRITE_ONLY_FIELDS]

    @property
    def mnemonic(self):
        return self.id

    @property
    def parent(self):
        return self.organization if self.organization_id else self.user

    @property
    def parent_id(self):
        return self.organization_id or self.user_id

    @property
    def matches_summary(self):
        if self.matches:
            counter = Counter()
            counter.update(match.get('state') for match in self.matches if 'state' in match)
            return dict(counter)
        return None

    @property
    def visible_columns(self):
        if self.columns:
            return [col for col in self.columns if 'hidden' not in col or col['hidden'] is False and col.get('label')]  # pylint: disable=not-an-iterable,line-too-long
        return []

    @property
    def summary(self):
        return {
            'matches': self.matches_summary,
            'columns': [col.get('label') for col in self.visible_columns],
        }

    @property
    def rows_used(self):
        """
        `mapper.rows_per_project` is project-scoped, not user-scoped, and is already
        fully derivable from AutomatchRun - no separate counter needed. Retries
        (parent_run set) re-attempt already-declared rows, so only top-level runs
        count toward the project's declared size.
        """
        return self.auto_match_runs.filter(parent_run__isnull=True).aggregate(total=Sum('intended_rows'))['total'] or 0

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
                self.save(update_fields=['input_file_name'])

    def save(self, *args, **kwargs):
        # Tokens never stay in logs, whichever path saves them.
        if self.logs:
            self.logs = drop_key_deep(self.logs, self.TOKEN_KEY)
        super().save(*args, **kwargs)

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
        except IntegrityError:
            # The database's message can quote the failing row, credentials included.
            errors.update({'__all__': [PERSIST_ERROR_MESSAGE]})
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
            if input_file:  # without a new file, keep the stored one
                instance.input_file_name = input_file.name
            instance.updated_by = user
            with transaction.atomic():
                # Stored tokens are read under a row lock so a concurrent save can't drop a newly stored one.
                instance.credentials = cls.objects.select_for_update().values_list(
                    'credentials', flat=True).get(id=instance.id)
                instance.full_clean()
                instance.save(**kwargs)
            if input_file:
                instance.update_input_file(input_file)
        except ValidationError as ex:
            errors.update(get(ex, 'message_dict', {}) or get(ex, 'error_dict', {}))
        except IntegrityError:
            # The database's message can quote the failing row, credentials included.
            errors.update({'__all__': [PERSIST_ERROR_MESSAGE]})
        except cls.DoesNotExist:
            errors.update({'__all__': ['This map project no longer exists.']})

        return errors

    def delete(self, using=None, keep_parents=False):
        file_path = self.file_path
        result = super().delete(using=using, keep_parents=keep_parents)
        self._delete_uploaded_file(file_path)
        return result

    @staticmethod
    def _delete_uploaded_file(file_path):
        from core.common.tasks import delete_s3_objects
        delete_s3_objects.apply_async((file_path,), queue='default', permanent=False)

    @classmethod
    def format_request_data(cls, data, parent_resource=None):
        # Multipart requests can wrap JSON-capable fields in single-item lists.
        # Keep list-shaped config fields intact so ArrayField-backed values like
        # input_locales do not collapse into a scalar during update flows.
        new_data = {
            key: val[0] if (
                isinstance(val, list) and
                len(val) == 1 and
                not isinstance(val[0], (dict, list)) and
                key not in ['candidates', 'analysis', 'input_locales']
            ) else val for key, val in data.items()
        }
        cls.format_json(new_data, 'matches')
        cls.format_json(new_data, 'columns')
        cls.format_json(new_data, 'score_configuration')
        cls.format_json(new_data, 'filters')
        cls.format_json(new_data, 'candidates')
        cls.format_json(new_data, 'analysis')
        cls.format_json(new_data, 'algorithms')
        cls.format_json(new_data, 'lookup_config')
        cls.format_json(new_data, 'input_locales')

        # The owner comes only from the URL (parent_resource), never from the request body.
        new_data.pop('organization_id', None)
        new_data.pop('user_id', None)
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
        self.clean_filters()
        self.clean_include_retired()
        self.clean_encoder_model()
        self.clean_matches()
        self.clean_tokens()

    def clean_tokens(self):
        if self.lookup_config and not isinstance(self.lookup_config, dict):
            raise ValidationError({'lookup_config': ['Must be an object.']})
        if any(not isinstance(entry, dict) for entry in self.algorithms or []):
            raise ValidationError({'algorithms': ['Each algorithm must be an object.']})
        self.algorithms, self.lookup_config, self.credentials = self.split_tokens(
            self.algorithms, self.lookup_config, self.credentials)

    @staticmethod
    def is_ocl_api_url(url):
        """
        A lookup URL the Mapper sends to this API: none (the target repo), a path, or a URL on its origin.
        As in the Mapper, only an http(s):// URL, in any case, is absolute; anything else is a path on this API.
        """
        if not url:
            return True
        if not isinstance(url, str):
            return False
        if not re.match(r'https?://', url, re.IGNORECASE):
            return True
        return get_url_origin(url) == get_url_origin(get_api_base_url())

    @staticmethod
    def get_endpoint_url(entry):
        url = entry.get('url')
        return url if isinstance(url, str) else ''

    @classmethod
    def without_token(cls, entry):
        entry = drop_key_deep(entry, cls.TOKEN_KEY)
        entry.pop(cls.HAS_TOKEN_KEY, None)
        return entry

    @classmethod
    def take_tokens(cls, entries, stored, field, accept_new=True):
        """
        Tokens of the entries by URL. A missing token keeps the stored one for the same URL, so changing
        the URL drops it; an empty one clears it. Entries sharing a URL share one token.
        """
        new_tokens, kept_urls, cleared_urls = {}, set(), set()
        for entry in entries:
            url = cls.get_endpoint_url(entry)
            token = entry.get(cls.TOKEN_KEY)
            if cls.TOKEN_KEY not in entry or (token and not accept_new):
                kept_urls.add(url)
            elif not (isinstance(token, str) and token.strip()):
                cleared_urls.add(url)
            elif new_tokens.setdefault(url, token) != token:
                raise ValidationError({field: ['Entries with the same URL must use the same token.']})
        tokens = {url: stored[url] for url in kept_urls - cleared_urls if stored.get(url)}
        return {**tokens, **new_tokens}

    @classmethod
    def split_tokens(cls, algorithms, lookup_config, credentials):
        """
        Returns (algorithms, lookup_config, credentials) with tokens moved into credentials. No token is kept
        for an algorithm without a URL, nor a new one for an OCL API lookup, which gets the user's own.
        """
        credentials = credentials if isinstance(credentials, dict) else {}
        algorithm_tokens, lookup_tokens = {}, {}
        if isinstance(algorithms, list):
            algorithm_tokens = cls.take_tokens(
                [entry for entry in algorithms if isinstance(entry, dict) and cls.get_endpoint_url(entry)],
                credentials.get(cls.ALGORITHMS_CREDENTIALS_KEY) or {}, 'algorithms'
            )
            algorithms = [cls.without_token(entry) if isinstance(entry, dict) else entry for entry in algorithms]
        if isinstance(lookup_config, dict):
            lookup_tokens = cls.take_tokens(
                [lookup_config], credentials.get(cls.LOOKUP_CREDENTIALS_KEY) or {}, 'lookup_config',
                accept_new=not cls.is_ocl_api_url(cls.get_endpoint_url(lookup_config))
            )
            lookup_config = cls.without_token(lookup_config)
        return algorithms, lookup_config, {
            key: tokens for key, tokens in (
                (cls.ALGORITHMS_CREDENTIALS_KEY, algorithm_tokens), (cls.LOOKUP_CREDENTIALS_KEY, lookup_tokens),
                (cls.LOGGED_CREDENTIALS_KEY, credentials.get(cls.LOGGED_CREDENTIALS_KEY)),
            ) if tokens
        }

    def flag_token(self, entry, credentials_key):
        """The entry without tokens; has_token where one is stored. OCL API lookups never show one."""
        entry = self.without_token(entry)
        url = self.get_endpoint_url(entry)
        tokens = (self.credentials or {}).get(credentials_key) or {}
        is_usable = bool(url) if credentials_key == self.ALGORITHMS_CREDENTIALS_KEY else not self.is_ocl_api_url(url)
        return {**entry, self.HAS_TOKEN_KEY: True} if url in tokens and is_usable else entry

    def get_algorithms_without_tokens(self, algorithms):
        """The API's view of algorithms: no tokens, and has_token where one is stored."""
        if not isinstance(algorithms, list):
            return drop_key_deep(algorithms, self.TOKEN_KEY)
        return [
            self.flag_token(entry, self.ALGORITHMS_CREDENTIALS_KEY) if isinstance(entry, dict) else
            drop_key_deep(entry, self.TOKEN_KEY) for entry in algorithms
        ]

    def get_lookup_config_without_token(self, lookup_config):
        """The API's view of lookup_config: no token, and has_token where one is stored."""
        if not isinstance(lookup_config, dict):
            return drop_key_deep(lookup_config, self.TOKEN_KEY)
        return self.flag_token(lookup_config, self.LOOKUP_CREDENTIALS_KEY)

    def clean_matches(self):
        if self.matches:
            try:
                self.matches = json.loads(self.matches)
            except (json.JSONDecodeError, TypeError):
                pass

    def clean_include_retired(self):
        if not self.include_retired:
            self.include_retired = False

    def clean_encoder_model(self):
        self.encoder_model = self.encoder_model.strip() if self.encoder_model else settings.ENCODER_MODEL_NAME

    def clean_filters(self):
        if not self.filters:
            self.filters = {}
        for key, value in self.filters.copy().items():
            if not value:
                self.filters.pop(key)

    @property
    def target_repo(self):
        if not self.target_repo_url:
            return None

        from core.sources.models import Source
        repo, _ = Source.resolve_reference_expression(self.target_repo_url)
        return repo if repo and repo.id else None

    @property
    def fields_mapped(self):
        return [
            col.get('label') for col in self.visible_columns if (
                    col['label'].lower() in [
                        'id', 'description', 'mapping: list', 'mapping: code',
                        'concept_class', 'class', 'datatype', 'name', 'synonyms'
                    ] or col['label'].lower().startswith('property:')
            )
        ] if self.columns else []


class AutomatchRun(BaseModel):
    """
    System-of-record for a single Mapper auto-match run — a child of MapProject.

    Tracks the lifecycle of one auto-match execution: how many input rows were
    intended, how many completed/failed, the project configuration captured at
    run start (``config_snapshot``), who/what triggered it, and the originating
    client. It is created at run start and PATCHed as rows complete (see
    OpenConceptLab/ocl_online#105 §A, #109).

    Re-run semantics (ocl_online#105 OQ3): re-running the failed rows of a run
    creates a *new* AutomatchRun with ``parent_run`` set and
    ``trigger_source='ui-rerun-row'``. The retry's ``intended_rows`` is the count
    of *failed* rows being re-run (not the original total) and its
    ``config_snapshot`` is the config at retry time. The parent run's
    ``failed_rows`` / ``completion_status`` are an immutable snapshot and are
    never mutated retrospectively; "all attempts" queries walk the
    ``parent_run`` chain.
    """
    OBJECT_TYPE = 'AutomatchRun'

    # completion_status vocabulary
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    PARTIAL = 'partial'
    COMPLETION_STATUSES = [RUNNING, COMPLETED, FAILED, CANCELLED, PARTIAL]
    # Statuses that close out a run; reaching one stamps completed_at.
    TERMINAL_STATUSES = [COMPLETED, FAILED, CANCELLED, PARTIAL]

    # trigger_source vocabulary
    TRIGGER_SOURCES = ['ui-auto-match', 'ui-rerun-row', 'api', 'cli', 'scheduled']

    map_project = models.ForeignKey(
        MapProject, on_delete=models.CASCADE, related_name='auto_match_runs')
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    intended_rows = models.IntegerField()  # set at run start from input
    completed_rows = models.IntegerField(default=0)  # updated as rows complete
    failed_rows = models.IntegerField(default=0)
    # algorithms, encoder, template, score_config, filters AT START
    config_snapshot = models.JSONField(default=dict)
    started_by = models.ForeignKey(
        'users.UserProfile', on_delete=models.SET_NULL, null=True, related_name='started_auto_match_runs')
    completion_status = models.CharField(max_length=16, default=RUNNING)
    trigger_source = models.CharField(max_length=32)
    # set when a re-run of failed rows spawns a new run
    parent_run = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='retry_runs')
    client_user_agent = models.TextField(null=True, blank=True)
    client_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = 'automatch_runs'
        indexes = [
            models.Index(fields=['map_project', '-started_at']),
            models.Index(fields=['completion_status']),
        ]

    def calculate_uri(self):
        return f"/auto-match-runs/{self.id or generate_temp_version()}/"

    # A run the client never closed, e.g. its tab closed before the create
    # response arrived. The Mapper PATCHes a run only when it ends, so this must
    # outlast the longest run.
    STALE_AFTER = timezone.timedelta(hours=24)

    @classmethod
    def expire_stale(cls, older_than=None):
        now = timezone.now()
        return cls.objects.filter(
            completion_status=cls.RUNNING, updated_at__lt=now - (older_than or cls.STALE_AFTER)
        ).update(completion_status=cls.CANCELLED, completed_at=now, updated_at=now)
