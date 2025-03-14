import uuid

from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from pydash import get
from rest_framework import serializers
from rest_framework.fields import IntegerField
from rest_framework.serializers import ModelSerializer
from rest_framework.validators import UniqueValidator

from core.common.constants import NAMESPACE_REGEX, INCLUDE_SUBSCRIBED_ORGS, INCLUDE_VERIFICATION_TOKEN, \
    INCLUDE_AUTH_GROUPS, INCLUDE_PINS, INCLUDE_FOLLOWERS, INCLUDE_FOLLOWING
from core.users.constants import INVALID_AUTH_GROUP_NAME
from .models import UserProfile, Follow
from ..common.serializers import AbstractResourceSerializer
from ..common.utils import get_truthy_values

TRUTHY = get_truthy_values()


class UserListSerializer(AbstractResourceSerializer):
    type = serializers.CharField(source='resource_type', read_only=True)

    class Meta:
        model = UserProfile
        fields = AbstractResourceSerializer.Meta.fields + (
            'username', 'name', 'url', 'logo_url', 'type', 'company'
        )


class UserSummarySerializer(serializers.ModelSerializer):
    sources = IntegerField(source='public_sources')
    collections = IntegerField(source='public_collections')
    organizations = IntegerField(source='orgs_count')
    bookmarks = IntegerField(source='bookmarks_count')

    class Meta:
        model = UserProfile
        fields = (
            'username', 'name', 'url', 'logo_url', 'sources', 'collections', 'organizations',
            'is_superuser', 'is_staff', 'first_name', 'last_name', 'status', 'bookmarks'
        )


class UserCreateSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='resource_type', read_only=True)
    uuid = serializers.CharField(source='id', read_only=True)
    username = serializers.CharField(required=True, validators=[
        RegexValidator(regex=NAMESPACE_REGEX),
        UniqueValidator(queryset=UserProfile.objects.all(), message='A user with this username already exists')
    ])
    name = serializers.CharField(required=False)
    first_name = serializers.CharField(required=False, write_only=True, allow_blank=True)
    last_name = serializers.CharField(required=False, write_only=True, allow_blank=True)
    email = serializers.CharField(required=True, validators=[
        UniqueValidator(queryset=UserProfile.objects.all(), message='A user with this email already exists')
    ])
    password = serializers.CharField(required=False, write_only=True)
    company = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    location = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    bio = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    preferred_locale = serializers.CharField(required=False)
    orgs = serializers.IntegerField(read_only=True, source='orgs_count')
    created_on = serializers.DateTimeField(source='created_at', read_only=True)
    updated_on = serializers.DateTimeField(source='updated_at', read_only=True)
    created_by = serializers.CharField(read_only=True)
    updated_by = serializers.CharField(read_only=True)
    extras = serializers.JSONField(required=False, allow_null=True)
    token = serializers.CharField(required=False, read_only=True)
    verified = serializers.BooleanField(required=False, default=True)
    website = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = UserProfile
        fields = (
            'type', 'uuid', 'username', 'name', 'email', 'company', 'location', 'bio', 'preferred_locale', 'orgs',
            'public_collections', 'public_sources', 'created_on', 'updated_on', 'created_by', 'updated_by',
            'url', 'extras', 'password', 'token', 'verified', 'first_name', 'last_name', 'website'
        )

    def create(self, validated_data):
        requesting_user = self.context['request'].user
        if requesting_user and requesting_user.is_anonymous:
            requesting_user = None
        username = validated_data.get('username')
        existing_profile = UserProfile.objects.filter(username=username)
        if existing_profile.exists():
            self._errors['username'] = f'User with username {username} already exists.'
            user = existing_profile.first()
            user.token = user.get_token()
            return user

        user = UserProfile(
            username=username,
            email=validated_data.get('email'),
            company=validated_data.get('company', None),
            location=validated_data.get('location', None),
            bio=validated_data.get('bio', None),
            extras=validated_data.get('extras', None),
            preferred_locale=validated_data.get('preferred_locale', None),
            first_name=validated_data.get('name', None) or validated_data.get('first_name'),
            last_name=validated_data.get('last_name', '')
        )
        password = validated_data.get('password', None)

        try:
            validate_password(password)
        except ValidationError as ex:
            self._errors['password'] = ex.messages
            return user

        user.set_password(password)

        if requesting_user:
            user.created_by = user.updated_by = requesting_user
        if 'verified' in validated_data:
            user.verified = validated_data['verified']
            if not user.verified:
                user.verification_token = uuid.uuid4()

        user.save()
        user.set_checksums()
        user.token = user.get_token()
        user.send_verification_email()
        from core.events.models import Event
        user.record_event(Event.JOINED)

        return user


class AbstractFollowerSerializer(ModelSerializer):
    url = serializers.CharField(source='uri', read_only=True)
    object = serializers.SerializerMethodField()

    class Meta:
        model = Follow
        fields = (
            'id', 'follow_date', 'object', 'url', 'type'
        )


class FollowerSerializer(AbstractFollowerSerializer):
    @staticmethod
    def get_object(obj):
        follower = obj.follower
        return follower.get_brief_serializer()(follower).data


