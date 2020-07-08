from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.CollectionListView.as_view(), name='collection-list'),
    re_path(
        r"^(?P<collection>{pattern})/$".format(pattern=NAMESPACE_PATTERN),
        views.CollectionRetrieveUpdateDestroyView.as_view(),
        name='collection-detail'
    ),
    re_path(
        r'^(?P<collection>{pattern})/(?P<version>{pattern})/$'.format(pattern=NAMESPACE_PATTERN),
        views.CollectionVersionRetrieveUpdateDestroyView.as_view(),
        name='collection-version-detail'
    ),
]
