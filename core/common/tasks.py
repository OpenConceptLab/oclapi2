from celery.utils.log import get_task_logger
from celery_once import QueueOnce

from core.celery import app
from core.common.utils import write_export_file

logger = get_task_logger(__name__)


@app.task(base=QueueOnce, bind=True)
def export_source(self, version_id):
    from core.sources.models import Source
    logger.info('Finding source version...')

    version = Source.objects.filter(id=version_id).first()

    if not version:
        logger.info('Not found source version %s', version_id)

    version.add_processing(self.request.id)
    try:
        logger.info('Found source version %s.  Beginning export...', version.version)
        write_export_file(version, 'source', 'sources.serializers.SourceDetailSerializer', logger)
        logger.info('Export complete!')
    finally:
        version.remove_processing(self.request.id)


@app.task(base=QueueOnce, bind=True)
def export_collection(self, version_id):
    from core.collections.models import Collection
    logger.info('Finding collection version...')

    version = Collection.objects.filter(id=version_id).first()

    if not version:
        logger.info('Not found collection version %s', version_id)

    version.add_processing(self.request.id)
    try:
        logger.info('Found collection version %s.  Beginning export...', version.version)
        write_export_file(version, 'collection', 'collection.serializers.CollectionDetailSerializer', logger)
        logger.info('Export complete!')
    finally:
        version.remove_processing(self.request.id)


@app.task(bind=True)
def add_references(
        self, user, data, collection, host_url, cascade_mappings=False
):  # pylint: disable=too-many-arguments
    head = collection.get_head()
    head.add_processing(self.request.id)

    (added_references, errors) = collection.add_expressions(
        data, host_url, user, cascade_mappings
    )

    head.remove_processing(self.request.id)

    return added_references, errors