class FollowingSerializer(AbstractFollowerSerializer):
    @staticmethod
    def get_object(obj):
        following = obj.following
        return following.get_brief_serializer()(following).data


class UserDetailSerializer(AbstractResourceSerializer):
    type = serializers.CharField(source='resource_type', read_only=True)
    uuid = serializers.CharField(source='id', read_only=True)
    username = serializers.CharField(required=False)
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False)
    email = serializers.CharField(required=False)
    company = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    website = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    location = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    bio = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    preferred_locale = serializers.CharField(required=False)
    orgs = serializers.IntegerField(read_only=True, source='orgs_count')
    owned_orgs = serializers.IntegerField(read_only=True, source='owned_orgs_count')
    sources = serializers.IntegerField(read_only=True, source='all_sources_count')
    bookmarks = serializers.IntegerField(read_only=True, source='bookmarks_count')
    collections = serializers.IntegerField(read_only=True, source='all_collections_count')
    created_on = serializers.DateTimeField(source='created_at', read_only=True)
    updated_on = serializers.DateTimeField(source='updated_at', read_only=True)
    created_by = serializers.CharField(read_only=True)
    updated_by = serializers.CharField(read_only=True)
    extras = serializers.JSONField(required=False, allow_null=True)
    subscribed_orgs = serializers.SerializerMethodField()
    auth_groups = serializers.ListField(required=False, allow_null=True, allow_empty=True)
    deactivated_at = serializers.DateTimeField(read_only=True)
    pins = serializers.SerializerMethodField()
    followers = FollowerSerializer(many=True, read_only=True)
    following = FollowingSerializer(many=True, read_only=True)
    rate_plan = serializers.CharField(source='api_rate_limit.rate_plan', read_only=True)

    class Meta:
        model = UserProfile
        fields = AbstractResourceSerializer.Meta.fields + (
            'type', 'uuid', 'username', 'name', 'email', 'company', 'location', 'preferred_locale', 'orgs',
            'public_collections', 'public_sources', 'created_on', 'updated_on', 'created_by', 'updated_by',
            'url', 'organizations_url', 'extras', 'sources_url', 'collections_url', 'website', 'last_login',
            'logo_url', 'subscribed_orgs', 'is_superuser', 'is_staff', 'first_name', 'last_name', 'verified',
            'verification_token', 'date_joined', 'auth_groups', 'status', 'deactivated_at',
            'sources', 'collections', 'owned_orgs', 'bookmarks', 'pins', 'bio', 'followers', 'following', 'rate_plan'
        )

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')
        self.query_params = params.dict() if params else {}
        self.include_subscribed_orgs = self.query_params.get(INCLUDE_SUBSCRIBED_ORGS) in TRUTHY
        self.include_verification_token = self.query_params.get(INCLUDE_VERIFICATION_TOKEN) in TRUTHY
        self.include_auth_groups = self.query_params.get(INCLUDE_AUTH_GROUPS) in TRUTHY
        self.include_pins = self.query_params.get(INCLUDE_PINS) in TRUTHY
        self.include_followers = self.query_params.get(INCLUDE_FOLLOWERS) in TRUTHY
        self.include_following = self.query_params.get(INCLUDE_FOLLOWING) in TRUTHY

        if not self.include_subscribed_orgs:
            self.fields.pop('subscribed_orgs')
        if not self.include_verification_token:
            self.fields.pop('verification_token')
        if not self.include_auth_groups:
            self.fields.pop('auth_groups')
        if not self.include_pins:
            self.fields.pop('pins')
        if not self.include_followers:
            self.fields.pop('followers')
        if not self.include_following:
            self.fields.pop('following')

        super().__init__(*args, **kwargs)

    def get_subscribed_orgs(self, obj):
        if self.include_subscribed_orgs:
            from core.orgs.serializers import OrganizationListSerializer
            return OrganizationListSerializer(obj.organizations.all(), many=True).data

        return None

    def get_pins(self, obj):
        if self.include_pins:
            from core.pins.serializers import PinSerializer
            return PinSerializer(obj.pins.prefetch_related('resource').all(), many=True).data

        return None

    def update(self, instance, validated_data):
        request_user = self.context['request'].user
        instance.email = validated_data.get('email', instance.email)
        instance.username = validated_data.get('username', instance.username)
        instance.first_name = validated_data.get('first_name', instance.first_name)
        instance.last_name = validated_data.get('last_name', instance.last_name)
        instance.company = validated_data.get('company', instance.company)
        instance.website = validated_data.get('website', instance.website)
        instance.location = validated_data.get('location', instance.location)
        instance.bio = validated_data.get('bio', instance.bio)
        instance.preferred_locale = validated_data.get('preferred_locale', instance.preferred_locale)
        instance.extras = validated_data.get('extras', instance.extras)
        instance.updated_by = request_user
        auth_groups = validated_data.get('auth_groups', None)
        if isinstance(auth_groups, list):
            if len(auth_groups) == 0:
                instance.groups.set([])
            else:
                if instance.is_valid_auth_group(*auth_groups):
                    instance.groups.set(Group.objects.filter(name__in=auth_groups))
                else:
                    self._errors.update({'auth_groups': [INVALID_AUTH_GROUP_NAME]})
                    return instance

        instance.save()
        if instance.id:
            instance.set_checksums()
        return instance
