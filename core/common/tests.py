from colour_runner.django_runner import ColourRunnerMixin
from django.test.runner import DiscoverRunner


class CustomTestRunner(ColourRunnerMixin, DiscoverRunner):
    pass
