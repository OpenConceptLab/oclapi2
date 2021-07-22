from datetime import datetime

from dateutil.relativedelta import relativedelta
from django.contrib import admin
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, F
from django.db.models.functions import TruncMonth
from django.urls import reverse
from rest_framework.authtoken.models import Token

from core.common.constants import HEAD
from core.common.mixins import SourceContainerMixin
from core.common.models import BaseModel, CommonLogoModel
from core.common.tasks import send_user_verification_email, send_user_reset_password_email
from core.common.utils import web_url
from core.users.constants import AUTH_GROUPS
from .constants import USER_OBJECT_TYPE


class UserProfile(AbstractUser, BaseModel, CommonLogoModel, SourceContainerMixin):
    class Meta:
        db_table = 'user_profiles'
        swappable = 'AUTH_USER_MODEL'
        indexes = [] + BaseModel.Meta.indexes

    OBJECT_TYPE = USER_OBJECT_TYPE
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    organizations = models.ManyToManyField('orgs.Organization', related_name='members')
    company = models.TextField(null=True, blank=True)
    location = models.TextField(null=True, blank=True)
    preferred_locale = models.TextField(null=True, blank=True)
    website = models.TextField(null=True, blank=True)
    verified = models.BooleanField(default=True)
    verification_token = models.TextField(null=True, blank=True)
    mnemonic_attr = 'username'

    es_fields = {
        'username': {'sortable': True, 'filterable': True, 'exact': True},
        'date_joined': {'sortable': True, 'default': 'asc', 'filterable': False},
        'company': {'sortable': True, 'filterable': True, 'exact': True},
        'location': {'sortable': True, 'filterable': True, 'exact': True},
        'is_superuser': {'sortable': False, 'filterable': True, 'exact': False, 'facet': True},
        'is_staff': {'sortable': False, 'filterable': False, 'exact': False, 'facet': True},
        'is_admin': {'sortable': False, 'filterable': False, 'exact': False, 'facet': True}
    }

    @property
    def user(self):
        return self.username

    @property
    def name(self):
        return "{} {}".format(self.first_name, self.last_name)

    @property
    def full_name(self):
        return self.name

    @property
    def mnemonic(self):
        return self.username

    @staticmethod
    def get_url_kwarg():
        return 'user'

    @property
    def organizations_url(self):
        return reverse('userprofile-orgs', kwargs={'user': self.mnemonic})

    def update_password(self, password=None, hashed_password=None):
        if not password and not hashed_password:
            return None

        if password:
            try:
                validate_password(password)
                self.set_password(password)
            except ValidationError as ex:
                return dict(errors=ex.messages)
        elif hashed_password:
            self.password = hashed_password

        if self.verification_token:
            self.verification_token = None
        self.save()
        self.refresh_token()
        return None

    def refresh_token(self):
        self.__delete_token()
        self.__create_token()

    def get_token(self):
        token = Token.objects.filter(user_id=self.id).first() or self.__create_token()
        return token.key

    def set_token(self, token):
        self.__delete_token()
        Token.objects.create(user=self, key=token)

    def is_admin_for(self, concept_container):  # pragma: no cover
        parent_id = concept_container.parent_id
        return parent_id == self.id or self.organizations.filter(id=parent_id).exists()

    def __create_token(self):
        return Token.objects.create(user=self)

    def __delete_token(self):
        return Token.objects.filter(user=self).delete()

    @property
    def orgs_count(self):
        return self.organizations.count()

    def send_verification_email(self):
        return send_user_verification_email.delay(self.id)

    def send_reset_password_email(self):
        return send_user_reset_password_email.delay(self.id)

    @property
    def email_verification_url(self):
        return "{}/#/accounts/{}/verify/{}/".format(web_url(), self.username, self.verification_token)

    @property
    def reset_password_url(self):
        return "{}/#/accounts/{}/password/reset/{}/".format(web_url(), self.username, self.verification_token)

    def mark_verified(self, token):
        if self.verified:
            return True

        if token == self.verification_token:
            self.verified = True
            self.verification_token = None
            self.save()
            return True

        return False

    @staticmethod
    def is_valid_auth_group(*names):
        return all(name in AUTH_GROUPS for name in names)

    @property
    def auth_groups(self):
        return self.groups.values_list('name', flat=True)


