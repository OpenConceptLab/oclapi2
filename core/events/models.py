from django.db import models
from pydash import has


class Event(models.Model):
    object_url = models.CharField(max_length=255)
    referenced_object_url = models.CharField(max_length=255)
    event_type = models.CharField(max_length=255)
    actor = models.ForeignKey('users.UserProfile', on_delete=models.DO_NOTHING, related_name='actor_events')
    created_at = models.DateTimeField(auto_now_add=True)
    public = models.BooleanField(default=True)  # private events are shown to creator/staff/org members only
    _referenced_object = models.JSONField(null=True, blank=True)

    CREATED = 'Created'
    RELEASED = 'Released'
    DELETED = 'Deleted'
    FOLLOWED = 'Followed'
    UNFOLLOWED = 'Unfollowed'
    HIGHLIGHT_EVENT_TYPES = [CREATED, RELEASED, FOLLOWED]

    @staticmethod
    def object_criteria(url):
        return models.Q(object_url=url) | models.Q(referenced_object_url=url)

    @classmethod
    def get_two_way_events_for(cls, object_url):
        return cls.objects.filter(cls.object_criteria(object_url))

    @classmethod
    def get_user_following_events(cls, user, private=False, **event_kwargs):
        return cls.get_events_for_following(user.following.filter(), private, **event_kwargs)

    @classmethod
    def get_events_for_following(cls, following_queryset, private=False, **event_kwargs):
        queryset = cls.objects.none()
        for following in following_queryset:
            events = following.following.events.filter(**event_kwargs)
            if not private:
                events = events.filter(public=True)
            queryset = queryset.union(events)
        return queryset

    @classmethod
    def get_user_organization_events(cls, user, private=False):
        queryset = cls.objects.none()
        for org in user.organizations.filter():
            events = org.events
            if not private:
                events = events.filter(public=True)
            queryset = queryset.union(events)
        return queryset

    @classmethod
    def get_user_all_events(cls, user, private=False):
        return cls.get_user_organization_events(user, private).union(
            cls.get_user_following_events(user, private))

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
        return self.get_object_repr(self.referenced_object or self._referenced_object)

    @property
    def object_repr(self):
        return self.get_object_repr(self.object)

    @staticmethod
    def get_object_repr(object_instance):
        if isinstance(object_instance, dict):
            return f"{object_instance.get('type', None)}:{object_instance.get('id', None)}"
        return repr(object_instance) if object_instance else None

    @property
    def description(self):
        return f"{self.event_type} {self.referenced_object_repr}"

    def clean_fields(self, exclude=None):
        if self.public is None:
            self.public = False
        super().clean_fields(exclude=exclude)

    @classmethod
    def record(cls, instance, event_type=CREATED):
        if instance.id:
            public_can_view = instance.public_can_view if has(instance, 'public_can_view') else True
            cls.objects.create(
                object_url=instance.updated_by.url, event_type=event_type, actor=instance.updated_by,
                referenced_object_url=instance.url, public=public_can_view,
                _referenced_object=instance.get_brief_serializer()(instance).data
            )
