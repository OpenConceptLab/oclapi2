from django.db import models
from django.db.models.functions import Coalesce
from pydash import has, compact

from core.common.constants import ACCESS_TYPE_VIEW, RETIRED_ACCESS_TYPE_EDIT, HEAD


def get_repo_head_url(field):
    """The URL of the repo HEAD an event URL is in, read by position, or '' when it names no repo."""
    return Coalesce(
        models.Func(
            models.F(field), function='SUBSTRING',
            template="%(function)s(%(expressions)s FROM '^(/(?:orgs|users)/[^/]+/(?:sources|collections)/[^/]+/)')",
            output_field=models.TextField()),
        models.Value(''), output_field=models.TextField())


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
        """
        The model of the object an event's URL names, read by position:
        /<orgs|users>/<owner>/[<sources|collections>/<repo>/[<version>/]][<concepts|mappings>/<id>/...]
        """
        parts = [part for part in (url or '').split('/') if part]
        if len(parts) < 2 or parts[0] not in ('orgs', 'users'):
            return None
        if len(parts) == 2:
            if parts[0] == 'orgs':
                from core.orgs.models import Organization
                return Organization
            from core.users.models import UserProfile
            return UserProfile
        if len(parts) < 4 or parts[2] not in ('sources', 'collections'):
            return None
        content_type = parts[4] if len(parts) >= 6 else None
        if content_type not in ('concepts', 'mappings') and len(parts) >= 7:
            content_type = parts[5]
        if content_type == 'concepts':
            from core.concepts.models import Concept
            return Concept
        if content_type == 'mappings':
            from core.mappings.models import Mapping
            return Mapping
        if len(parts) > 5:
            return None
        if parts[2] == 'sources':
            from core.sources.models import Source
            return Source
        from core.collections.models import Collection
        return Collection

    @property
    def referenced_object(self):
        model = self.get_model_for_url(self.referenced_object_url)
        return model.objects.filter(uri=self.referenced_object_url).first() if model else None

    @staticmethod
    def get_hidden_criteria(field, user):
        """
        A query on an event URL field for the orgs, repos, repo versions and repo content `user` can't view now, as
        UserProfile.can_user_view decides; built from subqueries so the database applies it with the page.
        """
        from core.collections.models import Collection
        from core.orgs.models import Organization
        from core.sources.models import Source
        is_authenticated = bool(user and user.is_authenticated)

        orgs = Organization.objects.exclude(id__in=Organization.get_public().values('id'))
        if is_authenticated:
            orgs = orgs.exclude(members__id=user.id)
        criteria = models.Q(**{f'{field}__in': orgs.filter(uri__isnull=False).values('uri')})

        for model in (Source, Collection):
            repos = model.objects.exclude(public_access__in=[ACCESS_TYPE_VIEW, RETIRED_ACCESS_TYPE_EDIT])
            if is_authenticated:
                repos = repos.exclude(user_id=user.id).exclude(organization__members__id=user.id)
            repos = repos.filter(uri__isnull=False)
            # a private HEAD hides its versions and content too; a private version only itself
            criteria |= models.Q(**{f'{field}_repo__in': repos.filter(version=HEAD).values('uri')})
            criteria |= models.Q(**{f'{field}__in': repos.exclude(version=HEAD).values('uri')})
        return criteria

    @classmethod
    def get_visible_to(cls, queryset, user):
        """
        The events of `queryset` without those of an org or repo `user` can't view now, such as one made private after
        the event: its stored URLs would still name it. Staff get them all.
        """
        if getattr(user, 'is_staff', False):
            return queryset
        if queryset.query.combinator:  # a union can't be filtered
            queryset = cls.objects.filter(id__in=queryset.order_by().values('id'))
        return queryset.annotate(
            object_url_repo=get_repo_head_url('object_url'),
            referenced_object_url_repo=get_repo_head_url('referenced_object_url')
        ).exclude(
            cls.get_hidden_criteria('object_url', user) | cls.get_hidden_criteria('referenced_object_url', user))

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
