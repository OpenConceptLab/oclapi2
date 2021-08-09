
from billiard.exceptions import WorkerLostError
from celery.utils.log import get_task_logger
from celery_once import QueueOnce
from django.apps import apps
from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management import call_command
from django.template.loader import render_to_string
from django_elasticsearch_dsl.registries import registry
from pydash import get

from core.celery import app
from core.common.constants import CONFIRM_EMAIL_ADDRESS_MAIL_SUBJECT, PASSWORD_RESET_MAIL_SUBJECT
from core.common.utils import write_export_file, web_url, get_resource_class_from_resource_name

logger = get_task_logger(__name__)


@app.task(base=QueueOnce)
def delete_organization(org_id):
    from core.orgs.models import Organization
    logger.info('Finding org...')

    org = Organization.objects.filter(id=org_id).first()

    if not org:  # pragma: no cover
        logger.info('Not found org %s', org_id)
        return

    try:
        logger.info('Found org %s.  Beginning purge...', org.mnemonic)
        org.delete()
        from core.pins.models import Pin
        Pin.objects.filter(resource_type__model='organization', resource_id=org.id).delete()
        from core.client_configs.models import ClientConfig
        ClientConfig.objects.filter(resource_type__model='organization', resource_id=org.id).delete()
        logger.info('Purge complete!')
    except Exception as ex:
        logger.info('Org delete failed for %s with exception %s', org.mnemonic, ex.args)


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
    try:
        logger.info('Found collection version %s.  Beginning export...', version.version)
        write_export_file(
            version, 'collection', 'core.collections.serializers.CollectionVersionExportSerializer', logger
        )
        logger.info('Export complete!')
    finally:
        version.remove_processing(self.request.id)


@app.task(bind=True)
def add_references(
        self, user, data, collection, host_url, cascade_mappings=False
):  # pylint: disable=too-many-arguments
    head = collection.get_head()
    head.add_processing(self.request.id)

    try:
        (added_references, errors) = collection.add_expressions(data, host_url, user, cascade_mappings)
    finally:
        head.remove_processing(self.request.id)

    for ref in added_references:
        if ref.concepts:
            for concept in ref.concepts:
                concept.index()
        if ref.mappings:
            for mapping in ref.mappings:
                mapping.index()

    return added_references, errors


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
        call_command('search_index', '%s' % command, '-f', '--models', *app_names, '--parallel')
    else:
        call_command('search_index', command, '-f', '--parallel')


@app.task(base=QueueOnce)
def bulk_import(to_import, username, update_if_exists):
    from core.importers.models import BulkImport
    return BulkImport(content=to_import, username=username, update_if_exists=update_if_exists).run()


@app.task(base=QueueOnce, bind=True)
def bulk_import_parallel_inline(self, to_import, username, update_if_exists, threads=5):
    from core.importers.models import BulkImportParallelRunner
    return BulkImportParallelRunner(
        content=to_import, username=username, update_if_exists=update_if_exists, parallel=threads,
        self_task_id=self.request.id
    ).run()


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
def seed_children(self, resource, obj_id, export=True):
    instance = None
    export_task = None

    if resource == 'source':
        from core.sources.models import Source
        instance = Source.objects.filter(id=obj_id).first()
        export_task = export_source
    if resource == 'collection':
        from core.collections.models import Collection
        instance = Collection.objects.filter(id=obj_id).first()
        export_task = export_collection

    if instance:
        task_id = self.request.id

        index = not export

        try:
            instance.add_processing(task_id)
            instance.seed_concepts(index=index)
            instance.seed_mappings(index=index)
            instance.seed_references()

            if export:
                export_task.delay(obj_id)
                instance.index_children()
        finally:
            instance.remove_processing(task_id)


@app.task
def import_v1_content(importer_class, file_url, drop_version_if_version_missing=False):  # pragma: no cover
    from core.v1_importers.models import V1BaseImporter
    klass = V1BaseImporter.get_importer_class_from_string(importer_class)
    if klass:
        return klass(file_url, drop_version_if_version_missing=drop_version_if_version_missing).run()

    return None


@app.task
def update_validation_schema(instance_type, instance_id, target_schema):
    klass = get_resource_class_from_resource_name(instance_type)
    instance = klass.objects.get(id=instance_id)
    instance.custom_validation_schema = target_schema
    errors = dict()

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

    logger.info('%d concepts with more than one locales. Getting them in batches of %d...' % (total, batch_size))  # pylint: disable=logging-not-lazy

    for start in range(start_from, total, batch_size):
        end = min(start + batch_size, total)
        logger.info('Iterating concepts %d - %d...' % (start + 1, end))  # pylint: disable=logging-not-lazy
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
    queryset = LocalizedText.objects.filter(name_locales__isnull=True, description_locales__isnull=True)
    total = queryset.count()
    logger.info('%s Dormant locales found. Deleting in batches...' % total)  # pylint: disable=logging-not-lazy

    batch_size = 1000
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        logger.info('Iterating locales %d - %d to delete...' % (start + 1, end))  # pylint: disable=logging-not-lazy
        LocalizedText.objects.filter(id__in=queryset.order_by('id')[start:end].values('id')).delete()

    return 1


@app.task
def delete_concept(concept_id):  # pragma: no cover
    from core.concepts.models import Concept

    concept = Concept.objects.filter(id=concept_id).first()
    concept.delete()

    return 1


@app.task
def batch_index_resources(resource, filters):
    model = get_resource_class_from_resource_name(resource)
    if model:
        model.batch_index(model.objects.filter(**filters), model.get_search_document())

    return 1
