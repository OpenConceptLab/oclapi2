from pydash import get
from rest_framework.fields import CharField, SerializerMethodField, JSONField
from rest_framework.serializers import ModelSerializer

from core.tasks.models import Task


class TaskBriefSerializer(ModelSerializer):
    task = CharField(source='id')
    queue = CharField(source='queue_name')
    result = SerializerMethodField()

    class Meta:
        model = Task
        fields = ('id', 'state', 'name', 'queue', 'username', 'task', 'result')

    def __init__(self, *args, **kwargs):  # pylint: disable=too-many-branches
        request = get(kwargs, 'context.request')
        params = get(request, 'query_params')
        self.view_kwargs = get(kwargs, 'context.view.kwargs', {})

        self.query_params = params.dict() if params else {}
        self.include_result = bool(self.query_params.get('result'))
        if not self.include_result:
            self.fields.pop('result', None)

        super().__init__(*args, **kwargs)

    def get_result(self, obj):
        if self.include_result:
            return obj.json_result
        return None


class TaskListSerializer(TaskBriefSerializer):
    class Meta:
        model = Task
        fields = TaskBriefSerializer.Meta.fields + (
            'created_at', 'started_at', 'finished_at', 'runtime', 'summary', 'children'
        )


class TaskDetailSerializer(TaskListSerializer):
    class Meta:
        model = Task
        fields = TaskListSerializer.Meta.fields + (
            'kwargs', 'error_message', 'traceback', 'retry'
        )


class TaskResultSerializer(TaskDetailSerializer):
    result = JSONField(read_only=True, source='json_result')

    class Meta:
        model = Task
        fields = TaskDetailSerializer.Meta.fields + (
            'result'
        )
