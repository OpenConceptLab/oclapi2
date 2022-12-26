from datetime import datetime
from json import JSONDecodeError

from billiard.exceptions import WorkerLostError
from celery.utils.log import get_task_logger
from celery_once import QueueOnce
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.management import call_command
from django.template.loader import render_to_string
from django.utils import timezone
from django_elasticsearch_dsl.registries import registry
from pydash import get

from core.celery import app
from core.common.constants import CONFIRM_EMAIL_ADDRESS_MAIL_SUBJECT, PASSWORD_RESET_MAIL_SUBJECT
from core.common.utils import write_export_file, web_url, get_resource_class_from_resource_name, get_export_service

logger = get_task_logger(__name__)


@app.task(base=QueueOnce)
def delete_organization(org_id):
    from core.orgs.models import Organization
    logger.info('Finding org...')

    org = Organization.objects.filter(id=org_id).first()

    if not org:
        logger.info('Not found org %s', org_id)
        return

    try:
        logger.info('Found org %s.  Beginning purge...', org.mnemonic)
        org.delete()
        logger.info('Purge complete!')
    except Exception as ex:
        logger.info('Org delete failed for %s with exception %s', org.mnemonic, ex.args)


@app.task(base=QueueOnce)
def delete_source(source_id):
    from core.sources.models import Source
    logger.info('Finding source...')

    source = Source.objects.filter(id=source_id).first()

    if not source:
        logger.info('Not found source %s', source_id)
        return None

    try:
        logger.info('Found source %s.  Beginning purge...', source.mnemonic)
        source.batch_delete(source.concepts_set)
        source.batch_delete(source.mappings_set)
        source.delete(force=True)
        logger.info('Delete complete!')
        return True
    except Exception as ex:
        logger.info('Source delete failed for %s with exception %s', source.mnemonic, ex.args)
        return ex


@app.task(base=QueueOnce)
def delete_collection(collection_id):
    from core.collections.models import Collection
    logger.info('Finding collection...')

    collection = Collection.objects.filter(id=collection_id).first()

    if not collection:
        logger.info('Not found collection %s', collection_id)
        return None

    try:
        logger.info('Found collection %s.  Beginning purge...', collection.mnemonic)
        collection.delete(force=True)
        logger.info('Delete complete!')
        return True
    except Exception as ex:
        logger.info('Collection delete failed for %s with exception %s', collection.mnemonic, ex.args)
        return ex


@app.task(base=QueueOnce, bind=True)
def export_source(self, version_id):
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
        write_export_file(version, 'source', 'core.sources.serializers.SourceVersionExportSerializer', logger)
        logger.info('Export complete!')
    finally:
        version.remove_processing(self.request.id)


@app.task(base=QueueOnce, bind=True)
def export_collection(self, version_id):
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
        version.expansion.wait_until_processed()
    try:
        logger.info('Found collection version %s.  Beginning export...', version.version)
        write_export_file(
            version, 'collection', 'core.collections.serializers.CollectionVersionExportSerializer', logger
        )
        logger.info('Export complete!')
    finally:
        version.remove_processing(self.request.id)


@app.task(bind=True)
def add_references(  # pylint: disable=too-many-arguments,too-many-locals
        self, user_id, data, collection_id, cascade=False, transform_to_resource_version=False
):
    from core.users.models import UserProfile
    from core.collections.models import Collection
    user = UserProfile.objects.get(id=user_id)
    collection = Collection.objects.get(id=collection_id)
    head = collection.get_head()
    head.add_processing(self.request.id)

    try:
        (added_references, errors) = collection.add_expressions(
            data, user, cascade, transform_to_resource_version)
    finally:
        head.remove_processing(self.request.id)

    for ref in added_references:
        if ref.concepts.exists():
            from core.concepts.models import Concept
            from core.concepts.documents import ConceptDocument
            Concept.batch_index(ref.concepts, ConceptDocument)
        if ref.mappings.exists():
            from core.mappings.models import Mapping
            from core.mappings.documents import MappingDocument
            Mapping.batch_index(ref.mappings, MappingDocument)
    if errors:
        logger.info('Errors while adding references....')
        logger.info(errors)

    return errors


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


@app.task(base=QueueOnce)
def populate_indexes(app_names=None):  # app_names has to be an iterable of strings
    __run_search_index_command('--populate', app_names)


@app.task(base=QueueOnce)
def rebuild_indexes(app_names=None):  # app_names has to be an iterable of strings
    __run_search_index_command('--rebuild', app_names)


def __run_search_index_command(command, app_names=None):
    if not command:
        return

    if app_names:
        call_command('search_index', f'{command}', '-f', '--models', *app_names, '--parallel')
    else:
        call_command('search_index', command, '-f', '--parallel')


@app.task(base=QueueOnce)
def bulk_import(to_import, username, update_if_exists):
    from core.importers.models import BulkImport
    return BulkImport(content=to_import, username=username, update_if_exists=update_if_exists).run()


