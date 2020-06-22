import factory
from factory import Sequence, SubFactory

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

    @factory.post_generation
    def organizations(self, create, extracted):
        if not create:
            return

        if extracted:
            for org in extracted:
                self.organizations.add(org)

