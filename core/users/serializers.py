import uuid

from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from pydash import get
from rest_framework import serializers
from rest_framework.fields import IntegerField
from rest_framework.validators import UniqueValidator

from core.common.constants import NAMESPACE_REGEX, INCLUDE_SUBSCRIBED_ORGS, INCLUDE_VERIFICATION_TOKEN, \
    INCLUDE_AUTH_GROUPS
from core.users.constants import INVALID_AUTH_GROUP_NAME
from .models import UserProfile


class UserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = (
            'username', 'name', 'url'
        )


class UserSummarySerializer(serializers.ModelSerializer):
    sources = IntegerField(source='public_sources')
    collections = IntegerField(source='public_collections')
    organizations = IntegerField(source='orgs_count')

    class Meta:
        model = UserProfile
        fields = (
            'username', 'name', 'url', 'logo_url', 'sources', 'collections', 'organizations',
            'is_superuser', 'is_staff', 'first_name', 'last_name', 'status'
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
            'type', 'uuid', 'username', 'name', 'email', 'company', 'location', 'preferred_locale', 'orgs',
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
            username=username, email=validated_data.get('email'), company=validated_data.get('company', None),
            location=validated_data.get('location', None), extras=validated_data.get('extras', None),
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
        user.token = user.get_token()
        user.send_verification_email()

        return user


class UserDetailSerializer(serializers.ModelSerializer):
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
    preferred_locale = serializers.CharField(required=False)
    orgs = serializers.IntegerField(read_only=True, source='orgs_count')
    owned_orgs = serializers.IntegerField(read_only=True, source='owned_orgs_count')
    sources = serializers.IntegerField(read_only=True, source='all_sources_count')
    collections = serializers.IntegerField(read_only=True, source='all_collections_count')
    created_on = serializers.DateTimeField(source='created_at', read_only=True)
    updated_on = serializers.DateTimeField(source='updated_at', read_only=True)
    created_by = serializers.CharField(read_only=True)
    updated_by = serializers.CharField(read_only=True)
    extras = serializers.JSONField(required=False, allow_null=True)
    subscribed_orgs = serializers.SerializerMethodField()
    auth_groups = serializers.ListField(required=False, allow_null=True, allow_empty=True)
    deactivated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = UserProfile
        fields = (
            'type', 'uuid', 'username', 'name', 'email', 'company', 'location', 'preferred_locale', 'orgs',
            'public_collections', 'public_sources', 'created_on', 'updated_on', 'created_by', 'updated_by',
            'url', 'organizations_url', 'extras', 'sources_url', 'collections_url', 'website', 'last_login',
            'logo_url', 'subscribed_orgs', 'is_superuser', 'is_staff', 'first_name', 'last_name', 'verified',
            'verification_token', 'date_joined', 'auth_groups', 'status', 'deactivated_at',
            'sources', 'collections', 'owned_orgs'
        )

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')
        self.query_params = params.dict() if params else {}
        self.include_subscribed_orgs = self.query_params.get(INCLUDE_SUBSCRIBED_ORGS) in ['true', True]
        self.include_verification_token = self.query_params.get(INCLUDE_VERIFICATION_TOKEN) in ['true', True]
        self.include_auth_groups = self.query_params.get(INCLUDE_AUTH_GROUPS) in ['true', True]

        if not self.include_subscribed_orgs:
            self.fields.pop('subscribed_orgs')
        if not self.include_verification_token:
            self.fields.pop('verification_token')
        if not self.include_auth_groups:
            self.fields.pop('auth_groups')

        super().__init__(*args, **kwargs)

    def get_subscribed_orgs(self, obj):
        if self.include_subscribed_orgs:
            from core.orgs.serializers import OrganizationListSerializer
            return OrganizationListSerializer(obj.organizations.all(), many=True).data

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
                    self._errors.update(dict(auth_groups=[INVALID_AUTH_GROUP_NAME]))
                    return instance

        instance.save()
        return instance
