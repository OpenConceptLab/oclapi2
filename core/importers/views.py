import urllib

from celery.result import AsyncResult
from celery_once import AlreadyQueued
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.celery import app
from core.common.services import RedisService
from core.common.swagger_parameters import update_if_exists_param, task_param, result_param, username_param, \
    file_upload_param, file_url_param, parallel_threads_param, verbose_param
from core.common.utils import parse_bulk_import_task_id, task_exists, flower_get, queue_bulk_import
from core.importers.constants import ALREADY_QUEUED, INVALID_UPDATE_IF_EXISTS, NO_CONTENT_TO_IMPORT


def import_response(request, import_queue, data, threads=None, inline=False):
    if not data:
        return Response(dict(exception=NO_CONTENT_TO_IMPORT), status=status.HTTP_400_BAD_REQUEST)

    user = request.user
    username = user.username
    update_if_exists = request.GET.get('update_if_exists', 'true')
    if update_if_exists not in ['true', 'false']:
        return Response(
            dict(exception=INVALID_UPDATE_IF_EXISTS),
            status=status.HTTP_400_BAD_REQUEST
        )
    update_if_exists = update_if_exists == 'true'

    data = data.decode('utf-8') if isinstance(data, bytes) else data

    try:
        task = queue_bulk_import(data, import_queue, username, update_if_exists, threads, inline)
    except AlreadyQueued:
        return Response(dict(exception=ALREADY_QUEUED), status=status.HTTP_409_CONFLICT)
    parsed_task = parse_bulk_import_task_id(task.id)
    return Response(
        dict(task=task.id, state=task.state, username=username, queue=parsed_task['queue']),
        status=status.HTTP_202_ACCEPTED
    )


class BulkImportFileUploadView(APIView):
    permission_classes = (IsAuthenticated, )
    parser_classes = (MultiPartParser, )

    @swagger_auto_schema(
        manual_parameters=[update_if_exists_param, file_upload_param],
    )
    def post(self, request, import_queue=None):
        file = request.data.get('file', None)

        if not file:
            return Response(dict(exception=NO_CONTENT_TO_IMPORT), status=status.HTTP_400_BAD_REQUEST)

        return import_response(self.request, import_queue, file.read())


class BulkImportFileURLView(APIView):
    permission_classes = (IsAuthenticated, )
    parser_classes = (MultiPartParser, )

    @swagger_auto_schema(
        manual_parameters=[update_if_exists_param, file_url_param],
    )
    def post(self, request, import_queue=None):
        file = None

        try:
            file = urllib.request.urlopen(request.data.get('file_url'))
        except:  # pylint: disable=bare-except
            pass

        if not file:
            return Response(dict(exception=NO_CONTENT_TO_IMPORT), status=status.HTTP_400_BAD_REQUEST)

        return import_response(self.request, import_queue, file.read())


