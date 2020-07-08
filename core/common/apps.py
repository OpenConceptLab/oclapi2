from django.apps import AppConfig


class CommonConfig(AppConfig):
    name = 'core.common'
    verbose_name = "Common"

    def ready(self):
        from core.common import signals  # pylint: disable=unused-variable, unused-import
