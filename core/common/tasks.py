import json
import time
from datetime import datetime
from json import JSONDecodeError

from billiard.exceptions import WorkerLostError
from celery.utils.log import get_task_logger
from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.management import call_command
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.utils import timezone
from django_elasticsearch_dsl.registries import registry
from pydash import get

from core.celery import app
from core.common import ERRBIT_LOGGER
from core.common.constants import CONFIRM_EMAIL_ADDRESS_MAIL_SUBJECT, PASSWORD_RESET_MAIL_SUBJECT
from core.common.utils import write_export_file, web_url, get_resource_class_from_resource_name, get_export_service, \
    get_date_range_label
from core.reports.models import ResourceUsageReport
from core.tasks.models import QueueOnceCustomTask

logger = get_task_logger(__name__)


@app.task(base=QueueOnceCustomTask)
def delete_organization(org_id):
    from core.orgs.models import Organization
    logger.info('Finding org...')

    org = Organization.objects.filter(id=org_id).first()

    if not org:
        logger.info('Not found org %s', org_id)
        return

    try:
        logger.info('Found org %s.  Beginning purge...', org.mnemonic)
        org.delete(sync=True)
        logger.info('Purge complete!')
    except Exception as ex:
        logger.info('Org delete failed for %s with exception %s', org.mnemonic, ex.args)


@app.task(base=QueueOnceCustomTask)
def delete_source(source_id):
    from core.sources.models import Source
    logger.info('Finding source...')

    source = Source.objects.filter(id=source_id).first()

    if not source:
        logger.info('Not found source %s', source_id)
        return None

    try:
        logger.info('Found source %s', source.mnemonic)
        logger.info('Beginning concepts purge...')
        source.batch_delete(source.concepts_set)
        logger.info('Beginning mappings purge...')
        source.batch_delete(source.mappings_set)
        logger.info('Beginning versions and self purge...')
        source.delete(force=True)
        logger.info('Delete complete!')
        return True
    except Exception as ex:
        logger.info('Source delete failed for %s with exception %s', source.mnemonic, ex.args)
        ERRBIT_LOGGER.log(ex)
        return False


@app.task(base=QueueOnceCustomTask)
def delete_collection(collection_id):
    from core.collections.models import Collection
    logger.info('Finding collection...')

    collection = Collection.objects.filter(id=collection_id).first()

    if not collection:
        logger.info('Not found collection %s', collection_id)
        return None

    try:
        logger.info('Found collection %s.  Beginning purge...', collection.mnemonic)
        collection.references.all().delete()
        collection.expansions.all().delete()
        collection.delete(force=True)
        logger.info('Delete complete!')
        return True
    except Exception as ex:
        logger.info('Collection delete failed for %s with exception %s', collection.mnemonic, ex.args)
        ERRBIT_LOGGER.log(ex)
        return False


@app.task(base=QueueOnceCustomTask, bind=True)
def export_source(self, version_id):
    start_time = time.time()
    from core.sources.models import Source
    logger.info('Finding source version...')

    version = Source.objects.filter(id=version_id).select_related(
        'organization', 'user'
    ).first()

    if not version:  # pragma: no cover
        logger.info('Not found source version %s', version_id)
        return

    version.add_processing(self.request.id)
    try:
        logger.info('Found source version %s.  Beginning export...', version.version)
        write_export_file(
            version,
            'source', 'core.sources.serializers.SourceVersionExportSerializer',
            logger,
            start_time
        )
        logger.info('Export complete!')
    finally:
        version.remove_processing(self.request.id)


@app.task(base=QueueOnceCustomTask, bind=True)
def export_collection(self, version_id):
    start_time = time.time()
    from core.collections.models import Collection
    logger.info('Finding collection version...')

    version = Collection.objects.filter(id=version_id).select_related(
        'organization', 'user'
    ).first()

    if not version:  # pragma: no cover
        logger.info('Not found collection version %s', version_id)
        return

    version.add_processing(self.request.id)

    if version.expansion_uri:
        expansion = version.expansion
        if expansion:
            expansion.wait_until_processed()
    try:
        logger.info('Found collection version %s.  Beginning export...', version.version)
        write_export_file(
            version,
            'collection', 'core.collections.serializers.CollectionVersionExportSerializer',
            logger,
            start_time
        )
        logger.info('Export complete!')
    finally:
        version.remove_processing(self.request.id)


