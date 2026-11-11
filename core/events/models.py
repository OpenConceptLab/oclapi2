from django.db import models
from pydash import has, compact


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
            if not private or not cls.can_follower_view_private_events(following.follower, following.following):
                events = events.filter(public=True)
            queryset = queryset.union(events)
        return queryset

    @staticmethod
    def can_follower_view_private_events(follower, followed):
        """Private events of an org or repo go to followers who can view it. A user's private events are about
        orgs and repos the follower may not view, so only staff get them (org members get them in the orgs scope)."""
        if followed.__class__.__name__ == 'UserProfile':
            return follower.is_staff
        return follower.can_view(followed)

    @classmethod
    def get_user_organization_events(cls, user, private=False):
        org_uris = list(user.organizations.values_list('uri', flat=True))
        source_uris = compact(user.organizations.values_list('source__uri', flat=True))
        collection_uris = compact(user.organizations.values_list('collection__uri', flat=True))

        queryset = Event.objects.filter(
            models.Q(object_url__in=org_uris) |
            models.Q(referenced_object_url__in=[*org_uris, *source_uris, *collection_uris])
        )

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

    @staticmethod
    def get_model_for_url(url):  # pylint: disable=too-many-return-statements
        """The model of the object an event's URL names."""
        if not url:
            return None
        if '/mappings/' in url:
            from core.mappings.models import Mapping
            return Mapping
        if '/concepts/' in url:
            from core.concepts.models import Concept
            return Concept
        if '/sources/' in url:
            from core.sources.models import Source
            return Source
        if '/collections/' in url:
            from core.collections.models import Collection
            return Collection
        if '/orgs/' in url:
            from core.orgs.models import Organization
            return Organization
        if '/users/' in url:
            from core.users.models import UserProfile
            return UserProfile
        return None

    @property
    def referenced_object(self):
        model = self.get_model_for_url(self.referenced_object_url)
        return model.objects.filter(uri=self.referenced_object_url).first() if model else None

    @classmethod
    def get_hidden_urls(cls, urls, user):
        """The URLs among `urls` of objects that exist and `user` can't view now. A user can always be viewed."""
        from core.users.models import UserProfile
        urls_by_model = {}
        for url in urls:
            model = cls.get_model_for_url(url)
            if model and model is not UserProfile:
                urls_by_model.setdefault(model, set()).add(url)
        hidden = set()
        for model, model_urls in urls_by_model.items():
            for obj in model.objects.filter(uri__in=model_urls):
                if not UserProfile.can_user_view(user, obj):
                    hidden.add(obj.uri)
        return hidden

    @classmethod
    def get_visible_to(cls, queryset, user):
        """
        The events of `queryset` without those of an org or repo `user` can't view now, such as one made private after
        the event: its stored URLs would still name it. Staff get them all.
        """
        if getattr(user, 'is_staff', False):
            return queryset
        is_combined = bool(queryset.query.combinator)
        pairs = queryset.order_by().values_list('object_url', 'referenced_object_url')
        if not is_combined:  # a union's rows are distinct already
            pairs = pairs.distinct()
        hidden = cls.get_hidden_urls({url for pair in pairs for url in pair}, user)
        if not hidden:
            return queryset
        if is_combined:  # a union can't be filtered
            queryset = cls.objects.filter(id__in=queryset.order_by().values('id'))
        return queryset.exclude(object_url__in=hidden).exclude(referenced_object_url__in=hidden)

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
