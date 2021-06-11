import csv
import io

import requests
from celery.result import AsyncResult
from celery_once import AlreadyQueued, QueueOnce
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from ocldev.oclcsvtojsonconverter import OclStandardCsvToJsonConverter
from pydash import get
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.celery import app
from core.common.services import RedisService
from core.common.swagger_parameters import update_if_exists_param, task_param, result_param, username_param, \
    file_upload_param, file_url_param, parallel_threads_param, verbose_param
from core.common.utils import parse_bulk_import_task_id, task_exists, flower_get, queue_bulk_import, \
    get_bulk_import_celery_once_lock_key, is_csv_file
from core.importers.constants import ALREADY_QUEUED, INVALID_UPDATE_IF_EXISTS, NO_CONTENT_TO_IMPORT


def csv_file_data_to_input_list(file_content):
    return [row for row in csv.DictReader(io.StringIO(file_content))]  # pylint: disable=unnecessary-comprehension


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

        if is_csv_file(name=file.name):
            data = OclStandardCsvToJsonConverter(
                input_list=csv_file_data_to_input_list(file.read().decode('utf-8')),
                allow_special_characters=True
            ).process()
        else:
            data = file.read()

        return import_response(self.request, import_queue, data)


class BulkImportFileURLView(APIView):
    permission_classes = (IsAuthenticated, )
    parser_classes = (MultiPartParser, )

    @swagger_auto_schema(
        manual_parameters=[update_if_exists_param, file_url_param],
    )
    def post(self, request, import_queue=None):
        file = None
        file_url = request.data.get('file_url')

        try:
            file = requests.get(file_url)
        except:  # pylint: disable=bare-except
            pass

        if not file:
            return Response(dict(exception=NO_CONTENT_TO_IMPORT), status=status.HTTP_400_BAD_REQUEST)

        if is_csv_file(name=file_url):
            data = OclStandardCsvToJsonConverter(
                input_list=csv_file_data_to_input_list(file.text), allow_special_characters=True).process()
        else:
            data = file.text

        return import_response(self.request, import_queue, data)


class BulkImportView(APIView):
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
                    if value['state'] in ['RECEIVED', 'PENDING']:
                        result = AsyncResult(task_id)
                        if result.state and result.state != value['state']:
                            details['state'] = result.state
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
    def delete(request, _=None):  # pylint: disable=unused-argument
        task_id = request.data.get('task_id', None)
        signal = request.data.get('signal', None) or 'SIGKILL'
        if not task_id:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        result = AsyncResult(task_id)
        user = request.user
        if not user.is_staff:  # non-admin users should be able to cancel their own tasks
            task_info = parse_bulk_import_task_id(task_id)
            username = task_info.get('username', None)
            if not username:
                username = get(result, 'args.1')  # for parallel child tasks
            if username != user.username:
                return Response(status=status.HTTP_403_FORBIDDEN)

        try:
            app.control.revoke(task_id, terminate=True, signal=signal)

            # Below code is needed for removing the lock from QueueOnce
            if (get(result, 'name') or '').startswith('core.common.tasks.bulk_import'):
                celery_once_key = get_bulk_import_celery_once_lock_key(result)
                if celery_once_key:
                    celery_once = QueueOnce()
                    celery_once.name = result.name
                    celery_once.once_backend.clear_lock(celery_once_key)
        except Exception as ex:
            return Response(dict(errors=ex.args), status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)


class BulkImportParallelInlineView(APIView):  # pragma: no cover
    permission_classes = (IsAuthenticated, )
    parser_classes = (MultiPartParser, FormParser)

    @swagger_auto_schema(
        manual_parameters=[update_if_exists_param, file_url_param, file_upload_param, parallel_threads_param],
    )
    def post(self, request, import_queue=None):
        parallel_threads = request.data.get('parallel') or 5
        file = None
        file_name = None
        is_upload = 'file' in request.data
        is_file_url = 'file_url' in request.data
        is_data = 'data' in request.data
        file_content = None
        try:
            if is_upload:
                file = request.data['file']
                file_name = file.name
                file_content = file.read().decode('utf-8')
            elif is_file_url:
                file_name = request.data['file_url']
                file = requests.get(file_name)
                file_content = file.text
        except:  # pylint: disable=bare-except
            pass

        if not file_content and not is_data:
            return Response(dict(exception=NO_CONTENT_TO_IMPORT), status=status.HTTP_400_BAD_REQUEST)

        if file_name and is_csv_file(name=file_name):
            data = OclStandardCsvToJsonConverter(
                input_list=csv_file_data_to_input_list(file_content),
                allow_special_characters=True
            ).process()
        elif file:
            data = file_content
        else:
            data = request.data.get('data')

        return import_response(self.request, import_queue, data, parallel_threads, True)


class BulkImportInlineView(APIView):  # pragma: no cover
    permission_classes = (IsAuthenticated, )
    parser_classes = (MultiPartParser, FormParser)

    @swagger_auto_schema(
        manual_parameters=[update_if_exists_param, file_url_param, file_upload_param],
    )
    def post(self, request, import_queue=None):
        file = None
        file_name = None
        is_upload = 'file' in request.data
        is_file_url = 'file_url' in request.data
        is_data = 'data' in request.data
        file_content = None
        try:
            if is_upload:
                file = request.data['file']
                file_name = file.name
                file_content = file.read().decode('utf-8')
            elif is_file_url:
                file_name = request.data['file_url']
                file = requests.get(file_name)
                file_content = file.text
        except:  # pylint: disable=bare-except
            pass

        if not file_content and not is_data:
            return Response(dict(exception=NO_CONTENT_TO_IMPORT), status=status.HTTP_400_BAD_REQUEST)

        if file_name and is_csv_file(name=file_name):
            data = OclStandardCsvToJsonConverter(
                input_list=csv_file_data_to_input_list(file_content),
                allow_special_characters=True
            ).process()
        elif file:
            data = file_content
        else:
            data = request.data.get('data')

        return import_response(self.request, import_queue, data, None, True)
