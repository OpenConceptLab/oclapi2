from django.core.validators import RegexValidator
from rest_framework import serializers

from core.common.constants import NAMESPACE_REGEX, ACCESS_TYPE_CHOICES, DEFAULT_ACCESS_TYPE
from .models import Organization


class OrganizationListSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source='mnemonic')

    class Meta:
        model = Organization
        fields = ('id', 'name', 'url')
        extra_kwargs = {
            'url': {'view_name': 'organization-detail', 'lookup_field': 'org'},
        }


class OrganizationCreateSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='resource_type', read_only=True)
    uuid = serializers.CharField(source='id', read_only=True)
    id = serializers.CharField(required=True, validators=[RegexValidator(regex=NAMESPACE_REGEX)], source='mnemonic')
    public_access = serializers.ChoiceField(required=False, choices=ACCESS_TYPE_CHOICES, default=DEFAULT_ACCESS_TYPE)
    name = serializers.CharField(required=True)
    company = serializers.CharField(required=False)
    website = serializers.CharField(required=False)
    location = serializers.CharField(required=False)
    members = serializers.IntegerField(source='num_members', read_only=True)
    created_on = serializers.DateTimeField(source='created_at', read_only=True)
    updated_on = serializers.DateTimeField(source='updated_at', read_only=True)
    url = serializers.CharField(read_only=True)
    extras = serializers.Field(required=False)

    class Meta:
        model = Organization
        fields = (
            'type', 'uuid', 'id', 'public_access', 'name', 'company', 'website', 'location', 'members',
            'created_on', 'updated_on', 'url', 'extras'
        )

    def restore_object(self, attrs, _=None):
        request_user = self.context['request'].user
        mnemonic = attrs.get('mnemonic', None)
        if Organization.objects.filter(mnemonic=mnemonic).exists():
            self._errors['mnemonic'] = 'Organization with mnemonic %s already exists.' % mnemonic
            return None
        organization = Organization(name=attrs.get('name'), mnemonic=mnemonic)
        organization.created_by = request_user
        organization.updated_by = request_user
        organization.public_access = attrs.get('public_access', DEFAULT_ACCESS_TYPE)
        organization.company = attrs.get('company', None)
        organization.website = attrs.get('website', None)
        organization.location = attrs.get('location', None)
        organization.extras = attrs.get('extras', None)
        return organization


class OrganizationDetailSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='resource_type', read_only=True)
    uuid = serializers.CharField(source='id', read_only=True)
    id = serializers.CharField(source='mnemonic', read_only=True)
    public_access = serializers.ChoiceField(required=False, choices=ACCESS_TYPE_CHOICES, default=DEFAULT_ACCESS_TYPE)
    name = serializers.CharField(required=False)
    company = serializers.CharField(required=False)
    website = serializers.CharField(required=False)
    location = serializers.CharField(required=False)
    members = serializers.IntegerField(source='num_members', read_only=True)
    created_on = serializers.DateTimeField(source='created_at', read_only=True)
    updated_on = serializers.DateTimeField(source='updated_at', read_only=True)
    created_by = serializers.CharField(read_only=True)
    updated_by = serializers.CharField(read_only=True)
    url = serializers.URLField(read_only=True)
    members_url = serializers.URLField(read_only=True)
    extras = serializers.Field(required=False)

    class Meta:
        model = Organization
        fields = (
            'type', 'uuid', 'id', 'public_access', 'name', 'company', 'website', 'location', 'members',
            'created_on', 'updated_on', 'url', 'extras', 'members_url', 'created_by', 'updated_by', 'location',
        )

    def restore_object(self, attrs, instance=None):
        request_user = self.context['request'].user
        instance.public_access = attrs.get('public_access', instance.public_access)
        instance.name = attrs.get('name', instance.name)
        instance.company = attrs.get('company', instance.company)
        instance.website = attrs.get('website', instance.website)
        instance.location = attrs.get('location', instance.website)
        instance.extras = attrs.get('extras', instance.extras)
        instance.updated_by = request_user
        return instance
