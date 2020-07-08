from django.apps import AppConfig


class SourceConfig(AppConfig):
    name = 'core.sources'
    verbose_name = "Source"

    def ready(self):
        from core.sources import signals  # pylint: disable=unused-variable, unused-import