@app.task(bind=True)
def add_references(  # pylint: disable=too-many-arguments,too-many-locals
        self, user_id, data, collection_id, cascade=False, transform=False
):
    from core.users.models import UserProfile
    from core.collections.models import Collection
    user = UserProfile.objects.get(id=user_id)
    collection = Collection.objects.get(id=collection_id)
    head = collection.get_head()
    head.add_processing(self.request.id)

    try:
        (added_references, errors) = collection.add_expressions(
            data, user, cascade, transform, True)
    finally:
        head.remove_processing(self.request.id)
    if collection.expansion_uri:
        for ref in added_references:
            from core.concepts.documents import ConceptDocument
            from core.mappings.documents import MappingDocument
            collection.batch_index(ref.concepts, ConceptDocument)
            collection.batch_index(ref.mappings, MappingDocument)
    if errors:
        logger.info('Errors while adding references....')
        logger.info(errors)

    return [reference.id for reference in added_references], errors


def __handle_save(instance):
    if instance:
        registry.update(instance)
        registry.update_related(instance)


def __handle_pre_delete(instance):
    if instance:
        registry.delete_related(instance)


@app.task(
    ignore_result=True, autoretry_for=(Exception, WorkerLostError, ), retry_kwargs={'max_retries': 2, 'countdown': 2},
    acks_late=True, reject_on_worker_lost=True
)
def handle_save(app_name, model_name, instance_id):
    __handle_save(apps.get_model(app_name, model_name).objects.filter(id=instance_id).first())


@app.task(
    ignore_result=True, autoretry_for=(Exception, WorkerLostError, ), retry_kwargs={'max_retries': 2, 'countdown': 2},
    acks_late=True, reject_on_worker_lost=True
)
def handle_m2m_changed(app_name, model_name, instance_id, action):
    instance = apps.get_model(app_name, model_name).objects.filter(id=instance_id).first()
    if instance:
        if action in ('post_add', 'post_remove', 'post_clear'):
            __handle_save(instance)
        elif action in ('pre_remove', 'pre_clear'):
            __handle_pre_delete(instance)


@app.task(ignore_result=True)
def handle_pre_delete(app_name, model_name, instance_id):
    __handle_pre_delete(apps.get_model(app_name, model_name).objects.filter(id=instance_id).first())


@app.task(base=QueueOnceCustomTask)
def populate_indexes(app_names=None):  # app_names has to be an iterable of strings
    __run_search_index_command('--populate', app_names)


@app.task(base=QueueOnceCustomTask)
def rebuild_indexes(app_names=None):  # app_names has to be an iterable of strings
    __run_search_index_command('--rebuild', app_names)


def __run_search_index_command(command, app_names=None):
    if not command:
        return

    if app_names:
        call_command('search_index', f'{command}', '-f', '--models', *app_names, '--parallel')
    else:
        call_command('search_index', command, '-f', '--parallel')


@app.task(base=QueueOnceCustomTask, retry_kwargs={'max_retries': 0})
def bulk_import(to_import, username, update_if_exists):
    from core.importers.models import BulkImport
    return BulkImport(content=to_import, username=username, update_if_exists=update_if_exists).run()


@app.task(base=QueueOnceCustomTask, bind=True, retry_kwargs={'max_retries': 0})
def bulk_import_parallel_inline(self, to_import, username, update_if_exists, threads=5):
    from core.importers.models import BulkImportParallelRunner
    try:
        importer = BulkImportParallelRunner(
            content=to_import, username=username, update_if_exists=update_if_exists,
            parallel=threads, self_task_id=self.request.id
        )
    except JSONDecodeError as ex:
        return {'error': f"Invalid JSON ({ex.msg})"}
    except ValidationError as ex:
        return {'error': f"Invalid Input ({ex.message})"}
    return importer.run()


