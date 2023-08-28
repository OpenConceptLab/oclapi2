from django.db.models import F

from core.reports.models import AbstractReport
from core.concepts.models import Concept


class ConceptReport(AbstractReport):
    queryset = Concept.objects.filter(id=F('versioned_object_id'))
    name = 'Concepts'
    verbose = False


class ConceptVersionReport(AbstractReport):
    queryset = Concept.objects.exclude(id=F('versioned_object_id')).exclude(is_latest_version=True)
    name = 'Concept Versions'
    verbose = False
