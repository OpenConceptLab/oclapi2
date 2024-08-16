import factory
from factory import SubFactory
from pydash import get

from core.events.models import Event
from core.orgs.tests.factories import OrganizationFactory
from core.users.tests.factories import UserProfileFactory


class EventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Event

    event_type = "Created"
    actor = SubFactory(UserProfileFactory)

    @factory.post_generation
    def referenced_object(self, create, extracted):
        if not create or self.referenced_object_url:  # pylint: disable=access-member-before-definition
            return

        if extracted:
            self.referenced_object_url = get(extracted, 'url')
        else:
            self.referenced_object_url = OrganizationFactory().url  # pylint: disable=access-member-before-definition

    @factory.post_generation
    def object(self, create, extracted):
        if not create or self.object_url:  # pylint: disable=access-member-before-definition
            return

        if extracted:
            self.object_url = get(extracted, 'url')
        else:
            self.object_url = UserProfileFactory().url