@app.task(base=QueueOnceCustomTask, retry_kwargs={'max_retries': 0})
def bulk_import_inline(to_import, username, update_if_exists):
    from core.importers.models import BulkImportInline
    return BulkImportInline(content=to_import, username=username, update_if_exists=update_if_exists).run()


# pylint: disable=too-many-arguments
@app.task(bind=True, base=QueueOnceCustomTask, retry_kwargs={'max_retries': 0})
def bulk_import_new(self, path, username, owner_type, owner, import_type='default'):
    from core.importers.importer import Importer
    task_id = self.request.id
    return Importer(task_id, path, username, owner_type, owner, import_type).run()


# pylint: disable=too-many-arguments
@app.task(retry_kwargs={'max_retries': 0}, compression='gzip')
def bulk_import_subtask(path, username, owner_type, owner, resource_type, files):
    from core.importers.importer import ImporterSubtask
    return ImporterSubtask(path, username, owner_type, owner, resource_type, files).run()


@app.task
def import_finisher(task_id):
    """Persist final import results so that they can be retrieved instantly"""
    from core.importers.importer import ImportTask
    from core.tasks.models import Task
    task = Task.objects.filter(id=task_id).first()
    if task:
        if task.json_result:
            import_task = ImportTask.import_task_from_json(task.json_result)
            if import_task:
                # Persist final results
                import_task.final_summary = import_task.summary
                import_task.time_finished = datetime.now()
                return import_task.model_dump(exclude={'summary'})

            task.json_result['time_finished'] = datetime.now()
            return task.json_result

    return {'time_finished': datetime.now()}


@app.task(bind=True, retry_kwargs={'max_retries': 0})
def bulk_import_parts_inline(self, input_list, username, update_if_exists):
    from core.importers.models import BulkImportInline
    return BulkImportInline(
        content=None, username=username, update_if_exists=update_if_exists, input_list=input_list,
        self_task_id=self.request.id
    ).run()


@app.task
def send_user_verification_email(user_id):
    from core.users.models import UserProfile
    user = UserProfile.objects.filter(id=user_id).first()
    if not user:
        return user

    html_body = render_to_string(
        'verification.html', {
            'user': user,
            'url': user.email_verification_url,
        }
    )
    mail = EmailMessage(subject=CONFIRM_EMAIL_ADDRESS_MAIL_SUBJECT, body=html_body, to=[user.email])
    mail.content_subtype = "html"
    res = mail.send()

    return mail if get(settings, 'TEST_MODE', False) else res


@app.task
def send_user_reset_password_email(user_id):
    from core.users.models import UserProfile
    user = UserProfile.objects.filter(id=user_id).first()
    if not user:
        return user

    html_body = render_to_string(
        'password_reset.html', {
            'user': user,
            'url': user.reset_password_url,
            'web_url': web_url(),
        }
    )
    mail = EmailMessage(subject=PASSWORD_RESET_MAIL_SUBJECT, body=html_body, to=[user.email])
    mail.content_subtype = "html"
    res = mail.send()

    return mail if get(settings, 'TEST_MODE', False) else res


@app.task(bind=True)
def seed_children_to_new_version(self, resource, obj_id, export=True, sync=False):  # pylint: disable=too-many-arguments,too-many-locals,too-many-branches
    export_task = None
    autoexpand = True

    is_source = resource == 'source'
    is_collection = resource == 'collection'

    klass = get_resource_class_from_resource_name(resource)
    instance = klass.objects.filter(id=obj_id).first()

    if is_source:
        export_task = export_source
    elif is_collection:
        export_task = export_collection
        autoexpand = instance.should_auto_expand

    if instance:  # pylint: disable=too-many-nested-blocks
        task_id = self.request.id
        try:
            instance.add_processing(task_id)
            instance.seed_references()
            if is_source:
                instance.seed_concepts(index=False)
                instance.seed_mappings(index=False)
                instance.update_children_counts(sync)
                if instance.released:
                    instance.index_resources_for_self_as_latest_released()
                else:
                    instance.index_children(sync=False, user=instance.created_by)
            elif autoexpand:
                instance.cascade_children_to_expansion(index=True, sync=sync)
                instance.update_children_counts(sync)

            if export:
                from core.tasks.models import Task
                task = Task.new(queue='default', username=instance.updated_by, name=export_task.__name__)
                export_task.apply_async((obj_id,), queue=task.queue, task_id=task.id, persist_args=True)
        finally:
            instance.remove_processing(task_id)


