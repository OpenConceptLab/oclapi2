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
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.celery import app
from core.common.constants import DEPRECATED_API_HEADER
from core.common.services import RedisService
from core.common.swagger_parameters import update_if_exists_param, task_param, result_param, username_param, \
    file_upload_param, file_url_param, parallel_threads_param, verbose_param
from core.common.utils import parse_bulk_import_task_id, task_exists, flower_get, queue_bulk_import, \
    get_bulk_import_celery_once_lock_key, is_csv_file, get_truthy_values
from core.importers.constants import ALREADY_QUEUED, INVALID_UPDATE_IF_EXISTS, NO_CONTENT_TO_IMPORT
from core.importers.input_parsers import ImportContentParser

TRUTHY = get_truthy_values()


def csv_file_data_to_input_list(file_content):
    return [row for row in csv.DictReader(io.StringIO(file_content))]  # pylint: disable=unnecessary-comprehension


def import_response(request, import_queue, data, threads=None, inline=False, deprecated=False):  # pylint: disable=too-many-arguments
    if not data:
        return Response({'exception': NO_CONTENT_TO_IMPORT}, status=status.HTTP_400_BAD_REQUEST)

    user = request.user
    username = user.username
    update_if_exists = request.GET.get('update_if_exists', 'true')
    if update_if_exists not in ['true', 'false']:
        return Response(
            {'exception': INVALID_UPDATE_IF_EXISTS},
            status=status.HTTP_400_BAD_REQUEST
        )
    update_if_exists = update_if_exists == 'true'

    data = data.decode('utf-8') if isinstance(data, bytes) else data

    try:
        task = queue_bulk_import(data, import_queue, username, update_if_exists, threads, inline)
    except AlreadyQueued:
        return Response({'exception': ALREADY_QUEUED}, status=status.HTTP_409_CONFLICT)
    parsed_task = parse_bulk_import_task_id(task.id)
    response = Response({
                            'task': task.id,
                            'state': task.state,
                            'username': username,
                            'queue': parsed_task['queue']
                        }, status=status.HTTP_202_ACCEPTED)
    if deprecated:
        response[DEPRECATED_API_HEADER] = True
    return response


