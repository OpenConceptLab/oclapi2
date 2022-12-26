import json
from urllib.parse import quote

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db.models import Count, F
from django.db.models.functions import TruncMonth
from django.utils import timezone
from pydash import get

from core.collections.models import Collection, CollectionReference
from core.common.constants import HEAD
from core.common.utils import get_end_of_month
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
        now = timezone.now()
        self.current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        self.current_month_end = get_end_of_month(self.current_month_start).replace(
            hour=23, minute=59, second=59, microsecond=0)
        self.resources = []
        self.current_month_resources = []
        self.result = {}
        self.current_month_result = {}
        self.make_current_month_resources()
        self.make_resources()
        self.make_current_month_resources()

    @staticmethod
    def to_chart_url(label, graph_data, chart_type='bar'):
        labels = []
        data = []
        color = "rgba(51, 115, 170, 1)"
        color_light = "rgba(51, 115, 170, .2)"
        for ele in graph_data:
            key = list(ele.keys())[0]
            labels.append(key)
            data.append(ele[key])

        config = dict(
            type=chart_type,
            data=dict(
                labels=labels,
                datasets=[dict(
                    label=label,
                    data=data,
                    backgroundColor=[color_light],
                    borderColor=[color],
                    borderWidth=1
                )]
            ),
            options=dict(
                scales=dict(y=dict(beginAtZero=True))
            )
        )
        return f'https://quickchart.io/chart?c={quote(json.dumps(config))}'

    def make_resources(self):
        start = self.start
        end = self.end
        self.resources.append(UserReport(start=start, end=end, verbose=self.verbose))
        self.resources.append(OrganizationReport(start=start, end=end, verbose=self.verbose))
        self.resources.append(SourceReport(start=start, end=end, verbose=self.verbose))
        self.resources.append(CollectionReport(start=start, end=end, verbose=self.verbose))
        if self.verbose:
            self.resources.append(SourceVersionReport(start=start, end=end, verbose=self.verbose))
            self.resources.append(CollectionVersionReport(start=start, end=end, verbose=self.verbose))
            self.resources.append(CollectionReferenceReport(start=start, end=end, verbose=self.verbose))
            self.resources.append(ConceptReport(start=start, end=end, verbose=self.verbose))
            self.resources.append(MappingReport(start=start, end=end, verbose=self.verbose))

    def make_current_month_resources(self):
        start = self.current_month_start
        end = self.current_month_end
        self.current_month_resources.append(UserReport(start=start, end=end, verbose=self.verbose))
        self.current_month_resources.append(OrganizationReport(start=start, end=end, verbose=self.verbose))
        self.current_month_resources.append(SourceReport(start=start, end=end, verbose=self.verbose))
        self.current_month_resources.append(CollectionReport(start=start, end=end, verbose=self.verbose))
        if self.verbose:
            self.current_month_resources.append(SourceVersionReport(start=start, end=end, verbose=self.verbose))
            self.current_month_resources.append(CollectionVersionReport(start=start, end=end, verbose=self.verbose))
            self.current_month_resources.append(CollectionReferenceReport(start=start, end=end, verbose=self.verbose))
            self.current_month_resources.append(ConceptReport(start=start, end=end, verbose=self.verbose))
            self.current_month_resources.append(MappingReport(start=start, end=end, verbose=self.verbose))

    def prepare(self):
        self.result['start'] = self.start
        self.result['end'] = self.end
        for resource in self.resources:
            self.result[resource.resource] = resource.get_monthly_report()
        for resource in self.current_month_resources:
            self.current_month_result[resource.resource] = resource.get_monthly_report()

    def get_result_for_email(self):
        urls = {}
        for resource in self.resources:
            entity = resource.resource
            stats = [
                dict(
                    data=self.result[entity]['created_monthly'],
                    label=f"{entity.title()} Created Monthly",
                    key=f"{entity}_url")
            ]

            if entity == 'users':
                stats.append(
                    dict(
                        data=self.result[entity]['last_login_monthly'],
                        label=f"{entity.title()} Joined Monthly",
                        key=f"{entity}_last_login_monthly_url"),
                )
            for stat in stats:
                urls[stat['key']] = self.to_chart_url(stat['label'], stat['data'])
        return {
            **self.result,
            **urls,
            'current_month': self.format_current_month_result(),
            'current_month_start': self.current_month_start,
            'current_month_end': self.current_month_end,
            'env': settings.ENV
        }

    def format_current_month_result(self):
        _result = {}

        def __format(stat):
            return list(stat[0].values())[0] or 0 if stat else 0

        for resource, stats in self.current_month_result.items():
            _result[resource] = {
                **stats,
                'created_monthly': __format(stats['created_monthly']),
            }
        return _result


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
    raw_queryset = UserProfile.objects
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

    def set_date_range(self):
        pass

    def set_created_at_date_range(self):
        self.queryset = self.raw_queryset.filter(created_at__gte=self.start, created_at__lte=self.end)

    def set_last_login_date_range(self):
        self.queryset = self.raw_queryset.filter(last_login__gte=self.start, last_login__lte=self.end)

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
    queryset = CollectionReference.objects.filter(collection__version=HEAD)
    resource = 'collection_references'


class ConceptReport(ResourceReport):
    queryset = Concept.objects.filter(id=F('versioned_object_id'))
    resource = 'concepts'


class MappingReport(ResourceReport):
    queryset = Mapping.objects.filter(id=F('versioned_object_id'))
    resource = 'mappings'
