from rest_framework.fields import CharField, JSONField
from rest_framework.serializers import Serializer, ModelSerializer

from core.tasks.models import Task


class FlowerTaskSerializer(Serializer):  # pylint: disable=abstract-method
    task_id = CharField(read_only=True, source='task-id')
    state = CharField(read_only=True)
    result = JSONField(allow_null=True, read_only=True)


class TaskBriefSerializer(ModelSerializer):
    task = CharField(source='id')

    class Meta:
        model = Task
        fields = ('id', 'state', 'name', 'queue', 'username', 'task')


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
            'args', 'kwargs', 'result', 'error_message', 'traceback', 'retry'
        )
