from django.db.models import F

from core.reports.models import AbstractReport
from core.concepts.models import Concept


class ConceptReport(AbstractReport):
    queryset = Concept.objects.filter(id=F('versioned_object_id'))
    name = 'Concepts'
    id = 'concepts'
    verbose = False
    retired_criteria = {'retired': True}
    note = 'Equivalent of latest concept version'


class ConceptVersionReport(AbstractReport):
    queryset = Concept.objects.exclude(id=F('versioned_object_id')).exclude(is_latest_version=True)
    name = 'Concept Versions'
    id = 'concept_versions'
    verbose = False
    retired_criteria = {'retired': True}
    note = 'Excludes latest concept version'
