from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db.utils import IntegrityError
from rest_framework import serializers
from rest_framework.fields import SerializerMethodField

from core.common.constants import NAMESPACE_REGEX
from .models import UserProfile, PinnedItem


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
            'url', 'organizations_url', 'extras', 'sources_url', 'collections_url', 'website', 'last_login',
            'logo_url'
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


class UserPinnedItemSerializer(serializers.ModelSerializer):
    resource_type = serializers.CharField(required=True, write_only=True)
    resource_id = serializers.IntegerField(required=True, write_only=True)
    user_id = serializers.IntegerField(required=True)
    organization_id = serializers.IntegerField(required=False, allow_null=True)
    resource = SerializerMethodField()

    class Meta:
        model = PinnedItem
        fields = (
            'id', 'created_at', 'resource_uri', 'user_id', 'organization_id', 'resource_type', 'resource_id', 'resource'
        )
        extra_kwargs = {'resource_type': {'write_only': True}, 'resource_id': {'write_only': True}}

    def create(self, validated_data):
        resource_type = validated_data.get('resource_type', '')
        resource_id = validated_data.get('resource_id', '')
        resource = PinnedItem.get_resource(resource_type, resource_id)
        item = PinnedItem(
            user_id=validated_data.get('user_id'),
            organization_id=validated_data.get('organization_id'),
            resource=resource,
        )

        if not resource:
            self._errors['resource'] = 'Resource type %s with id %s does not exists.' % (resource_type, resource_id)
            return item

        try:
            item.full_clean()
            item.save()
        except (ValidationError, IntegrityError):
            self._errors.update(dict(__all__='This pin already exists.'))

        return item

    @staticmethod
    def get_resource(obj):
        resource = obj.resource
        if not resource:
            return None
        resource_type = resource.resource_type.lower()
        if resource_type == 'source':
            from core.sources.serializers import SourceDetailSerializer
            return SourceDetailSerializer(resource).data
        if resource_type == 'organization':
            from core.orgs.serializers import OrganizationDetailSerializer
            return OrganizationDetailSerializer(resource).data
        if resource_type == 'collection':
            from core.collections.serializers import CollectionDetailSerializer
            return CollectionDetailSerializer(resource).data

        return None
