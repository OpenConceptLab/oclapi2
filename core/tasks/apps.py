from django.apps import AppConfig


class TaskConfig(AppConfig):
    name = 'core.tasks'
    verbose_name = "Task"

    def ready(self):
        from core.tasks import signals  # pylint: disable=unused-variable, unused-import
