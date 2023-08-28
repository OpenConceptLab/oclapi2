import csv
import io
import os
from datetime import datetime

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from pydash import get

from core.common.utils import from_string_to_date, get_date_range_label, cd_temp


class AbstractReport:
    stat_fields = ['active', 'retired', 'count']
    verbose_fields = ['mnemonic', 'name', 'created_by.username', 'created_at']
    retired_criteria = {'is_active': False}
    name = 'Abstract Resources'
    STAT_HEADERS = ["Resource", "Active", "Retired", "Total"]
    VERBOSE_HEADERS = ["ID", "Name", "Created By", "Created At"]
    select_related = []
    queryset = None
    verbose = True
    stats = True
    grouped = False

    def __init__(self, start_date=None, end_date=None):
        self.start_date = from_string_to_date(start_date) if start_date else None
        self.end_date = from_string_to_date(end_date) if end_date else None
        self._active = None
        self._retired = None
        self._count = None
        self.build_queryset()

    def get_overall_report_instance(self):
        return self.__class__()

    def build_queryset(self):
        if self.select_related:
            self.queryset = self.queryset.select_related(*self.select_related)
        if self.start_date:
            self.queryset = self.queryset.filter(created_at__gte=self.start_date)
        if self.end_date:
            self.queryset = self.queryset.filter(created_at__lte=self.end_date)

    @property
    def count(self):
        if self._count is None:
            self._count = self.queryset.count()
        return self._count

    @property
    def retired(self):
        if self._retired is None:
            self._retired = self.queryset.filter(**self.retired_criteria).count()
        return self._retired

    @property
    def active(self):
        if self._active is None:
            self._active = self.count - self.retired
        return self._active

    @property
    def date_range(self):
        return get_date_range_label(self.start_date, self.end_date) if self.start_date and self.end_date else None

    @property
    def label(self):
        date_range = self.date_range
        report_name = self.name
        return f"New {report_name}: {date_range}" if date_range else f"New {report_name} - All Time"

    @staticmethod
    def to_value(value):
        if isinstance(value, datetime):
            return value.strftime('%Y-%m-%d %H:%M:%S')
        return value

    def to_csv_row(self, resource):
        return [self.to_value(get(resource, field)) for field in self.verbose_fields]

    def to_stat_csv_row(self):
        return [self.name, *[get(self, field) for field in self.stat_fields]]


class ResourceUsageReport:
    def __init__(self, start_date, end_date):
        self.start_date = start_date
        self.end_date = end_date
        self.organization = None
        self.user = None
        self.source = None
        self.source_version = None
        self.collection = None
        self.collection_version = None
        self.reference = None
        self.expansion = None
        self.concept = None
        self.concept_version = None
        self.mapping = None
        self.mapping_version = None

    def build(self):
        from core.orgs.reports import OrganizationReport
        from core.users.reports import UserReport
        from core.sources.reports import SourceReport, SourceVersionReport
        from core.collections.reports import CollectionReport, CollectionVersionReport, ExpansionReport, ReferenceReport
        from core.concepts.reports import ConceptReport, ConceptVersionReport
        from core.mappings.reports import MappingReport, MappingVersionReport

        self.organization = OrganizationReport(self.start_date, self.end_date)
        self.user = UserReport(self.start_date, self.end_date)
        self.source = SourceReport(self.start_date, self.end_date)
        self.collection = CollectionReport(self.start_date, self.end_date)
        self.source_version = SourceVersionReport(self.start_date, self.end_date)
        self.collection_version = CollectionVersionReport(self.start_date, self.end_date)
        self.reference = ReferenceReport(self.start_date, self.end_date)
        self.expansion = ExpansionReport(self.start_date, self.end_date)
        self.concept = ConceptReport(self.start_date, self.end_date)
        self.concept_version = ConceptVersionReport(self.start_date, self.end_date)
        self.mapping = MappingReport(self.start_date, self.end_date)
        self.mapping_version = MappingVersionReport(self.start_date, self.end_date)

    @property
    def resources(self):
        return [
            self.organization,
            self.user,
            self.source,
            self.source_version,
            self.collection,
            self.collection_version,
            self.reference,
            self.expansion,
            self.concept,
            self.concept_version,
            self.mapping,
            self.mapping_version
        ]

    def generate(self, write_to_file=False):  # pylint: disable=too-many-locals
        self.build()
        buff = io.StringIO()
        writer = csv.writer(buff, dialect='excel', delimiter=',')
        max_columns = 8
        blank_row = ["", "", "", "", "", "", "", ""]

        def to_row(values):
            return [*values, *blank_row[:max_columns - len(values)]]

        date_range_label = get_date_range_label(self.start_date, self.end_date)
        writer.writerow(
            to_row([f"Resources Created: {date_range_label}"]))
        stat_headers = self.organization.STAT_HEADERS
        writer.writerow(to_row(stat_headers))
        resources = self.resources
        for resource in resources:
            writer.writerow(to_row(resource.to_stat_csv_row()))

        writer.writerow(blank_row)

        for resource in resources:
            if resource.verbose and resource.queryset.exists():
                writer.writerow(to_row([resource.label]))
                writer.writerow(to_row(resource.VERBOSE_HEADERS))
                for obj in resource.queryset.order_by('-created_at'):
                    writer.writerow(to_row(resource.to_csv_row(obj)))

                writer.writerow(blank_row)

        writer.writerow(blank_row)

        for resource in resources:
            if resource.grouped and (
                    resource.grouped_queryset.exists() if isinstance(
                        resource.grouped_queryset, QuerySet
                    ) else len(resource.grouped_queryset) > 0
            ):
                writer.writerow(to_row([f"{resource.grouped_label}: {date_range_label}"]))
                writer.writerow(to_row(resource.GROUPED_HEADERS))
                for obj in resource.grouped_queryset:
                    writer.writerow(to_row(resource.to_grouped_stat_csv_row(obj)))

        writer.writerow(blank_row)

        writer.writerow(to_row(["Overall Resources"]))
        writer.writerow(to_row(stat_headers))
        for resource in resources:
            writer.writerow(to_row(resource.get_overall_report_instance().to_stat_csv_row()))

        buff2 = io.BytesIO(buff.getvalue().encode())
        now = timezone.now().strftime("%Y-%m-%d-%H-%M")
        filename = f'{settings.ENV.lower()}_resource_report_{now}.csv'
        if write_to_file:
            cwd = cd_temp()
            with open(filename, 'wb') as file:
                file.write(buff2.getvalue())
            os.chdir(cwd)
            return file.name
        return buff2, filename