@app.task(base=QueueOnce, bind=True)
def bulk_import_parallel_inline(self, to_import, username, update_if_exists, threads=5):
    from core.importers.models import BulkImportParallelRunner
    try:
        importer = BulkImportParallelRunner(
            content=to_import, username=username, update_if_exists=update_if_exists,
            parallel=threads, self_task_id=self.request.id
        )
    except JSONDecodeError as ex:
        return dict(error=f"Invalid JSON ({ex.msg})")
    except ValidationError as ex:
        return dict(error=f"Invalid Input ({ex.message})")
    return importer.run()


@app.task(base=QueueOnce)
def bulk_import_inline(to_import, username, update_if_exists):
    from core.importers.models import BulkImportInline
    return BulkImportInline(content=to_import, username=username, update_if_exists=update_if_exists).run()


@app.task(bind=True)
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
def seed_children_to_new_version(self, resource, obj_id, export=True, sync=False):
    instance = None
    export_task = None
    autoexpand = True
    is_source = resource == 'source'
    is_collection = resource == 'collection'

    if is_source:
        from core.sources.models import Source
        instance = Source.objects.filter(id=obj_id).first()
        export_task = export_source
    if is_collection:
        from core.collections.models import Collection
        instance = Collection.objects.filter(id=obj_id).first()
        export_task = export_collection
        autoexpand = instance.should_auto_expand

    if instance:
        task_id = self.request.id

        index = not export

        try:
            instance.add_processing(task_id)
            instance.seed_references()
            if is_source:
                instance.seed_concepts(index=index)
                instance.seed_mappings(index=index)
            elif autoexpand:
                instance.cascade_children_to_expansion(index=index, sync=sync)

            if export:
                export_task.delay(obj_id)
                if autoexpand:
                    instance.index_children()
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
def delete_duplicate_locales(start_from=None):  # pragma: no cover
    from core.concepts.models import Concept
    from django.db.models import Count
    from django.db.models import Q
    start_from = start_from or 0
    queryset = Concept.objects.annotate(
        names_count=Count('names'), desc_count=Count('descriptions')).filter(Q(names_count__gt=1) | Q(desc_count__gt=1))
    total = queryset.count()
    batch_size = 1000

    logger.info(f'{total:d} concepts with more than one locales. Getting them in batches of {batch_size:d}...')  # pylint: disable=logging-not-lazy,logging-fstring-interpolation

    for start in range(start_from, total, batch_size):
        end = min(start + batch_size, total)
        logger.info('Iterating concepts %d - %d...' % (start + 1, end))  # pylint: disable=logging-not-lazy,consider-using-f-string
        concepts = queryset.order_by('id')[start:end]
        for concept in concepts:
            logger.info('Cleaning up %s', concept.mnemonic)
            for name in concept.names.all().reverse():
                if concept.names.filter(
                        type=name.type, name=name.name, locale=name.locale, locale_preferred=name.locale_preferred,
                        external_id=name.external_id
                ).count() > 1:
                    name.delete()
            for desc in concept.descriptions.all().reverse():
                if concept.descriptions.filter(
                        type=desc.type, name=desc.name, locale=desc.locale, locale_preferred=desc.locale_preferred,
                        external_id=desc.external_id
                ).count() > 1:
                    desc.delete()


@app.task
def delete_dormant_locales():  # pragma: no cover
    from core.concepts.models import LocalizedText
    queryset = LocalizedText.get_dormant_queryset()
    total = queryset.count()
    logger.info('%s Dormant locales found. Deleting in batches...' % total)  # pylint: disable=logging-not-lazy,consider-using-f-string

    batch_size = 1000
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        logger.info('Iterating locales %d - %d to delete...' % (start + 1, end))  # pylint: disable=logging-not-lazy,consider-using-f-string
        LocalizedText.objects.filter(id__in=queryset.order_by('id')[start:end].values('id')).delete()

    return 1


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
    if model:
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
    acks_late=True, reject_on_worker_lost=True
)
def index_expansion_concepts(expansion_id):
    from core.collections.models import Expansion
    expansion = Expansion.objects.filter(id=expansion_id).first()
    if expansion:
        from core.concepts.documents import ConceptDocument
        expansion.batch_index(expansion.concepts, ConceptDocument)


@app.task(
    ignore_result=True, autoretry_for=(Exception, WorkerLostError, ), retry_kwargs={'max_retries': 2, 'countdown': 2},
    acks_late=True, reject_on_worker_lost=True
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
    acks_late=True, reject_on_worker_lost=True
)
def index_source_concepts(source_id):
    from core.sources.models import Source
    source = Source.objects.filter(id=source_id).first()
    if source:
        from core.concepts.documents import ConceptDocument
        source.batch_index(source.concepts, ConceptDocument)