class ImportRetrieveDestroyMixin(APIView):
    @swagger_auto_schema(
        manual_parameters=[task_param, result_param, username_param, verbose_param],
    )
    def get(
            self, request, import_queue=None
    ):  # pylint: disable=too-many-return-statements,too-many-locals,too-many-branches
        task_id = request.GET.get('task')
        result_format = request.GET.get('result')
        username = request.GET.get('username')
        is_verbose = request.GET.get('verbose') in TRUTHY
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
                return Response({'exception': str(task.result)}, status=status.HTTP_400_BAD_REQUEST)
            if task.state == 'STARTED':
                service = RedisService()
                if service.exists(task_id):
                    return Response(
                        {
                            'details': service.get_formatted(task_id),
                            'task': task.id,
                            'state': task.state,
                            'username': username,
                            'queue': parsed_task['queue']
                        },
                        status=status.HTTP_200_OK
                    )
            if task.state == 'PENDING' and not task_exists(task_id):
                return Response({'exception': 'task ' + task_id + ' not found'}, status=status.HTTP_404_NOT_FOUND)

            return Response(
                {
                    'task': task.id,
                    'state': task.state,
                    'username': username,
                    'queue': parsed_task['queue']
                },
                status=status.HTTP_202_ACCEPTED
            )

        try:
            flower_tasks = {}
            import_task_names = [
                'core.common.tasks.bulk_import',
                'core.common.tasks.bulk_import_parallel_inline',
                'core.common.tasks.bulk_import_inline',
            ]
            for import_task_name in import_task_names:
                flower_tasks = {**flower_tasks, **flower_get(f'api/tasks?taskname={import_task_name}').json()}

            pending_tasks = RedisService().get_pending_tasks(
                import_queue or 'bulk_import_root',
                import_task_names,
                ['bulk_import_parts_inline']
            )
            all_tasks = {
                **flower_tasks,
                **{task['task_id']: task for task in pending_tasks if task.get('task_id')},
            }
        except Exception as ex:
            return Response(
                {'detail': 'Flower service returned unexpected result. Maybe check healthcheck.', 'exception': str(ex)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

        tasks = []
        for task_id, value in all_tasks.items():
            task = parse_bulk_import_task_id(task_id)

            if user.is_staff or user.username == task['username']:
                if (not import_queue or task['queue'] == import_queue) and \
                        (not username or task['username'] == username):
                    details = {
                        'task': task_id,
                        'state': value['state'],
                        'queue': task['queue'],
                        'username': task['username']
                    }
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
            return Response({'errors': ex.args}, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)


# DEPRECATED
class BulkImportParallelInlineView(APIView):
    permission_classes = (IsAuthenticated, )
    deprecated = True

    def get_parsers(self):
        if 'application/json' in [self.request.META.get('CONTENT_TYPE')]:
            return [JSONParser()]
        if self.request.method == 'POST':
            return [MultiPartParser(), FormParser()]
        return super().get_parsers()

    @swagger_auto_schema(
        manual_parameters=[update_if_exists_param, file_url_param, file_upload_param, parallel_threads_param],
        deprecated=True
    )
    def post(self, request, import_queue=None):
        parallel_threads = request.data.get('parallel') or 5
        is_upload = 'file' in request.data
        is_file_url = 'file_url' in request.data
        is_data = 'data' in request.data
        parser = ImportContentParser(
            file=get(request.data, 'file') if is_upload else None,
            file_url=get(request.data, 'file_url') if is_file_url else None,
            content=get(request.data, 'data') if is_data else None,
            owner=get(request.data, 'owner') or None,
            owner_type=get(request.data, 'owner_type') or None,
            version=get(request.data, 'version') or None,
        )
        parser.parse()
        if parser.errors:
            return Response({'exception': ' '.join(parser.errors)}, status=status.HTTP_400_BAD_REQUEST)

        return import_response(self.request, import_queue, parser.content, parallel_threads, True, self.deprecated)


class ImportView(BulkImportParallelInlineView, ImportRetrieveDestroyMixin):
    deprecated = False

    @swagger_auto_schema(
        manual_parameters=[update_if_exists_param, file_url_param, file_upload_param, parallel_threads_param],
    )
    def post(self, request, import_queue=None):
        return super().post(request, import_queue)


# DEPRECATED
class BulkImportFileUploadView(APIView):  # pragma: no cover
    permission_classes = (IsAuthenticated, )
    parser_classes = (MultiPartParser, )
    deprecated = True

    @swagger_auto_schema(
        manual_parameters=[update_if_exists_param, file_upload_param],
        deprecated=True
    )
    def post(self, request, import_queue=None):
        file = request.data.get('file', None)

        if not file:
            return Response({'exception': NO_CONTENT_TO_IMPORT}, status=status.HTTP_400_BAD_REQUEST)

        if is_csv_file(name=file.name):
            try:
                data = OclStandardCsvToJsonConverter(
                    input_list=csv_file_data_to_input_list(file.read().decode('utf-8')),
                    allow_special_characters=True
                ).process()
            except Exception as ex:  # pylint: disable=broad-except
                return Response({'exception': f'Bad CSV ({str(ex)})'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            data = file.read()

        return import_response(request=self.request, import_queue=import_queue, data=data, deprecated=self.deprecated)


# DEPRECATED
class BulkImportFileURLView(APIView):  # pragma: no cover
    permission_classes = (IsAuthenticated, )
    parser_classes = (MultiPartParser, )
    deprecated = True

    @swagger_auto_schema(
        manual_parameters=[update_if_exists_param, file_url_param],
        deprecated=True
    )
    def post(self, request, import_queue=None):
        file = None
        file_url = request.data.get('file_url')

        try:
            headers = {
                'User-Agent': 'OCL'  # user-agent required by mod_security on some servers
            }
            file = requests.get(file_url, headers=headers)
        except:  # pylint: disable=bare-except
            pass

        if not file:
            return Response({'exception': NO_CONTENT_TO_IMPORT}, status=status.HTTP_400_BAD_REQUEST)

        if is_csv_file(name=file_url):
            try:
                data = OclStandardCsvToJsonConverter(
                    input_list=csv_file_data_to_input_list(file.text), allow_special_characters=True).process()
            except Exception as ex:  # pylint: disable=broad-except
                return Response({'exception': f'Bad CSV ({str(ex)})'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            data = file.text

        return import_response(request=self.request, import_queue=import_queue, data=data, deprecated=self.deprecated)


# DEPRECATED
class BulkImportView(ImportRetrieveDestroyMixin):  # pragma: no cover
    deprecated = True

    @swagger_auto_schema(
        manual_parameters=[update_if_exists_param],
        request_body=openapi.Schema(type=openapi.TYPE_OBJECT),
        deprecated=True
    )
    def post(self, request, import_queue=None):
        return import_response(request=self.request, import_queue=import_queue, data=request.body, deprecated=True)

    @swagger_auto_schema(
        manual_parameters=[task_param, result_param, username_param, verbose_param],
        deprecated=True
    )
    def get(
            self, request, import_queue=None
    ):
        response = super().get(request, import_queue)
        response[DEPRECATED_API_HEADER] = self.deprecated
        return response

    @staticmethod
    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'task_id': openapi.Schema(
                    type=openapi.TYPE_STRING, description='Task Id to be terminated (mandatory)',
                ),
                'signal': openapi.Schema(
                    type=openapi.TYPE_STRING, description='Kill Signal', default='SIGKILL',
                ),
            }
        ),
        deprecated=True
    )
    def delete(request, _=None):  # pylint: disable=unused-argument
        response = super().delete(request)
        response[DEPRECATED_API_HEADER] = True
        return response


# DEPRECATED
class BulkImportInlineView(APIView):  # pragma: no cover
    permission_classes = (IsAuthenticated, )
    parser_classes = (MultiPartParser, FormParser)
    deprecated = True

    @swagger_auto_schema(
        manual_parameters=[update_if_exists_param, file_url_param, file_upload_param],
        deprecated=True
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
                headers = {
                    'User-Agent': 'OCL'  # user-agent required by mod_security on some servers
                }
                file = requests.get(file_name, headers=headers)
                file_content = file.text
        except:  # pylint: disable=bare-except
            pass

        if not file_content and not is_data:
            return Response({'exception': NO_CONTENT_TO_IMPORT}, status=status.HTTP_400_BAD_REQUEST)

        if file_name and is_csv_file(name=file_name):
            try:
                data = OclStandardCsvToJsonConverter(
                    input_list=csv_file_data_to_input_list(file_content),
                    allow_special_characters=True
                ).process()
            except Exception as ex:  # pylint: disable=broad-except
                return Response({'exception': f'Bad CSV ({str(ex)})'}, status=status.HTTP_400_BAD_REQUEST)
        elif file:
            data = file_content
        else:
            data = request.data.get('data')

        return import_response(self.request, import_queue, data, None, True, self.deprecated)
