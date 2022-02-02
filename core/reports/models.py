from dateutil.relativedelta import relativedelta
from django.db.models import Count, F
from django.db.models.functions import TruncMonth
from django.utils import timezone
from pydash import get

from core.collections.models import Collection, CollectionReference
from core.common.constants import HEAD
from core.concepts.models import Concept
from core.mappings.models import Mapping
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile


class MonthlyUsageReport:
    def __init__(self, verbose=False, start=None, end=None):
        self.verbose = verbose
        self.start = start
        self.end = end
        self.resources = []
        self.result = {}
        self.make_resources()

    def make_resources(self):
        self.resources.append(UserReport(start=self.start, end=self.end, verbose=self.verbose))
        self.resources.append(OrganizationReport(start=self.start, end=self.end, verbose=self.verbose))
        self.resources.append(SourceReport(start=self.start, end=self.end, verbose=self.verbose))
        self.resources.append(CollectionReport(start=self.start, end=self.end, verbose=self.verbose))
        if self.verbose:
            self.resources.append(SourceVersionReport(start=self.start, end=self.end, verbose=self.verbose))
            self.resources.append(CollectionVersionReport(start=self.start, end=self.end, verbose=self.verbose))
            self.resources.append(CollectionReferenceReport(start=self.start, end=self.end, verbose=self.verbose))
            self.resources.append(ConceptReport(start=self.start, end=self.end, verbose=self.verbose))
            self.resources.append(MappingReport(start=self.start, end=self.end, verbose=self.verbose))

    def prepare(self):
        self.result['start'] = self.resources[0].start
        self.result['end'] = self.resources[0].end
        for resource in self.resources:
            self.result[resource.resource] = resource.get_monthly_report()


class ResourceReport:
    queryset = None
    resource = None
    pk = 'mnemonic'

    def __init__(self, start=None, end=None, verbose=False, instance_ids=None):
        self.verbose = verbose
        now = timezone.now()
        self.start = start or (now - relativedelta(months=6)).date()
        self.end = end or now.date()
        self.total = 0
        self.active = 0
        self.inactive = 0
        self.instance_ids = instance_ids
        self.instances = self.get_instances()
        self.created_monthly_distribution = None
        self.result = {}
        self.set_date_range()

    def get_instances(self):
        if self.instance_ids:
            return self.queryset.filter(**{f"{self.pk}__in": self.instance_ids})

        return None

    @staticmethod
    def get_active_filter(active=True):
        return dict(retired=not active)

    def set_date_range(self):
        self.queryset = self.queryset.filter(created_at__gte=self.start, created_at__lte=self.end)

    def set_total(self):
        self.total = self.queryset.count()

    def set_active(self):
        self.active = self.queryset.filter(**self.get_active_filter()).count()

    def set_inactive(self):
        self.inactive = self.queryset.filter(**self.get_active_filter(False)).count()

    def set_created_monthly_distribution(self):
        self.created_monthly_distribution = self.get_distribution()

    def get_distribution(self, date_attr='created_at', count_by='id'):
        return self.queryset.annotate(
            month=TruncMonth(date_attr)
        ).filter(
            month__gte=self.start, month__lte=self.end
        ).values('month').annotate(total=Count(count_by)).values('month', 'total').order_by('-month')

    def get_monthly_report(self):
        self.set_total()
        self.set_created_monthly_distribution()

        self.result = dict(
            total=self.total, created_monthly=self.format_distribution(self.created_monthly_distribution)
        )
        if self.resource not in ['collection_references']:
            self.set_active()
            self.set_inactive()
            self.result['active'] = self.active
            self.result['inactive'] = self.inactive
        return self.result

    @staticmethod
    def format_distribution(queryset):
        formatted = []
        for item in queryset:
            month = item['month']
            if month:
                formatted.append({item['month'].strftime('%b %Y'): item['total']})

        return formatted


class UserReport(ResourceReport):
    queryset = UserProfile.objects
    resource = 'users'
    pk = 'username'

    def __init__(self, start=None, end=None, verbose=False, instance_ids=None):
        super().__init__(start, end, verbose, instance_ids)
        self.last_login_monthly_distribution = None

    @staticmethod
    def get_active_filter(active=True):
        return dict(is_active=active)

    def set_last_login_monthly_distribution(self):
        self.last_login_monthly_distribution = self.get_distribution('last_login')

    def get_monthly_report(self):
        self.result = super().get_monthly_report()
        self.set_last_login_monthly_distribution()
        self.result['last_login_monthly'] = self.format_distribution(self.last_login_monthly_distribution)
        return self.result

    def get_authoring_report(self):
        if self.instances is not None:
            for user in self.instances:
                user_result = {}
                for app, model in [
                        ('concepts', 'concept'), ('mappings', 'mapping'), ('sources', 'source'),
                        ('collections', 'collection'), ('users', 'userprofile'), ('orgs', 'organization')
                ]:
                    created = get(user, f"{app}_{model}_related_created_by").count()
                    updated = get(user, f"{app}_{model}_related_updated_by").count()
                    user_result[app] = dict(created=created, updated=updated)

                self.result[user.username] = user_result

        return self.result


class OrganizationReport(ResourceReport):
    queryset = Organization.objects
    resource = 'organizations'

    @staticmethod
    def get_active_filter(active=True):
        return dict(is_active=active)


class SourceReport(ResourceReport):
    queryset = Source.objects.filter(version=HEAD)
    resource = 'sources'


class CollectionReport(ResourceReport):
    queryset = Collection.objects.filter(version=HEAD)
    resource = 'collections'


class SourceVersionReport(ResourceReport):
    queryset = Source.objects.exclude(version=HEAD)
    resource = 'source_versions'


class CollectionVersionReport(ResourceReport):
    queryset = Collection.objects.exclude(version=HEAD)
    resource = 'collection_versions'


class CollectionReferenceReport(ResourceReport):
    queryset = CollectionReference.objects.filter(collections__version=HEAD)
    resource = 'collection_references'


class ConceptReport(ResourceReport):
    queryset = Concept.objects.filter(id=F('versioned_object_id'))
    resource = 'concepts'


class MappingReport(ResourceReport):
    queryset = Mapping.objects.filter(id=F('versioned_object_id'))
    resource = 'mappings'