@app.task
def seed_children_to_expansion(expansion_id, index=True):
    from core.collections.models import Expansion
    expansion = Expansion.objects.filter(id=expansion_id).first()
    if expansion:
        expansion.seed_children(index=index)
        if expansion.is_processing:
            expansion.is_processing = False
            expansion.save()


@app.task
def update_validation_schema(instance_type, instance_id, target_schema):
    klass = get_resource_class_from_resource_name(instance_type)
    instance = klass.objects.get(id=instance_id)
    instance.custom_validation_schema = target_schema
    errors = {}

    failed_concept_validations = instance.validate_child_concepts() or []
    if failed_concept_validations:
        errors.update({'failed_concept_validations': failed_concept_validations})

    if errors:
        return errors

    instance.save()

    return None


@app.task(
    ignore_result=True, autoretry_for=(Exception, WorkerLostError, ), retry_kwargs={'max_retries': 2, 'countdown': 2},
    acks_late=True, reject_on_worker_lost=True
)
def process_hierarchy_for_new_concept(concept_id, initial_version_id, parent_concept_uris, create_parent_version=True):
    """
      Executed when a new concept is created with parent_concept_urls and does following:
      1. Associates parent concepts to the concept and concept latest (initial) version
      2. Creates new versions for parent concept (if asked)
    """
    from core.concepts.models import Concept
    concept = Concept.objects.filter(id=concept_id).first()

    initial_version = None
    if initial_version_id:
        initial_version = Concept.objects.filter(id=initial_version_id).first()

    parent_concepts = Concept.objects.filter(uri__in=parent_concept_uris)
    concept._parent_concepts = parent_concepts  # pylint: disable=protected-access
    concept.set_parent_concepts_from_uris(create_parent_version=create_parent_version)

    if initial_version:
        initial_version._parent_concepts = parent_concepts  # pylint: disable=protected-access
        initial_version.set_parent_concepts_from_uris(create_parent_version=False)


@app.task(
    ignore_result=True, autoretry_for=(Exception, WorkerLostError, ), retry_kwargs={'max_retries': 2, 'countdown': 2},
    acks_late=True, reject_on_worker_lost=True
)
def process_hierarchy_for_concept_version(
        latest_version_id, prev_version_id, parent_concept_uris, create_parent_version):
    """
      Executed when a new concept version is created with new, updated or existing hierarchy
      1. Associates parent concepts to the latest concept version.
      2. Creates new versions for removed parent concepts from previous versions.
      3. Creates new versions for parent concept (if asked)
    """
    from core.concepts.models import Concept
    latest_version = Concept.objects.filter(id=latest_version_id).first()

    prev_version = None
    old_parents = None
    if prev_version_id:
        prev_version = Concept.objects.filter(id=prev_version_id).first()
        old_parents = prev_version.parent_concept_urls

    parent_concepts = Concept.objects.filter(
        uri__in=parent_concept_uris) if parent_concept_uris else Concept.objects.none()
    latest_version._parent_concepts = parent_concepts  # pylint: disable=protected-access
    latest_version.set_parent_concepts_from_uris(create_parent_version)
    latest_version.versioned_object.parent_concepts.set(latest_version.parent_concepts.all())

    if prev_version:
        removed_parent_urls = [
            url for url in old_parents if url not in list(latest_version.parent_concept_urls)
        ]
        latest_version.create_new_versions_for_removed_parents(removed_parent_urls)


