from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.ConceptListView.as_view(), name='concept-list'),
    re_path(
        r"^(?P<concept>{pattern})/$".format(pattern=NAMESPACE_PATTERN),
        views.ConceptRetrieveUpdateDestroyView.as_view(),
        name='concept-detail'
    ),
    re_path(
        r"^(?P<concept>{pattern})/descriptions/$".format(pattern=NAMESPACE_PATTERN),
        views.ConceptDescriptionListCreateView.as_view(),
        name='concept-descriptions'
    ),
    re_path(
        r'^(?P<concept>{pattern})/descriptions/(?P<id>{pattern})/$'.format(pattern=NAMESPACE_PATTERN),
        views.ConceptDescriptionRetrieveUpdateDestroyView.as_view(),
        name='concept-description'
    ),
    re_path(
        r"^(?P<concept>{pattern})/names/$".format(pattern=NAMESPACE_PATTERN),
        views.ConceptNameListCreateView.as_view(),
        name='concept-names'
    ),
    re_path(
        r'^(?P<concept>{pattern})/names/(?P<id>{pattern})/$'.format(pattern=NAMESPACE_PATTERN),
        views.ConceptNameRetrieveUpdateDestroyView.as_view(),
        name='concept-name'
    ),
    re_path(
        r'^(?P<concept>{pattern})/extras/$'.format(pattern=NAMESPACE_PATTERN),
        views.ConceptExtrasView.as_view(),
        name='concept-extras'
    ),
    re_path(
        r'^(?P<concept>{pattern})/extras/(?P<extra>{pattern})/$'.format(pattern=NAMESPACE_PATTERN),
        views.ConceptExtraRetrieveUpdateDestroyView.as_view(),
        name='concept-extra'
    ),
    re_path(
        r"^(?P<concept>{pattern})/versions/$".format(pattern=NAMESPACE_PATTERN),
        views.ConceptVersionsView.as_view(),
        name='concept-version-list'
    ),
]
