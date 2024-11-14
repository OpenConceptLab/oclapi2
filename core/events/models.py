from django.db import models
from pydash import has


class Event(models.Model):
    object_url = models.CharField(max_length=255)
    referenced_object_url = models.CharField(max_length=255, null=True, blank=True)
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
    JOINED = 'Joined'
    HIGHLIGHT_EVENT_TYPES = [CREATED, RELEASED]

    @property
    def is_joined_ocl(self):
        return self.event_type == self.JOINED and not self.referenced_object_url

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
        criterion = None
        for org in user.organizations.filter():
            criteria = Event.object_criteria(org.uri)
            repo_events_criteria = org.get_repo_events_criteria(private)
            if repo_events_criteria is not None:
                criteria |= repo_events_criteria
            if criterion is None:
                criterion = criteria
            else:
                criterion |= criteria

        queryset = Event.objects.filter(criterion)

        return queryset if private else queryset.filter(public=True)

    @classmethod
    def get_user_all_events(cls, user, private=False):
        return cls.get_user_organization_events(user, private).union(
            cls.get_user_following_events(user, private)
        ).union(user.get_repo_events(private))

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
        if not self.referenced_object_url:
            return None
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
        if self.is_joined_ocl:
            return self.event_type
        return f"{self.event_type} {self.referenced_object_repr}"

    def clean_fields(self, exclude=None):
        if self.public is None:
            self.public = False
        super().clean_fields(exclude=exclude)

    @classmethod
    def record(cls, reference_object, event_type=CREATED, object_instance=None, actor=None, **kwargs):
        if reference_object.id:
            if event_type == cls.JOINED and reference_object.__class__.__name__ == 'UserProfile':
                cls.record_joined_ocl(reference_object)
                return None
            public_can_view = reference_object.public_can_view if has(
                reference_object, 'public_can_view') else True
            object_instance = object_instance or reference_object.updated_by
            actor = actor or reference_object.updated_by
            return cls.objects.create(
                object_url=object_instance.url, event_type=event_type, actor=actor,
                referenced_object_url=reference_object.url, public=public_can_view,
                _referenced_object=reference_object.get_brief_serializer()(reference_object).data,
                **kwargs
            )
        return None

    @classmethod
    def record_joined_ocl(cls, user, **kwargs):
        return cls.objects.create(
            object_url=user.url, event_type=cls.JOINED, actor=user,
            public=True,
            **kwargs
        )
