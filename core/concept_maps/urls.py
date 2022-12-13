from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.ConceptMapListView.as_view(), name='concept-map-list'),
    re_path(
        fr"^(?P<source>{NAMESPACE_PATTERN})/$",
        views.ConceptMapRetrieveUpdateView.as_view(),
        name='concept-map-detail'
    )
]
