from health_check.contrib.redis.backends import RedisHealthCheck
from health_check.db.backends import DatabaseBackend
from health_check.views import MainView

from core.common.healthcheck.healthcheck import FlowerHealthCheck, CeleryDefaultQueueHealthCheck, \
    CeleryBulkImport0QueueHealthCheck, CeleryBulkImportRootQueueHealthCheck, CeleryBulkImport3QueueHealthCheck, \
    CeleryBulkImport2QueueHealthCheck, CeleryBulkImport1QueueHealthCheck, CeleryConcurrentThreadsHealthCheck, \
    ESHealthCheck


class FlowerHealthcheckView(MainView):
    _plugins = [FlowerHealthCheck(critical_service=True)]


class ESHealthcheckView(MainView):
    _plugins = [ESHealthCheck(critical_service=True)]


class RedisHealthcheckView(MainView):
    _plugins = [RedisHealthCheck()]


class DBHealthcheckView(MainView):
    _plugins = [DatabaseBackend()]


class CeleryHealthCheckView(MainView):
    _plugins = [
        CeleryDefaultQueueHealthCheck(critical_service=True),
        CeleryConcurrentThreadsHealthCheck(),
        CeleryBulkImport0QueueHealthCheck(),
        CeleryBulkImport1QueueHealthCheck(),
        CeleryBulkImport2QueueHealthCheck(),
        CeleryBulkImport3QueueHealthCheck(),
        CeleryBulkImportRootQueueHealthCheck(),
    ]


class CeleryDefaultHealthCheckView(MainView):
    _plugins = [CeleryDefaultQueueHealthCheck(critical_service=True)]


class CeleryBulkImport0HealthCheckView(MainView):
    _plugins = [CeleryBulkImport0QueueHealthCheck(critical_service=True)]


class CeleryBulkImport1HealthCheckView(MainView):
    _plugins = [CeleryBulkImport1QueueHealthCheck(critical_service=True)]


class CeleryBulkImport2HealthCheckView(MainView):
    _plugins = [CeleryBulkImport2QueueHealthCheck(critical_service=True)]


class CeleryBulkImport3HealthCheckView(MainView):
    _plugins = [CeleryBulkImport3QueueHealthCheck(critical_service=True)]


class CeleryBulkImportRootHealthCheckView(MainView):
    _plugins = [CeleryBulkImportRootQueueHealthCheck(critical_service=True)]


class CeleryConcurrentThreadsHealthCheckView(MainView):
    _plugins = [CeleryConcurrentThreadsHealthCheck(critical_service=True)]