@app.task(
    ignore_result=True, autoretry_for=(Exception, WorkerLostError, ), retry_kwargs={'max_retries': 2, 'countdown': 2},
    acks_late=True, reject_on_worker_lost=True
)
def process_hierarchy_for_new_parent_concept_version(prev_version_id, latest_version_id):
    """
      Associates latest parent version to child concepts
    """
    from core.concepts.models import Concept
    prev_version = Concept.objects.filter(id=prev_version_id).first()
    latest_version = Concept.objects.filter(id=latest_version_id).first()
    if prev_version and latest_version:
        for concept in Concept.objects.filter(parent_concepts__uri=prev_version.uri):
            concept.parent_concepts.add(latest_version)


@app.task
def delete_concept(concept_id):  # pragma: no cover
    from core.concepts.models import Concept

    queryset = Concept.objects.filter(id=concept_id)
    concept = queryset.first()
    if concept:
        parent = concept.parent
        concept.delete()
        parent.update_concepts_count()

    return 1


@app.task(
    ignore_result=True, autoretry_for=(Exception, WorkerLostError, ), retry_kwargs={'max_retries': 2, 'countdown': 2},
    acks_late=True, reject_on_worker_lost=True
)
def batch_index_resources(resource, filters, update_indexed=False):
    model = get_resource_class_from_resource_name(resource)
    if isinstance(filters, str):
        filters = json.loads(filters)
    if model and filters is not None:
        queryset = model.objects.filter(**filters)
        model.batch_index(queryset, model.get_search_document())

        from core.concepts.models import Concept
        from core.mappings.models import Mapping
        if model in [Concept, Mapping]:
            if update_indexed:
                queryset.update(_index=True)

    return 1


@app.task(
    ignore_result=True, autoretry_for=(Exception, WorkerLostError, ), retry_kwargs={'max_retries': 2, 'countdown': 2},
    acks_late=True, reject_on_worker_lost=True, base=QueueOnceCustomTask
)
def index_expansion_concepts(expansion_id):
    from core.collections.models import Expansion
    expansion = Expansion.objects.filter(id=expansion_id).first()
    if expansion:
        from core.concepts.documents import ConceptDocument
        expansion.batch_index(expansion.concepts, ConceptDocument)


@app.task(
    ignore_result=True, autoretry_for=(Exception, WorkerLostError, ), retry_kwargs={'max_retries': 2, 'countdown': 2},
    acks_late=True, reject_on_worker_lost=True, base=QueueOnceCustomTask
)
def index_expansion_mappings(expansion_id):
    from core.collections.models import Expansion
    expansion = Expansion.objects.filter(id=expansion_id).first()
    if expansion:
        from core.mappings.documents import MappingDocument
        expansion.batch_index(expansion.mappings, MappingDocument)


@app.task
def make_hierarchy(concept_map):  # pragma: no cover
    from core.concepts.models import Concept

    for parent_concept_uri, child_concept_urls in concept_map.items():
        parent_concept = Concept.objects.filter(uri=parent_concept_uri).first()
        if parent_concept:
            parent_latest = parent_concept.get_latest_version()
            if parent_latest:
                for child_concept in Concept.objects.filter(uri__in=child_concept_urls):
                    child_concept.parent_concepts.add(parent_latest)
                    child_latest = child_concept.get_latest_version()
                    if child_latest:
                        child_latest.parent_concepts.add(parent_latest)
                        logger.info('Added child %s to parent %s', child_concept.uri, parent_concept_uri)
                    else:
                        logger.info('Could not find child %s latest_version', child_concept.uri)
            else:
                logger.info('Could not find parent %s latest_version', parent_concept_uri)
        else:
            logger.info('Could not find parent %s', parent_concept_uri)


@app.task(
    ignore_result=True, autoretry_for=(Exception, WorkerLostError, ), retry_kwargs={'max_retries': 2, 'countdown': 2},
    acks_late=True, reject_on_worker_lost=True, base=QueueOnceCustomTask
)
def index_source_concepts(source_id):
    from core.sources.models import Source
    source = Source.objects.filter(id=source_id).first()
    if source:
        from core.concepts.documents import ConceptDocument
        source.batch_index(source.concepts, ConceptDocument)


