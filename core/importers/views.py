import csv
import io
import os
import shutil
import uuid

from datetime import datetime
import requests
from celery_once import AlreadyQueued
from django.db.models import Q
from django.http import Http404
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from ocldev.oclcsvtojsonconverter import OclStandardCsvToJsonConverter
from pydash import get
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.common.constants import DEPRECATED_API_HEADER
from core.common.views import BaseAPIView
from core.common.tasks import bulk_import_new
from core.common.swagger_parameters import update_if_exists_param, task_param, result_param, username_param, \
    file_upload_param, file_url_param, parallel_threads_param, verbose_param
from core.common.utils import queue_bulk_import, is_csv_file, get_truthy_values, get_queue_task_names, \
    get_export_service
from core.importers.constants import ALREADY_QUEUED, INVALID_UPDATE_IF_EXISTS, NO_CONTENT_TO_IMPORT
from core.importers.importer import Importer
from core.importers.input_parsers import ImportContentParser
from core.tasks.models import Task
from core.tasks.serializers import TaskDetailSerializer, TaskListSerializer
from core.users.models import UserProfile

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
    task = None
    try:
        task = queue_bulk_import(data, import_queue, username, update_if_exists, threads, inline)
        task.refresh_from_db()
    except AlreadyQueued:
        if task:
            task.delete()
        return Response({'exception': ALREADY_QUEUED}, status=status.HTTP_409_CONFLICT)
    response = Response(TaskListSerializer(task).data, status=status.HTTP_202_ACCEPTED)

    if deprecated:
        response[DEPRECATED_API_HEADER] = True
    return response


class ImportRetrieveDestroyMixin(BaseAPIView):
    def get_serializer_class(self):
        if self.request.GET.get('task'):
            return TaskDetailSerializer
        return TaskListSerializer

    @swagger_auto_schema(
        manual_parameters=[task_param, username_param, verbose_param],
    )
    def get(
            self, request, import_queue=None
    ):  # pylint: disable=too-many-return-statements,too-many-locals,too-many-branches
        task_id = request.GET.get('task')
        username = request.GET.get('username')
        requesting_user = self.request.user
        user = UserProfile.objects.filter(username=username).first() if username else requesting_user
        if not user:
            raise Http404('User not found')

        if not requesting_user.is_staff and requesting_user.username != user.username:
            return Response(status=status.HTTP_403_FORBIDDEN)

        tasks = user.async_tasks.filter(Q(name__icontains='bulk_import_parallel_inline') |
                                        Q(name__icontains='bulk_import_new')).order_by('-created_at')

        if task_id:
            task = tasks.filter(id=task_id).first()
            if not task:
                return Response(status=status.HTTP_404_NOT_FOUND)
            return Response(self.get_serializer(task).data)

        if import_queue:
            tasks = tasks.filter(Task.queue_criteria(import_queue))
            tasks = Task.objects.filter(id__in=tasks.values_list('id', flat=True))

        return Response(self.get_serializer(tasks, many=True).data)

    @staticmethod
    @swagger_auto_schema(request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            'task_id': openapi.Schema(
                type=openapi.TYPE_STRING, description='Task Id to be terminated (mandatory)',
            ),
        }
    ))
    def delete(request, _=None):  # pylint: disable=unused-argument
        task_id = request.data.get('task_id', None)
        if not task_id:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        task = Task.objects.filter(id=task_id).first()

        if not task:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not task.has_access(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)

        try:
            task.revoke()
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
        if 'import_type' in request.data:
            file_url = get(request.data, 'file_url')  # importing as url to a file
            if not file_url:
                data = get(request.data, 'data')  # importing by posting as text
                if data:
                    file = io.StringIO(data)
                else:
                    file = get(request.data, 'file')  # importing by uploading a file with multipart/form-data
                if file:
                    timestamp = datetime.now()
                    key = f'import_upload_{timestamp.strftime("%Y%m%d_%H%M%S")}_{str(uuid.uuid4())[:8]}'
                    from core import settings
                    if settings.DEBUG:
                        dir_url = os.path.join(settings.MEDIA_ROOT, 'import_uploads')
                        os.makedirs(dir_url, exist_ok=True)
                        file_url = os.path.join(dir_url, key)
                        with open(file_url, 'wb') as f:
                            shutil.copyfileobj(file, f)
                    else:
                        if not key.startswith(Importer.IMPORT_CACHE):
                            key = Importer.IMPORT_CACHE + key
                        upload_service = get_export_service()
                        upload_service.upload(key, file,
                                              metadata={'ContentType': 'application/octet-stream'},
                                              headers={'content-type': 'application/octet-stream'})
                        file_url = key

            task = get_queue_task_names(import_queue, self.request.user.username)
            new_task = bulk_import_new.apply_async(
                (file_url, self.request.user.username,
                 request.data.get('owner_type', 'user'), request.data.get('owner', self.request.user.username),
                 request.data.get('import_type', 'npm')), task_id=task.id, queue=task.queue)
            return Response({
                'task': new_task.id,
                'state': new_task.state
            }, status=status.HTTP_202_ACCEPTED)

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