@app.task(
    ignore_result=True, autoretry_for=(Exception, WorkerLostError, ), retry_kwargs={'max_retries': 2, 'countdown': 2},
    acks_late=True, reject_on_worker_lost=True
)
def index_source_mappings(source_id):
    from core.sources.models import Source
    source = Source.objects.filter(id=source_id).first()
    if source:
        from core.mappings.documents import MappingDocument
        source.batch_index(source.mappings, MappingDocument)


@app.task
def update_source_active_concepts_count(source_id):
    from core.sources.models import Source
    source = Source.objects.filter(id=source_id).first()
    if source:
        before_active_concepts = source.active_concepts
        source.set_active_concepts()
        if before_active_concepts != source.active_concepts:
            source.save(update_fields=['active_concepts'])


@app.task
def update_source_active_mappings_count(source_id):
    from core.sources.models import Source
    source = Source.objects.filter(id=source_id).first()
    if source:
        before_active_mappings = source.active_mappings
        source.set_active_mappings()
        if before_active_mappings != source.active_mappings:
            source.save(update_fields=['active_mappings'])


@app.task
def update_collection_active_concepts_count(collection_id):
    from core.collections.models import Collection
    collection = Collection.objects.filter(id=collection_id).first()
    if collection:
        before_active_concepts = collection.active_concepts
        collection.set_active_concepts()
        if before_active_concepts != collection.active_concepts:
            collection.save(update_fields=['active_concepts'])


@app.task
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
def link_references_to_resources(reference_ids):  # pragma: no cover
    from core.collections.models import CollectionReference
    for reference in CollectionReference.objects.filter(id__in=reference_ids):
        logger.info('Linking Reference %s', reference.uri)
        reference.link_resources()


@app.task(ignore_result=True)
def link_all_references_to_resources():  # pragma: no cover
    from core.collections.models import CollectionReference
    queryset = CollectionReference.objects.filter(concepts__isnull=True, mappings__isnull=True)
    total = queryset.count()
    logger.info('Need to link %d references', total)
    count = 1
    for reference in queryset:
        logger.info('(%d/%d) Linking Reference %s', count, total, reference.uri)
        count += 1
        reference.link_resources()


@app.task(ignore_result=True)
def link_expansions_repo_versions():  # pragma: no cover
    from core.collections.models import Expansion
    expansions = Expansion.objects.filter()
    total = expansions.count()
    logger.info('Total Expansions %d', total)
    count = 1
    for expansion in expansions:
        if (
                expansion.concepts.exists() or expansion.mappings.exists()
        ) and (
                not expansion.resolved_source_versions.exists() and not expansion.resolved_collection_versions.exists()
        ):
            logger.info('(%d/%d) Linking Repo Version %s', count, total, expansion.uri)
            expansion.link_repo_versions()
        else:
            logger.info('(%d/%d) Skipping already Linked %s', count, total, expansion.uri)
        count += 1


@app.task(ignore_result=True)
def reference_old_to_new_structure():  # pragma: no cover
    from core.collections.parsers import CollectionReferenceExpressionStringParser
    from core.collections.models import CollectionReference

    queryset = CollectionReference.objects.filter(expression__isnull=False, system__isnull=True, valueset__isnull=True)
    total = queryset.count()
    logger.info('Need to migrate %d references', total)
    count = 1
    for reference in queryset:
        logger.info('(%d/%d) Migrating %s', count, total, reference.uri)
        count += 1
        parser = CollectionReferenceExpressionStringParser(expression=reference.expression)
        parser.parse()
        ref_struct = parser.to_reference_structure()[0]
        reference.reference_type = ref_struct['reference_type'] or 'concepts'
        reference.system = ref_struct['system']
        reference.version = ref_struct['version']
        reference.code = ref_struct['code']
        reference.resource_version = ref_struct['resource_version']
        reference.valueset = ref_struct['valueset']
        reference.filter = ref_struct['filter']
        reference.save()


@app.task(ignore_result=True)
def beat_healthcheck():  # pragma: no cover
    from core.common.services import RedisService
    redis_service = RedisService()
    redis_service.set(settings.CELERYBEAT_HEALTHCHECK_KEY, str(datetime.now()), ex=120)


@app.task(ignore_result=True)
def monthly_usage_report():  # pragma: no cover
    # runs on first of every month
    # reports usage of prev month and trend over last 3 months
    from core.reports.models import MonthlyUsageReport
    now = timezone.now()
    three_months_from_now = now.replace(month=now.month-3, day=1)
    report = MonthlyUsageReport(verbose=True, start=three_months_from_now, end=now.replace(day=1))
    report.prepare()
    html_body = render_to_string('monthly_usage_report_for_mail.html', report.get_result_for_email())
    mail = EmailMessage(
        subject=f"{settings.ENV.upper()} Monthly usage report: {report.start} to {report.end}",
        body=html_body,
        to=[settings.REPORTS_EMAIL]
    )
    mail.content_subtype = "html"
    res = mail.send()
    return res
