from django.core.validators import RegexValidator
from pydash import get
from rest_framework import serializers
from rest_framework.fields import SerializerMethodField

from core.client_configs.serializers import ClientConfigSerializer
from core.common.constants import NAMESPACE_REGEX, ACCESS_TYPE_CHOICES, DEFAULT_ACCESS_TYPE, INCLUDE_CLIENT_CONFIGS
from .models import Organization


class OrganizationListSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source='mnemonic')

    class Meta:
        model = Organization
        fields = ('id', 'name', 'url')


class OrganizationCreateSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='resource_type', read_only=True)
    uuid = serializers.CharField(source='id', read_only=True)
    id = serializers.CharField(required=True, validators=[RegexValidator(regex=NAMESPACE_REGEX)], source='mnemonic')
    public_access = serializers.ChoiceField(required=False, choices=ACCESS_TYPE_CHOICES, default=DEFAULT_ACCESS_TYPE)
    name = serializers.CharField(required=True)
    company = serializers.CharField(required=False, allow_blank=True)
    website = serializers.CharField(required=False, allow_blank=True)
    location = serializers.CharField(required=False, allow_blank=True)
    members = serializers.IntegerField(source='num_members', read_only=True)
    created_on = serializers.DateTimeField(source='created_at', read_only=True)
    updated_on = serializers.DateTimeField(source='updated_at', read_only=True)
    url = serializers.CharField(read_only=True)
    extras = serializers.JSONField(required=False, allow_null=True)
    public_sources = serializers.IntegerField(read_only=True)

    class Meta:
        model = Organization
        fields = (
            'type', 'uuid', 'id', 'public_access', 'name', 'company', 'website', 'location', 'members',
            'created_on', 'updated_on', 'url', 'extras', 'public_sources', 'description', 'text'
        )

    def prepare_object(self, validated_data):
        user = self.context['request'].user
        mnemonic = validated_data.get('mnemonic', None)
        if Organization.objects.filter(mnemonic=mnemonic).exists():
            self._errors['mnemonic'] = f'Organization with mnemonic {mnemonic} already exists.'
            return Organization()
        organization = Organization(name=validated_data.get('name'), mnemonic=mnemonic)
        organization.created_by = user
        organization.updated_by = user
        organization.public_access = validated_data.get('public_access', DEFAULT_ACCESS_TYPE)
        organization.company = validated_data.get('company', None)
        organization.website = validated_data.get('website', None)
        organization.location = validated_data.get('location', None)
        organization.extras = validated_data.get('extras', None)
        organization.description = validated_data.get('description', None)
        organization.text = validated_data.get('text', None)
        return organization

    def create(self, validated_data):
        organization = self.prepare_object(validated_data)
        if not self._errors:
            organization.save()
        return organization


class OrganizationDetailSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='resource_type', read_only=True)
    uuid = serializers.CharField(source='id', read_only=True)
    id = serializers.CharField(source='mnemonic', read_only=True)
    public_access = serializers.ChoiceField(required=False, choices=ACCESS_TYPE_CHOICES, default=DEFAULT_ACCESS_TYPE)
    name = serializers.CharField(required=False)
    company = serializers.CharField(required=False, allow_blank=True)
    website = serializers.CharField(required=False, allow_blank=True)
    location = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    description = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    members = serializers.IntegerField(source='num_members', read_only=True)
    created_on = serializers.DateTimeField(source='created_at', read_only=True)
    updated_on = serializers.DateTimeField(source='updated_at', read_only=True)
    created_by = serializers.CharField(read_only=True)
    updated_by = serializers.CharField(read_only=True)
    url = serializers.URLField(read_only=True)
    members_url = serializers.URLField(read_only=True)
    extras = serializers.JSONField(required=False, allow_null=True)
    client_configs = SerializerMethodField()

    class Meta:
        model = Organization
        fields = (
            'type', 'uuid', 'id', 'public_access', 'name', 'company', 'website', 'location', 'members',
            'created_on', 'updated_on', 'url', 'extras', 'members_url', 'created_by', 'updated_by', 'location',
            'sources_url', 'public_sources', 'collections_url', 'public_collections', 'logo_url', 'description',
            'client_configs', 'text',
        )

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')
        self.query_params = params.dict() if params else {}
        self.include_client_configs = self.query_params.get(INCLUDE_CLIENT_CONFIGS) in ['true', True]

        if not self.include_client_configs:
            self.fields.pop('client_configs')

        super().__init__(*args, **kwargs)

    def get_client_configs(self, obj):
        if self.include_client_configs:
            return ClientConfigSerializer(obj.client_configs.filter(is_active=True), many=True).data

        return None

    def update(self, instance, validated_data):
        request_user = self.context['request'].user
        instance.description = validated_data.get('description', instance.description)
        instance.public_access = validated_data.get('public_access', instance.public_access)
        instance.name = validated_data.get('name', instance.name)
        instance.company = validated_data.get('company', instance.company)
        instance.website = validated_data.get('website', instance.website)
        instance.location = validated_data.get('location', instance.website)
        instance.extras = validated_data.get('extras', instance.extras)
        instance.text = validated_data.get('text', None)
        instance.updated_by = request_user
        instance.save()
        return instance
