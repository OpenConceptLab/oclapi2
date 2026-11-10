from django.apps import AppConfig


class CapabilitiesConfig(AppConfig):
    name = 'core.capabilities'
    verbose_name = "Capabilities"

    def ready(self):
        from core.capabilities import checks  # pylint: disable=unused-variable, unused-import
