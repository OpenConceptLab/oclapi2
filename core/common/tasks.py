from celery.utils.log import get_task_logger
from celery_once import QueueOnce
from django.apps import apps
from django.core.management import call_command
from django_elasticsearch_dsl.registries import registry

from core.celery import app
from core.common.utils import write_export_file

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
        write_export_file(version, 'source', 'core.sources.serializers.SourceDetailSerializer', logger)
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
        write_export_file(version, 'collection', 'core.collections.serializers.CollectionDetailSerializer', logger)
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

    return added_references, errors


def __handle_save(instance):
    registry.update(instance)
    registry.update_related(instance)


def __handle_pre_delete(instance):
    registry.delete_related(instance)


@app.task
def handle_save(app_name, model_name, instance_id):
    __handle_save(apps.get_model(app_name, model_name).objects.get(id=instance_id))


@app.task
def handle_m2m_changed(app_name, model_name, instance_id, action):
    instance = apps.get_model(app_name, model_name).objects.get(id=instance_id)
    if action in ('post_add', 'post_remove', 'post_clear'):
        __handle_save(instance)
    elif action in ('pre_remove', 'pre_clear'):
        __handle_pre_delete(instance)


@app.task
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


@app.task(base=QueueOnce)
def bulk_import_inline(to_import, username, update_if_exists):
    from core.importers.models import BulkImportInline
    return BulkImportInline(content=to_import, username=username, update_if_exists=update_if_exists).run()


@app.task(base=QueueOnce)
def bulk_priority_import(to_import, username, update_if_exists):
    from core.importers.models import BulkImport
    return BulkImport(content=to_import, username=username, update_if_exists=update_if_exists).run()
