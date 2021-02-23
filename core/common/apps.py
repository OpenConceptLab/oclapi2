from django.apps import AppConfig
from health_check.plugins import plugin_dir

from core.common.healthcheck import healthcheck


class CommonConfig(AppConfig):
    name = 'core.common'
    verbose_name = "Common"

    def ready(self):
        plugin_dir.register(healthcheck.ESHealthCheck)
        plugin_dir.register(healthcheck.FlowerHealthCheck)
        plugin_dir.register(healthcheck.CeleryDefaultQueueHealthCheck)
        plugin_dir.register(healthcheck.CeleryIndexingQueueHealthCheck)
        plugin_dir.register(healthcheck.CeleryConcurrentThreadsHealthCheck)
        plugin_dir.register(healthcheck.CeleryBulkImport0QueueHealthCheck)
        plugin_dir.register(healthcheck.CeleryBulkImport1QueueHealthCheck)
        plugin_dir.register(healthcheck.CeleryBulkImport2QueueHealthCheck)
        plugin_dir.register(healthcheck.CeleryBulkImport3QueueHealthCheck)
        plugin_dir.register(healthcheck.CeleryBulkImportRootQueueHealthCheck)

        from core.common import signals  # pylint: disable=unused-variable, unused-import
