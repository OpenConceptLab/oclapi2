import factory
from factory import Sequence, SubFactory
from factory.fuzzy import FuzzyText

from core.orgs.tests.factories import OrganizationFactory
from core.users.models import UserProfile


class UserProfileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UserProfile
    email = Sequence("email{}@test.com".format)
    username = Sequence("email{}@test.com".format)
    first_name = Sequence("First-{}".format)
    last_name = Sequence("Last-{}".format)
    password = 'Password1$'
    mobile = FuzzyText(length=8, prefix='04', chars=['1', '2', '3', '4', '5', '6', '7', '8', '9'])
    organization = SubFactory(OrganizationFactory)
