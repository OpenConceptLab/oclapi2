from django.conf.urls import url
from django.urls import re_path, include
from drf_yasg.utils import swagger_auto_schema
from rest_framework.authtoken.serializers import AuthTokenSerializer
from rest_framework.authtoken.views import obtain_auth_token

from core.common.constants import NAMESPACE_PATTERN
from core.orgs import views as org_views
from . import views

decorated_auth_view = swagger_auto_schema(
    method='post',
    request_body=AuthTokenSerializer
)(obtain_auth_token)

urlpatterns = [
    re_path(r'^$', views.UserListView.as_view(), name='userprofile-list'),
    url('login/', decorated_auth_view, name='user-login'),
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
