from celery.app import default_app as celery_app
from django.conf import settings
from health_check.backends import BaseHealthCheckBackend
from health_check.contrib.celery_ping.backends import CeleryPingHealthCheck
from health_check.exceptions import ServiceReturnedUnexpectedResult, ServiceUnavailable
from pydash import get

from core.common.utils import flower_get, es_get


class FlowerHealthCheck(CeleryPingHealthCheck):
    critical_service = False

    def check_status(self):
        try:
            response = flower_get('metrics', timeout=1)
            if not response.ok:
                raise ServiceUnavailable('Flower Unavailable')
        except Exception as ex:
            raise ServiceUnavailable(ex.args)

    def identifier(self):
        return "Flower"


class ESHealthCheck(BaseHealthCheckBackend):
    critical_service = False

    def check_status(self):
        try:
            response = es_get('_cluster/health', timeout=1)
            status = get(response.json(), 'status')
            is_ok = status == 'green'

            if not is_ok:
                raise ServiceReturnedUnexpectedResult("Status {}".format(status))
        except Exception as ex:
            raise ServiceReturnedUnexpectedResult(ex.args)

    def identifier(self):
        return 'ElasticSearch'


class CeleryQueueHealthCheck(BaseHealthCheckBackend):
    critical_service = False

    CORRECT_PING_RESPONSE = {"ok": "pong"}

    def check_status(self):
        timeout = getattr(settings, "HEALTHCHECK_CELERY_PING_TIMEOUT", 1)

        try:
            ping_result = celery_app.control.ping(timeout=timeout)
        except IOError as ex:
            self.add_error(ServiceUnavailable("IOError"), ex)
        except NotImplementedError as exc:
            self.add_error(
                ServiceUnavailable(
                    "NotImplementedError: Make sure CELERY_RESULT_BACKEND is set"
                ),
                exc,
            )
        except BaseException as exc:
            self.add_error(ServiceUnavailable("Unknown error"), exc)
        else:
            if not ping_result:
                self.add_error(
                    ServiceUnavailable("Celery workers unavailable"),
                )
            else:
                self._check_ping_result(ping_result)

    def _check_ping_result(self, ping_result):
        active_workers = []

        for result in ping_result:
            worker, response = list(result.items())[0]
            if response != self.CORRECT_PING_RESPONSE:
                self.add_error(
                    ServiceUnavailable(
                        f"Celery worker {worker} response was incorrect"
                    ),
                )
                continue
            active_workers.append(worker)

        if not self.errors:
            self._check_active_queues(active_workers)

    def _check_active_queues(self, active_workers):
        defined_queues = self.queues

        if not defined_queues:
            return

        active_queues = set()

        for queues in celery_app.control.inspect(active_workers).active_queues().values():
            active_queues.update([queue.get("name") for queue in queues])

        for queue in defined_queues.difference(active_queues):
            self.add_error(
                ServiceUnavailable(f"No worker for Celery task queue {queue}"),
            )

    def identifier(self):  # Display name on the endpoint.
        if self.queues:
            return "celery@{}".format(list(self.queues)[0])

        return self.__class__.__name__


class CeleryDefaultQueueHealthCheck(CeleryQueueHealthCheck):
    queues = {'default'}


class CeleryConcurrentThreadsHealthCheck(CeleryQueueHealthCheck):
    queues = {'concurrent'}


class CeleryBulkImport0QueueHealthCheck(CeleryQueueHealthCheck):
    queues = {'bulk_import_0'}


class CeleryBulkImport1QueueHealthCheck(CeleryQueueHealthCheck):
    queues = {'bulk_import_1'}


class CeleryBulkImport2QueueHealthCheck(CeleryQueueHealthCheck):
    queues = {'bulk_import_2'}


class CeleryBulkImport3QueueHealthCheck(CeleryQueueHealthCheck):
    queues = {'bulk_import_3'}


class CeleryBulkImportRootQueueHealthCheck(CeleryQueueHealthCheck):
    queues = {'bulk_import_root'}
