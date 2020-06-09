from django.core.validators import RegexValidator
from rest_framework import serializers

from core.common.constants import NAMESPACE_REGEX
from .models import UserProfile


class UserListSerializer(serializers.ModelSerializer):

    class Meta:
        model = UserProfile
        extra_kwargs = {
            'url': {'view_name': 'userprofile-detail', 'lookup_field': 'user'},
        }
        fields = (
            'username', 'name', 'url'
        )


class UserCreateSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='resource_type', read_only=True)
    uuid = serializers.CharField(source='id', read_only=True)
    username = serializers.CharField(required=True, validators=[RegexValidator(regex=NAMESPACE_REGEX)])
    name = serializers.CharField(required=True)
    email = serializers.CharField(required=True)
    password = serializers.CharField(required=False)
    company = serializers.CharField(required=False)
    location = serializers.CharField(required=False)
    preferred_locale = serializers.CharField(required=False)
    orgs = serializers.IntegerField(read_only=True)
    public_collections = serializers.IntegerField(read_only=True)
    public_sources = serializers.IntegerField(read_only=True)
    created_on = serializers.DateTimeField(source='created_at', read_only=True)
    updated_on = serializers.DateTimeField(source='updated_at', read_only=True)
    created_by = serializers.CharField(read_only=True)
    updated_by = serializers.CharField(read_only=True)
    url = serializers.CharField(read_only=True)
    extras = serializers.Field(required=False)

    class Meta:
        model = UserProfile
        fields = (
            'type', 'uuid', 'username', 'name', 'email', 'company', 'location', 'preferred_locale', 'orgs',
            'public_collections', 'public_sources', 'created_on', 'updated_on', 'created_by', 'updated_by',
            'url', 'extras',
        )

    def restore_object(self, attrs, _=None):
        request_user = self.context['request'].user
        username = attrs.get('username')
        if UserProfile.objects.filter(username=username).exists():
            self._errors['username'] = 'User with username %s already exists.' % username
            return None
        email = attrs.get('email')
        profile = UserProfile(first_name=attrs.get('name'), username=username, email=email)
        profile.created_by = request_user
        profile.updated_by = request_user
        profile.password = attrs.get('hashed_password', None)
        profile.company = attrs.get('company', None)
        profile.location = attrs.get('location', None)
        profile.preferred_locale = attrs.get('preferred_locale', None)
        profile.extras = attrs.get('extras', None)
        return profile


class UserDetailSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='resource_type', read_only=True)
    uuid = serializers.CharField(source='id', read_only=True)
    username = serializers.CharField(required=False)
    name = serializers.CharField(required=False)
    email = serializers.CharField(required=False)
    company = serializers.CharField(required=False)
    location = serializers.CharField(required=False)
    preferred_locale = serializers.CharField(required=False)
    orgs = serializers.IntegerField(read_only=True)
    public_collections = serializers.IntegerField(read_only=True)
    public_sources = serializers.IntegerField(read_only=True)
    created_on = serializers.DateTimeField(source='created_at', read_only=True)
    updated_on = serializers.DateTimeField(source='updated_at', read_only=True)
    created_by = serializers.CharField(read_only=True)
    updated_by = serializers.CharField(read_only=True)
    url = serializers.URLField(read_only=True)
    organizations_url = serializers.URLField(read_only=True)
    extras = serializers.Field(required=False)

    class Meta:
        model = UserProfile
        fields = (
            'type', 'uuid', 'username', 'name', 'email', 'company', 'location', 'preferred_locale', 'orgs',
            'public_collections', 'public_sources', 'created_on', 'updated_on', 'created_by', 'updated_by',
            'url', 'organizations_url', 'extras',
        )

    def restore_object(self, attrs, instance=None):
        request_user = self.context['request'].user
        instance.email = attrs.get('email', instance.email)
        instance.username = attrs.get('mnemonic', instance.username)
        instance.full_name = attrs.get('full_name', instance.full_name)
        instance.company = attrs.get('company', instance.company)
        instance.location = attrs.get('location', instance.location)
        instance.mnemonic = attrs.get('mnemonic', instance.mnemonic)
        instance.preferred_locale = attrs.get('preferred_locale', instance.preferred_locale)
        instance.extras = attrs.get('extras', instance.extras)
        instance.updated_by = request_user
        return instance