@app.task(
    ignore_result=True, autoretry_for=(Exception, WorkerLostError, ), retry_kwargs={'max_retries': 2, 'countdown': 2},
    acks_late=True, reject_on_worker_lost=True, base=QueueOnceCustomTask
)
def index_source_mappings(source_id):
    from core.sources.models import Source
    source = Source.objects.filter(id=source_id).first()
    if source:
        from core.mappings.documents import MappingDocument
        source.batch_index(source.mappings, MappingDocument)


@app.task(base=QueueOnceCustomTask)
def update_source_active_concepts_count(source_id):
    from core.sources.models import Source
    source = Source.objects.filter(id=source_id).first()
    if source:
        before_active_concepts = source.active_concepts
        source.set_active_concepts()
        if before_active_concepts != source.active_concepts:
            source.save(update_fields=['active_concepts'])


@app.task(base=QueueOnceCustomTask)
def update_source_active_mappings_count(source_id):
    from core.sources.models import Source
    source = Source.objects.filter(id=source_id).first()
    if source:
        before_active_mappings = source.active_mappings
        source.set_active_mappings()
        if before_active_mappings != source.active_mappings:
            source.save(update_fields=['active_mappings'])


@app.task(base=QueueOnceCustomTask)
def update_collection_active_concepts_count(collection_id):
    from core.collections.models import Collection
    collection = Collection.objects.filter(id=collection_id).first()
    if collection:
        before_active_concepts = collection.active_concepts
        collection.set_active_concepts()
        if before_active_concepts != collection.active_concepts:
            collection.save(update_fields=['active_concepts'])


@app.task(base=QueueOnceCustomTask)
def update_collection_active_mappings_count(collection_id):
    from core.collections.models import Collection
    collection = Collection.objects.filter(id=collection_id).first()
    if collection:
        before_active_mappings = collection.active_mappings
        collection.set_active_mappings()
        if before_active_mappings != collection.active_mappings:
            collection.save(update_fields=['active_mappings'])


@app.task
def delete_s3_objects(path):
    if path:
        get_export_service().delete_objects(path)


@app.task(ignore_result=True)
def beat_healthcheck():  # pragma: no cover
    from core.services.storages.redis import RedisService
    redis_service = RedisService()
    redis_service.set(settings.CELERYBEAT_HEALTHCHECK_KEY, str(datetime.now()), ex=120)


@app.task()
def resources_report(start_date=None, end_date=None, email=None):  # pragma: no cover
    # runs on first of every month
    # reports usage of prev month
    now = timezone.now().replace(day=1)
    start_date = start_date or now - relativedelta(months=1)
    end_date = end_date or now
    report = ResourceUsageReport(start_date=start_date, end_date=end_date)
    buff, file_name = report.generate()
    date_range_label = get_date_range_label(report.start_date, report.end_date)
    env = settings.ENV.upper()
    reports_email = email or settings.REPORTS_EMAIL
    mail = EmailMessage(
        subject=f"{env} Monthly Resources Report: {date_range_label}",
        body=f"Please find attached resources report of {env} for the period of {date_range_label}",
        to=[reports_email]
    )
    mail.attach(file_name, buff.getvalue(), 'text/csv')
    result = mail.send()
    logger.info('Resources report sent to %s, result: %s', reports_email, result)
    return result


@app.task(ignore_result=True)
def vacuum_and_analyze_db():
    from django.db import connections
    conn_proxy = connections['default']
    conn_proxy.cursor()  # init connection field
    conn = conn_proxy.connection
    old_isolation_level = conn.isolation_level
    conn.set_isolation_level(0)
    conn.cursor().execute('VACUUM ANALYZE')
    conn.set_isolation_level(old_isolation_level)