class UserReport:  # pragma: no cover
    def __init__(self, verbose=False, start=None, end=None):
        self.verbose = verbose
        now = datetime.now()
        self.start = start or (now - relativedelta(months=6))
        self.end = end or now
        self.total = 0
        self.active = 0
        self.inactive = 0
        self.joining_monthly_distribution = None
        self.last_login_monthly_distribution = None
        self.organizations_created_by_month = None
        self.sources_created_by_month = None
        self.source_versions_created_by_month = None
        self.collections_created_by_month = None
        self.collection_versions_created_by_month = None
        self.collection_references_created_by_month = None
        self.concepts_created_by_month = None
        self.mappings_created_by_month = None
        self.result = dict()
        self.queryset = self.set_date_range(UserProfile.objects)

    def set_date_range(self, queryset):
        return queryset.filter(created_at__gte=self.start, created_at__lte=self.end)

    def set_total(self):
        self.total = self.queryset.count()

    def set_active(self):
        self.active = self.queryset.filter(is_active=True).count()

    def set_inactive(self):
        self.inactive = self.queryset.filter(is_active=False).count()

    def set_joining_monthly_distribution(self):
        self.joining_monthly_distribution = self.get_distribution(self.queryset, 'created_at', 'username')

    def set_last_login_monthly_distribution(self):
        self.last_login_monthly_distribution = self.get_distribution(self.queryset, 'last_login', 'username')

    def set_mappings_created_by_month(self):
        from core.mappings.models import Mapping
        self.mappings_created_by_month = self.get_distribution(Mapping.objects.filter(id=F('versioned_object_id')))

    def set_concepts_created_by_month(self):
        from core.concepts.models import Concept
        self.concepts_created_by_month = self.get_distribution(Concept.objects.filter(id=F('versioned_object_id')))

    def set_collections_created_by_month(self):
        from core.collections.models import Collection
        self.collections_created_by_month = self.get_distribution(Collection.objects.filter(version=HEAD))

    def set_sources_created_by_month(self):
        from core.sources.models import Source
        self.sources_created_by_month = self.get_distribution(Source.objects.filter(version=HEAD))

    def set_source_versions_created_by_month(self):
        from core.sources.models import Source
        self.source_versions_created_by_month = self.get_distribution(Source.objects.exclude(version=HEAD))

    def set_collection_versions_created_by_month(self):
        from core.collections.models import Collection
        self.collection_versions_created_by_month = self.get_distribution(Collection.objects.exclude(version=HEAD))

    def set_collection_references_created_by_month(self):
        from core.collections.models import CollectionReference
        self.collection_references_created_by_month = self.get_distribution(
            CollectionReference.objects.filter(collections__version=HEAD))

    def set_organizations_created_by_month(self):
        from core.orgs.models import Organization
        self.organizations_created_by_month = self.get_distribution(Organization.objects)

    def get_distribution(self, queryset, date_attr='created_at', count_by='id'):
        return self.set_date_range(queryset).annotate(
            month=TruncMonth(date_attr)
        ).filter(
            month__gte=self.start, month__lte=self.end
        ).values('month').annotate(total=Count(count_by)).values('month', 'total').order_by('-month')

    @staticmethod
    def __format_distribution(queryset):
        formatted = list()
        for item in queryset:
            month = item['month']
            if month:
                result = dict()
                result[item['month'].strftime('%b %Y')] = item['total']
                formatted.append(result)

        return formatted

    def prepare(self):
        self.set_total()
        self.set_active()
        self.set_inactive()
        self.set_joining_monthly_distribution()
        self.set_last_login_monthly_distribution()
        self.set_organizations_created_by_month()
        self.set_sources_created_by_month()
        self.set_collections_created_by_month()
        if self.verbose:
            self.set_source_versions_created_by_month()
            self.set_collection_versions_created_by_month()
            self.set_collection_references_created_by_month()
            self.set_concepts_created_by_month()
            self.set_mappings_created_by_month()

    def make_result(self):
        self.result = dict(
            total=self.total,
            active=self.active,
            inactive=self.inactive,
            new_users=self.__format_distribution(self.joining_monthly_distribution),
            users_last_login=self.__format_distribution(self.last_login_monthly_distribution),
            new_organizations=self.__format_distribution(self.organizations_created_by_month),
            new_sources=self.__format_distribution(self.sources_created_by_month),
            new_collections=self.__format_distribution(self.collections_created_by_month),
        )
        if self.verbose:
            self.result['new_source_versions'] = self.__format_distribution(
                self.source_versions_created_by_month)
            self.result['new_collection_versions'] = self.__format_distribution(
                self.collection_versions_created_by_month)
            self.result['new_collection_references'] = self.__format_distribution(
                self.collection_references_created_by_month)
            self.result['new_concepts'] = self.__format_distribution(
                self.concepts_created_by_month)
            self.result['new_mappings'] = self.__format_distribution(
                self.mappings_created_by_month)

    def generate(self):
        self.prepare()
        self.make_result()


admin.site.register(UserProfile)
