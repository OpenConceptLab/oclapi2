from drf_yasg.utils import swagger_auto_schema
from pydash import compact
from rest_framework.generics import RetrieveAPIView, DestroyAPIView, get_object_or_404
from rest_framework.permissions import IsAuthenticated, IsAdminUser

from core.common.exceptions import Http400
from core.common.mixins import ListWithHeadersMixin
from core.common.swagger_parameters import page_param, verbose_param, task_state_param, limit_param, \
    task_start_date_param, q_param
from core.common.utils import from_string_to_date, get_truthy_values
from core.common.views import BaseAPIView
from core.tasks.models import Task
from core.tasks.serializers import TaskDetailSerializer, TaskListSerializer, TaskResultSerializer


class AbstractTaskView(BaseAPIView, RetrieveAPIView):
    queryset = Task.objects.filter()
    is_searchable = False
    permission_classes = (IsAuthenticated,)
    default_qs_sort_attr = '-created_at'

    def get_serializer_class(self):
        if self.request.query_params.get('result', None) in get_truthy_values():
            return TaskResultSerializer
        return TaskDetailSerializer if self.is_verbose() else TaskListSerializer

    def get_queryset(self):
        states = compact((self.request.query_params.get('state', None) or '').split(','))
        start_date = from_string_to_date(self.request.query_params.get('start_date', None))
        search_str = self.request.query_params.get('q', None)
        queryset = self.queryset.filter(started_at__gte=start_date) if start_date else self.queryset

        if states:
            queryset = queryset.filter(state__in=states)
        if search_str:
            queryset = queryset.filter(name__icontains=search_str)

        return queryset.order_by('-created_at')


class AbstractTaskListView(AbstractTaskView, ListWithHeadersMixin):
    def get_serializer_class(self):
        return TaskDetailSerializer if self.is_verbose() else TaskListSerializer

    @swagger_auto_schema(
        manual_parameters=[q_param, task_state_param, task_start_date_param, page_param, limit_param, verbose_param]
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class TaskView(AbstractTaskView, DestroyAPIView):
    lookup_field = 'id'
    lookup_url_kwarg = 'task_id'
    pk_field = 'id'

    def get_object(self, queryset=None):
        queryset = self.get_queryset()
        obj = get_object_or_404(queryset, **{self.lookup_field: self.kwargs[self.lookup_url_kwarg]})
        self.check_object_permissions(self.request, obj)
        return obj

    def perform_destroy(self, instance):
        if not instance.has_access(self.request.user):
            self.permission_denied(self.request)
        if instance.is_finished:
            raise Http400('Task is already finished.')
        try:
            instance.revoke()
        except Exception as ex:
            raise Http400({'errors': ex.args}) from ex


class UserTaskListView(AbstractTaskListView):
    def get_queryset(self):
        return super().get_queryset().filter(created_by=self.request.user)


class TaskListView(AbstractTaskListView):
    permission_classes = (IsAdminUser,)