@app.task(ignore_result=True)
def post_import_update_resource_counts():
    from core.sources.models import Source
    from core.concepts.models import Concept
    from core.mappings.models import Mapping

    uncounted_concepts = Concept.objects.filter(_counted__isnull=True)
    sources = Source.objects.filter(id__in=uncounted_concepts.values_list('parent_id', flat=True))
    for source in sources:
        source.update_concepts_count(sync=True)
        try:
            uncounted_concepts.filter(parent_id=source.id).update(_counted=True)
        except:  # pylint: disable=bare-except
            pass

    uncounted_mappings = Mapping.objects.filter(_counted__isnull=True)
    sources = Source.objects.filter(
        id__in=uncounted_mappings.values_list('parent_id', flat=True))

    for source in sources:
        source.update_mappings_count(sync=True)
        try:
            uncounted_mappings.filter(parent_id=source.id).update(_counted=True)
        except:  # pylint: disable=bare-except
            pass


@app.task(ignore_result=True)
def update_mappings_source(source_id):
    # Updates mappings where mapping.to_source_url or mapping.from_source_url matches source url or canonical url
    from core.sources.models import Source
    source = Source.objects.filter(id=source_id).first()
    if source:
        source.update_mappings()


@app.task(ignore_result=True)
def update_mappings_concept(concept_id):
    # Updates mappings where mapping.to_concept or mapping.from_concepts matches concept's mnemonic and parent
    from core.concepts.models import Concept
    concept = Concept.objects.filter(id=concept_id).first()
    if concept:
        concept.update_mappings()


@app.task(ignore_result=True)
def calculate_checksums(resource_type, resource_id):
    model = get_resource_class_from_resource_name(resource_type)
    if model:
        is_source_child = model.__name__ in ('Concept', 'Mapping')
        instance = model.objects.filter(id=resource_id).first()
        if instance:
            instance.set_checksums()
            if is_source_child:
                if not instance.is_latest_version:
                    instance.get_latest_version().set_checksums()
                if not instance.is_versioned_object:
                    instance.versioned_object.set_checksums()


@app.task(ignore_result=True)
def generate_source_resources_checksums(repo_id, only_latest=False):  # pragma: no cover
    from core.sources.models import Source
    repo = Source.objects.filter(id=repo_id).first()

    def set_checksums(queryset):
        paginator = Paginator(queryset.order_by('-id'), 500)
        for page_number in paginator.page_range:
            page = paginator.page(page_number)
            for resource in page.object_list:
                resource.set_checksums()

    if repo.is_head and only_latest:
        from core.concepts.models import Concept
        from core.mappings.models import Mapping
        set_checksums(Concept.objects.filter(is_latest_version=True, parent=repo))
        set_checksums(Mapping.objects.filter(is_latest_version=True, parent=repo))
    else:
        set_checksums(repo.get_concepts_queryset())
        set_checksums(repo.get_mappings_queryset())


@app.task(ignore_result=True)
def readd_references_to_expansion_on_references_removal(expansion_id, removed_reference_ids):
    from core.collections.models import Expansion
    expansion = Expansion.objects.filter(id=expansion_id).first()
    if expansion and removed_reference_ids:
        reference_to_readd = expansion.collection_version.references.exclude(id__in=removed_reference_ids)
        expansion.add_references(reference_to_readd, True, True, False)


@app.task(ignore_result=True)
def resolve_url_registry_entries(repo_id, repo_type):
    repo_klass = get_resource_class_from_resource_name(repo_type)
    if repo_klass:
        repo = repo_klass.objects.filter(id=repo_id).first()
        for entry in repo.active_url_registry_entries:
            entry.lookup_entry()


@app.task(ignore_result=True)
def expire_old_celery_tasks():
    from core.tasks.models import Task
    Task.objects.filter(updated_at__lt=timezone.now() - timezone.timedelta(days=7)).delete()


@app.task
def source_version_compare(version1_uri, version2_uri, is_changelog, verbosity):
    from core.sources.models import Source
    version1 = Source.objects.get(uri=version1_uri)
    version2 = Source.objects.get(uri=version2_uri)
    fn = Source.changelog if is_changelog else Source.compare
    return fn(version1, version2, verbosity)
