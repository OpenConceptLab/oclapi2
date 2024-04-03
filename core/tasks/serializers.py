from rest_framework.fields import CharField
from rest_framework.serializers import ModelSerializer

from core.tasks.models import Task


class TaskBriefSerializer(ModelSerializer):
    task = CharField(source='id')
    queue = CharField(source='queue_name')

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
