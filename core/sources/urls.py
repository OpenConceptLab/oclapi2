from django.urls import re_path, include

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.SourceListView.as_view(), name='source-list'),
    re_path(
        r'^(?P<source>' + NAMESPACE_PATTERN + ')/$',
        views.SourceRetrieveUpdateDestroyView.as_view(),
        name='source-detail'
    ),
    re_path(
        r'^(?P<source>' + NAMESPACE_PATTERN + ')/versions/$',
        views.SourceVersionListView.as_view(),
        name='source-version-list'
    ),
    re_path(r'^(?P<source>' + NAMESPACE_PATTERN + ')/concepts/', include('core.concepts.urls')),
]
