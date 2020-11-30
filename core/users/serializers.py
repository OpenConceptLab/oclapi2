from django.core.validators import RegexValidator
from rest_framework import serializers

from core.common.constants import NAMESPACE_REGEX
from .models import UserProfile


class UserListSerializer(serializers.ModelSerializer):

    class Meta:
        model = UserProfile
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
    hashed_password = serializers.CharField(required=False)
    company = serializers.CharField(required=False)
    location = serializers.CharField(required=False)
    preferred_locale = serializers.CharField(required=False)
    orgs = serializers.IntegerField(read_only=True, source='orgs_count')
    created_on = serializers.DateTimeField(source='created_at', read_only=True)
    updated_on = serializers.DateTimeField(source='updated_at', read_only=True)
    created_by = serializers.CharField(read_only=True)
    updated_by = serializers.CharField(read_only=True)
    extras = serializers.JSONField(required=False, allow_null=True)
    token = serializers.CharField(required=False, read_only=True)

    class Meta:
        model = UserProfile
        extra_kwargs = {'password': {'write_only': True}, 'hashed_password': {'write_only': True}}
        fields = (
            'type', 'uuid', 'username', 'name', 'email', 'company', 'location', 'preferred_locale', 'orgs',
            'public_collections', 'public_sources', 'created_on', 'updated_on', 'created_by', 'updated_by',
            'url', 'extras', 'password', 'hashed_password', 'token'
        )

    def create(self, validated_data):
        request_user = self.context['request'].user
        username = validated_data.get('username')
        existing_profile = UserProfile.objects.filter(username=username)
        if existing_profile.exists():
            self._errors['username'] = 'User with username %s already exists.' % username
            profile = existing_profile.first()
            profile.token = profile.get_token()
            return profile
        email = validated_data.get('email')
        profile = UserProfile(first_name=validated_data.get('name'), username=username, email=email)
        profile.created_by = request_user
        profile.updated_by = request_user
        profile.company = validated_data.get('company', None)
        profile.location = validated_data.get('location', None)
        profile.preferred_locale = validated_data.get('preferred_locale', None)
        profile.extras = validated_data.get('extras', None)
        profile.save()
        profile.update_password(validated_data.get('password', None), validated_data.get('hashed_password', None))
        profile.token = profile.get_token()
        return profile


class UserDetailSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='resource_type', read_only=True)
    uuid = serializers.CharField(source='id', read_only=True)
    username = serializers.CharField(required=False)
    name = serializers.CharField(required=False)
    email = serializers.CharField(required=False)
    company = serializers.CharField(required=False)
    website = serializers.CharField(required=False)
    location = serializers.CharField(required=False)
    preferred_locale = serializers.CharField(required=False)
    orgs = serializers.IntegerField(read_only=True, source='orgs_count')
    created_on = serializers.DateTimeField(source='created_at', read_only=True)
    updated_on = serializers.DateTimeField(source='updated_at', read_only=True)
    created_by = serializers.CharField(read_only=True)
    updated_by = serializers.CharField(read_only=True)
    extras = serializers.JSONField(required=False, allow_null=True)

    class Meta:
        model = UserProfile
        fields = (
            'type', 'uuid', 'username', 'name', 'email', 'company', 'location', 'preferred_locale', 'orgs',
            'public_collections', 'public_sources', 'created_on', 'updated_on', 'created_by', 'updated_by',
            'url', 'organizations_url', 'extras', 'sources_url', 'collections_url', 'website',
        )

    def update(self, instance, validated_data):
        request_user = self.context['request'].user
        instance.email = validated_data.get('email', instance.email)
        instance.username = validated_data.get('username', instance.username)
        instance.company = validated_data.get('company', instance.company)
        instance.website = validated_data.get('website', instance.website)
        instance.location = validated_data.get('location', instance.location)
        instance.preferred_locale = validated_data.get('preferred_locale', instance.preferred_locale)
        instance.extras = validated_data.get('extras', instance.extras)
        instance.updated_by = request_user
        instance.save()
        return instance
