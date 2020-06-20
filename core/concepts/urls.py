from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.ConceptListView.as_view(), name='concept-list'),
    re_path(
        r'^(?P<concept>' + NAMESPACE_PATTERN + ')/$',
        views.ConceptRetrieveUpdateDestroyView.as_view(),
        name='concept-detail'
    ),
]
