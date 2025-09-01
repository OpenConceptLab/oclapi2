import factory
from factory import Sequence, SubFactory

from core.map_projects.models import MapProject
from core.orgs.tests.factories import OrganizationFactory


class MapProjectFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MapProject

    organization = SubFactory(OrganizationFactory)
    name = Sequence("Project-{}".format)  # pylint: disable=consider-using-f-string
    input_file_name = "input.csv"
    columns = [{'label': 'name', 'hidden': False, 'dataKey': 'name', 'original': 'name'}]
