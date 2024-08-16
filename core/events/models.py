from django.db import models


class Event(models.Model):
    object_url = models.CharField(max_length=255)
    referenced_object_url = models.CharField(max_length=255)
    event_type = models.CharField(max_length=255)
    actor = models.ForeignKey('users.UserProfile', on_delete=models.DO_NOTHING, related_name='creator_events')
    created_at = models.DateTimeField(auto_now_add=True)
    public = models.BooleanField(default=True)  # private events are shown to creator/staff/org members only

    @property
    def type(self):
        return 'Event'

    @property
    def url(self):
        return f"{self.object_url}events/{self.id}/"

    @property
    def object(self):
        if '/orgs/' in self.object_url:
            from core.orgs.models import Organization
            return Organization.objects.filter(uri=self.object_url).first()
        if '/users/' in self.object_url:
            from core.users.models import UserProfile
            return UserProfile.objects.filter(uri=self.object_url).first()
        return None

    @property
    def referenced_object(self):  # pylint: disable=too-many-return-statements
        if '/mappings/' in self.referenced_object_url:
            from core.mappings.models import Mapping
            return Mapping.objects.filter(uri=self.referenced_object_url).first()
        if '/concepts/' in self.referenced_object_url:
            from core.concepts.models import Concept
            return Concept.objects.filter(uri=self.referenced_object_url).first()
        if '/sources/' in self.referenced_object_url:
            from core.sources.models import Source
            return Source.objects.filter(uri=self.referenced_object_url).first()
        if '/collections/' in self.referenced_object_url:
            from core.collections.models import Collection
            return Collection.objects.filter(uri=self.referenced_object_url).first()
        if '/orgs/' in self.referenced_object_url:
            from core.orgs.models import Organization
            return Organization.objects.filter(uri=self.referenced_object_url).first()
        if '/users/' in self.referenced_object_url:
            from core.users.models import UserProfile
            return UserProfile.objects.filter(uri=self.referenced_object_url).first()
        return None

    @property
    def referenced_object_repr(self):
        return self.get_object_repr(self.referenced_object)

    @property
    def object_repr(self):
        return self.get_object_repr(self.object)

    @staticmethod
    def get_object_repr(object_instance):
        if object_instance:
            return f"{object_instance.__class__.__name__}:{object_instance.mnemonic}"
        return None

    @property
    def description(self):
        description = f"{self.object_repr} {self.event_type} {self.referenced_object_repr}"
        description += f" by {self.actor.name} at {self.created_at}"
        return description

    def clean_fields(self, exclude=None):
        if self.public is None:
            self.public = False
        super().clean_fields(exclude=exclude)
