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
    note = ''
    STAT_HEADERS = ["Resource", "Active", "Retired", "Total"]
    VERBOSE_HEADERS = ["ID", "Name", "Created By", "Created At"]
    select_related = []
    queryset = None
    verbose = True
    stats = True
    grouped = False
    NA = 'N/A'

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
        self.queryset = self.make_queryset(self.queryset)

    def make_queryset(self, queryset):
        if self.select_related:
            queryset = queryset.select_related(*self.select_related)
        if self.start_date:
            queryset = queryset.filter(created_at__gte=self.start_date)
        if self.end_date:
            queryset = queryset.filter(created_at__lte=self.end_date)
        return queryset

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
            self._active = self.count - (0 if self.retired == self.NA else self.retired)
        return self._active

    @property
    def date_range(self):
        return get_date_range_label(self.start_date, self.end_date) if self.start_date and self.end_date else None

    @property
    def label(self):
        date_range = self.date_range
        report_name = self.name
        return f"New {report_name} during Period: {date_range}" if date_range else f"New {report_name} - All Time"

    @staticmethod
    def to_value(value):
        if isinstance(value, datetime):
            return value.strftime('%Y-%m-%d %H:%M:%S')
        return value

    def to_csv_row(self, resource):
        return [self.to_value(get(resource, field)) for field in self.verbose_fields]

    def to_stat_csv_row(self, include_name=True, include_note=False):
        stats = [get(self, field) for field in self.stat_fields]
        if include_name:
            stats = [self.name, *stats]
        if include_note:
            stats = [*stats, self.note]
        return stats


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
        self.repo = None
        self.stats_row = {}

    def build(self):
        from core.orgs.reports import OrganizationReport
        from core.users.reports import UserReport
        from core.sources.reports import SourceReport, SourceVersionReport
        from core.collections.reports import CollectionReport, CollectionVersionReport, ExpansionReport, ReferenceReport
        from core.concepts.reports import ConceptReport, ConceptVersionReport
        from core.mappings.reports import MappingReport, MappingVersionReport
        from core.repos.reports import RepoReport

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
        self.repo = RepoReport(self.start_date, self.end_date)

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

    def get_overall_concept_versions_stats(self):
        return [
            'All Concept Versions',
            *[
                x + y for x, y in zip(
                    self.stats_row['concepts'][1:-1],
                    self.stats_row['concept_versions'][1:-1],
                )
            ]
        ]

    def get_overall_mapping_versions_stats(self):
        return [
            'All Mapping Versions',
            *[
                x + y for x, y in zip(
                    self.stats_row['mappings'][1:-1],
                    self.stats_row['mapping_versions'][1:-1],
                )
            ]
        ]

    @staticmethod
    def _write_grouped_report(resource, writer, to_row, date_range_label):
        if resource.grouped and (
                resource.grouped_queryset.exists() if isinstance(
                    resource.grouped_queryset, QuerySet
                ) else len(resource.grouped_queryset) > 0
        ):
            writer.writerow(to_row([f"{resource.grouped_label}: {date_range_label}"]))
            writer.writerow(to_row(resource.GROUPED_HEADERS))
            for obj in resource.grouped_queryset:
                writer.writerow(to_row(resource.to_grouped_stat_csv_row(obj)))

    @staticmethod
    def _write_verbose_report(resource, writer, to_row):
        writer.writerow(to_row([resource.label]))
        writer.writerow(to_row(resource.VERBOSE_HEADERS))
        for obj in resource.queryset.order_by('-created_at'):
            writer.writerow(to_row(resource.to_csv_row(obj)))

    def generate(self, write_to_file=False):  # pylint: disable=too-many-locals,too-many-statements
        self.build()
        buff = io.StringIO()
        writer = csv.writer(buff, dialect='excel', delimiter=',')
        max_columns = 10
        blank_row = ["" for _ in range(max_columns)]

        def to_row(values):
            return [*values, *blank_row[:max_columns - len(values)]]

        date_range_label = get_date_range_label(self.start_date, self.end_date)
        writer.writerow(to_row(["OCL Usage Report"]))
        writer.writerow(to_row(["Environment", settings.ENV.lower()]))
        writer.writerow(to_row(["Reporting Period", date_range_label]))

        writer.writerow(blank_row)

        writer.writerow(to_row(['Summary by Resource Type']))
        writer.writerow(to_row([
            'Resource',
            'Created during Period',
            'Retired during Period',
            'Subtotal during Period',
            "Active as of Report Date",
            "Retired/Inactive as of Report Date",
            "Total as of Report Date",
            "Notes"
        ]))
        resources = self.resources
        for resource in resources:
            if resource.id == 'mappings':
                writer.writerow(to_row(self.get_overall_concept_versions_stats()))
            stats = [
                *resource.to_stat_csv_row(),
                *resource.get_overall_report_instance().to_stat_csv_row(False, True)
            ]
            writer.writerow(to_row(stats))
            self.stats_row[resource.id] = stats
            if resource.id == 'mapping_versions':
                writer.writerow(to_row(self.get_overall_mapping_versions_stats()))

        writer.writerow(blank_row)

        writer.writerow(to_row([self.repo.summary_label]))
        writer.writerow(to_row(self.repo.SUMMARY_HEADERS))
        for row in self.repo.to_summary_rows():
            writer.writerow(to_row(row))
        writer.writerow(blank_row)

        writer.writerow(to_row([self.reference.summary_label]))
        writer.writerow(to_row(self.reference.SUMMARY_HEADERS))
        writer.writerow(to_row(self.reference.to_summary_row()))
        writer.writerow(blank_row)

        self._write_grouped_report(self.reference, writer, to_row, date_range_label)
        writer.writerow(blank_row)

        writer.writerow(to_row([self.mapping.summary_label]))
        writer.writerow(to_row(self.mapping.SUMMARY_HEADERS))
        mapping_summary_row = self.mapping.to_summary_row()
        mapping_version_summary_row = self.mapping_version.to_summary_row()
        writer.writerow(to_row(mapping_summary_row))
        writer.writerow(to_row(mapping_version_summary_row))
        writer.writerow(to_row(
            [
                'All Mapping Versions',
                *[
                    x + y for x, y in zip(
                        mapping_summary_row[1:],
                        mapping_version_summary_row[1:],
                    )
                ]
            ]
        ))
        writer.writerow(blank_row)

        self._write_grouped_report(self.mapping, writer, to_row, date_range_label)
        writer.writerow(blank_row)

        for resource in resources:
            if resource.verbose and resource.queryset.exists():
                self._write_verbose_report(resource, writer, to_row)
                writer.writerow(blank_row)

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
