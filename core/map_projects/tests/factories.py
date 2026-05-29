import factory
from factory import Sequence, SubFactory

from core.map_projects.models import MapProject, AutomatchRun
from core.orgs.tests.factories import OrganizationFactory


class MapProjectFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MapProject

    organization = SubFactory(OrganizationFactory)
    name = Sequence("Project-{}".format)  # pylint: disable=consider-using-f-string
    input_file_name = "input.csv"
    columns = [{'label': 'name', 'hidden': False, 'dataKey': 'name', 'original': 'name'}]


class AutomatchRunFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AutomatchRun

    map_project = SubFactory(MapProjectFactory)
    intended_rows = 100
    trigger_source = 'ui-auto-match'
