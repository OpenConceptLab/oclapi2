from django.urls import re_path, include, path
from rest_framework.authtoken.views import obtain_auth_token

from core.common.constants import NAMESPACE_PATTERN
from core.orgs import views as org_views
from core.users.models import UserProfile
from . import views

urlpatterns = [
    re_path(r'^$', views.UserListView.as_view(), name='userprofile-list'),
    path('login/', obtain_auth_token, name='user-login'),
    re_path(
        r'^(?P<user>' + NAMESPACE_PATTERN + ')/$',
        views.UserDetailView.as_view(),
        name='userprofile-detail'
    ),
    re_path(
        r'^(?P<user>' + NAMESPACE_PATTERN + ')/reactivate/$',
        views.UserReactivateView.as_view(),
        name='userprofile-reactivate'
    ),
    re_path(
        r'^(?P<user>' + NAMESPACE_PATTERN + ')/orgs/$',
        org_views.OrganizationListView.as_view(),
        {'related_object_type': UserProfile, 'related_object_kwarg': 'user'},
        name='userprofile-orgs'
    ),
    re_path(
        r'^(?P<user>' + NAMESPACE_PATTERN + ')/orgs/sources/$',
        org_views.OrganizationSourceListView.as_view(),
        name='userprofile-organization-source-list'
    ),
    re_path(
        r'^(?P<user>' + NAMESPACE_PATTERN + ')/orgs/collections/$',
        org_views.OrganizationCollectionListView.as_view(),
        name='userprofile-organization-collection-list'
    ),
    re_path(r'^(?P<user>' + NAMESPACE_PATTERN + ')/sources/', include('core.sources.urls')),
    re_path(r'^(?P<user>' + NAMESPACE_PATTERN + ')/collections/', include('core.collections.urls')),
]