class BulkImportView(APIView):
    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [IsAdminUser(), ]

        return [IsAuthenticated(), ]

    @swagger_auto_schema(
        manual_parameters=[update_if_exists_param],
        request_body=openapi.Schema(type=openapi.TYPE_OBJECT)
    )
    def post(self, request, import_queue=None):
        return import_response(self.request, import_queue, request.body)

    @swagger_auto_schema(manual_parameters=[task_param, result_param, username_param, verbose_param])
    def get(
            self, request, import_queue=None
    ):  # pylint: disable=too-many-return-statements,too-many-locals,too-many-branches
        task_id = request.GET.get('task')
        result_format = request.GET.get('result')
        username = request.GET.get('username')
        is_verbose = request.GET.get('verbose') in ['true', True]
        user = self.request.user

        if task_id:
            parsed_task = parse_bulk_import_task_id(task_id)
            username = parsed_task['username']

            if not user.is_staff and user.username != username:
                return Response(status=status.HTTP_403_FORBIDDEN)

            task = AsyncResult(task_id)

            if task.successful():
                result = task.get()
                if result and result_format == 'json':
                    return Response(result.get('json', None), content_type="application/json")
                if result and result_format == 'report':
                    return Response(result.get('report', None))
                if result:
                    return Response(result.get('detailed_summary', None))
            if task.failed():
                return Response(dict(exception=str(task.result)), status=status.HTTP_400_BAD_REQUEST)
            if task.state == 'STARTED':
                service = RedisService()
                if service.exists(task_id):
                    return Response(
                        dict(
                            details=service.get_formatted(task_id), task=task.id, state=task.state,
                            username=username, queue=parsed_task['queue']
                        ),
                        status=status.HTTP_200_OK
                    )
            if task.state == 'PENDING' and not task_exists(task_id):
                return Response(dict(exception='task ' + task_id + ' not found'), status=status.HTTP_404_NOT_FOUND)

            return Response(
                dict(task=task.id, state=task.state, username=username, queue=parsed_task['queue']),
                status=status.HTTP_202_ACCEPTED
            )

        try:
            flower_tasks = {
                **flower_get('api/tasks?taskname=core.common.tasks.bulk_import').json(),
                **flower_get('api/tasks?taskname=core.common.tasks.bulk_import_parallel_inline').json(),
                **flower_get('api/tasks?taskname=core.common.tasks.bulk_import_inline').json()
            }
        except Exception as ex:
            return Response(
                dict(detail='Flower service returned unexpected result. Maybe check healthcheck.', exception=str(ex)),
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

        tasks = []
        for task_id, value in flower_tasks.items():
            task = parse_bulk_import_task_id(task_id)

            if user.is_staff or user.username == task['username']:
                if (not import_queue or task['queue'] == import_queue) and \
                        (not username or task['username'] == username):
                    details = dict(task=task_id, state=value['state'], queue=task['queue'], username=task['username'])
                    if is_verbose:
                        details['details'] = value
                    tasks.append(details)

        return Response(tasks)

    @staticmethod
    @swagger_auto_schema(request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            'task_id': openapi.Schema(
                type=openapi.TYPE_STRING, description='Task Id to be terminated (mandatory)',
            ),
            'signal': openapi.Schema(
                type=openapi.TYPE_STRING, description='Kill Signal', default='SIGKILL',
            ),
        }
    ))
    def delete(request, import_queue=None):  # pylint: disable=unused-argument
        task_id = request.data.get('task_id', None)
        signal = request.data.get('signal', None) or 'SIGKILL'
        if not task_id:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        try:
            app.control.revoke(task_id, terminate=True, signal=signal)
        except Exception as ex:
            return Response(dict(errors=ex.args), status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)


class BulkImportParallelInlineView(APIView):  # pragma: no cover
    permission_classes = (IsAuthenticated, )
    parser_classes = (MultiPartParser, )

    @swagger_auto_schema(
        manual_parameters=[update_if_exists_param, file_url_param, file_upload_param, parallel_threads_param],
    )
    def post(self, request, import_queue=None):
        parallel_threads = request.data.get('parallel') or 5
        file = None
        try:
            if 'file' in request.data:
                file = request.data['file']
            elif 'file_url' in request.data:
                file = urllib.request.urlopen(request.data['file_url'])
        except:  # pylint: disable=bare-except
            pass

        if not file:
            return Response(dict(exception=NO_CONTENT_TO_IMPORT), status=status.HTTP_400_BAD_REQUEST)

        return import_response(self.request, import_queue, file.read(), parallel_threads, True)


class BulkImportInlineView(APIView):  # pragma: no cover
    permission_classes = (IsAuthenticated, )
    parser_classes = (MultiPartParser, )

    @swagger_auto_schema(
        manual_parameters=[update_if_exists_param, file_url_param, file_upload_param],
    )
    def post(self, request, import_queue=None):
        file = None
        try:
            if 'file' in request.data:
                file = request.data['file']
            elif 'file_url' in request.data:
                file = urllib.request.urlopen(request.data['file_url'])
        except:  # pylint: disable=bare-except
            pass

        if not file:
            return Response(dict(exception=NO_CONTENT_TO_IMPORT), status=status.HTTP_400_BAD_REQUEST)

        return import_response(self.request, import_queue, file.read(), None, True)
