
from billiard.exceptions import WorkerLostError
from celery.utils.log import get_task_logger
from celery_once import QueueOnce
from django.apps import apps
from django.core.mail import EmailMessage
from django.core.management import call_command
from django.template.loader import render_to_string
from django_elasticsearch_dsl.registries import registry

from core.celery import app
from core.common.constants import CONFIRM_EMAIL_ADDRESS_MAIL_SUBJECT, PASSWORD_RESET_MAIL_SUBJECT
from core.common.utils import write_export_file, web_url

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
        logger.info('Purge complete!')
    except Exception as ex:
        logger.info('Org delete failed for %s with exception %s', org.mnemonic, ex.args)


@app.task(base=QueueOnce, bind=True)
def export_source(self, version_id):
    from core.sources.models import Source
    logger.info('Finding source version...')

    version = Source.objects.filter(id=version_id).first()

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

    version = Collection.objects.filter(id=version_id).first()

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
        (added_references, errors) = collection.add_expressions(
            data, host_url, user, cascade_mappings
        )
    finally:
        head.remove_processing(self.request.id)

    for ref in added_references:
        if ref.concepts:
            for concept in ref.concepts:
                concept.save()
        if ref.mappings:
            for mapping in ref.mappings:
                mapping.save()

    return added_references, errors


def __handle_save(instance):
    registry.update(instance)
    registry.update_related(instance)


def __handle_pre_delete(instance):
    registry.delete_related(instance)


@app.task(
    ignore_result=True, autoretry_for=(Exception, WorkerLostError, ), retry_kwargs={'max_retries': 2, 'countdown': 2},
    acks_late=True, reject_on_worker_lost=True
)
def handle_save(app_name, model_name, instance_id):
    __handle_save(apps.get_model(app_name, model_name).objects.get(id=instance_id))


@app.task(
    ignore_result=True, autoretry_for=(Exception, WorkerLostError, ), retry_kwargs={'max_retries': 2, 'countdown': 2},
    acks_late=True, reject_on_worker_lost=True
)
def handle_m2m_changed(app_name, model_name, instance_id, action):
    instance = apps.get_model(app_name, model_name).objects.get(id=instance_id)
    if action in ('post_add', 'post_remove', 'post_clear'):
        __handle_save(instance)
    elif action in ('pre_remove', 'pre_clear'):
        __handle_pre_delete(instance)


@app.task(ignore_result=True)
def handle_pre_delete(app_name, model_name, instance_id):
    __handle_pre_delete(apps.get_model(app_name, model_name).objects.get(id=instance_id))


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


@app.task(base=QueueOnce)
def bulk_priority_import(to_import, username, update_if_exists):
    from core.importers.models import BulkImport
    return BulkImport(content=to_import, username=username, update_if_exists=update_if_exists).run()


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
    return res


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
    return res


@app.task(bind=True)
def seed_children(self, resource, obj_id):
    instance = None
    if resource == 'source':
        from core.sources.models import Source
        instance = Source.objects.filter(id=obj_id).first()
    if resource == 'collection':
        from core.collections.models import Collection
        instance = Collection.objects.filter(id=obj_id).first()
    if instance:
        task_id = self.request.id

        try:
            instance.add_processing(task_id)
            instance.seed_concepts()
            instance.seed_mappings()
            instance.seed_references()
        finally:
            instance.remove_processing(task_id)
