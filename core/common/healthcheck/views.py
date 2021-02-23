from health_check.contrib.redis.backends import RedisHealthCheck
from health_check.db.backends import DatabaseBackend
from health_check.views import MainView

from core.common.healthcheck.healthcheck import FlowerHealthCheck, CeleryDefaultQueueHealthCheck, \
    CeleryBulkImport0QueueHealthCheck, CeleryBulkImportRootQueueHealthCheck, CeleryBulkImport3QueueHealthCheck, \
    CeleryBulkImport2QueueHealthCheck, CeleryBulkImport1QueueHealthCheck, CeleryConcurrentThreadsHealthCheck, \
    ESHealthCheck, CeleryIndexingQueueHealthCheck


class BaseHealthcheckView(MainView):
    swagger_schema = None


class CriticalHealthcheckView(BaseHealthcheckView):
    _plugins = [
        DatabaseBackend(),
        RedisHealthCheck(),
    ]


class FlowerHealthcheckView(BaseHealthcheckView):
    _plugins = [FlowerHealthCheck(critical_service=True)]


class ESHealthcheckView(BaseHealthcheckView):
    _plugins = [ESHealthCheck(critical_service=True)]


class RedisHealthcheckView(BaseHealthcheckView):
    _plugins = [RedisHealthCheck()]


class DBHealthcheckView(BaseHealthcheckView):
    _plugins = [DatabaseBackend()]


class CeleryHealthCheckView(BaseHealthcheckView):
    _plugins = [
        CeleryDefaultQueueHealthCheck(critical_service=True),
        CeleryIndexingQueueHealthCheck(critical_service=True),
        CeleryConcurrentThreadsHealthCheck(),
        CeleryBulkImport0QueueHealthCheck(),
        CeleryBulkImport1QueueHealthCheck(),
        CeleryBulkImport2QueueHealthCheck(),
        CeleryBulkImport3QueueHealthCheck(),
        CeleryBulkImportRootQueueHealthCheck(),
    ]


class CeleryDefaultHealthCheckView(BaseHealthcheckView):
    _plugins = [CeleryDefaultQueueHealthCheck(critical_service=True)]


class CeleryIndexingHealthCheckView(BaseHealthcheckView):
    _plugins = [CeleryIndexingQueueHealthCheck(critical_service=True)]


class CeleryBulkImport0HealthCheckView(BaseHealthcheckView):
    _plugins = [CeleryBulkImport0QueueHealthCheck(critical_service=True)]


class CeleryBulkImport1HealthCheckView(BaseHealthcheckView):
    _plugins = [CeleryBulkImport1QueueHealthCheck(critical_service=True)]


class CeleryBulkImport2HealthCheckView(BaseHealthcheckView):
    _plugins = [CeleryBulkImport2QueueHealthCheck(critical_service=True)]


class CeleryBulkImport3HealthCheckView(BaseHealthcheckView):
    _plugins = [CeleryBulkImport3QueueHealthCheck(critical_service=True)]


class CeleryBulkImportRootHealthCheckView(BaseHealthcheckView):
    _plugins = [CeleryBulkImportRootQueueHealthCheck(critical_service=True)]


class CeleryConcurrentThreadsHealthCheckView(BaseHealthcheckView):
    _plugins = [CeleryConcurrentThreadsHealthCheck(critical_service=True)]
