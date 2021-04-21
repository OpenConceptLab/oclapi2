from django.conf.urls import url
from django.urls import re_path, include, path

from core.common.constants import NAMESPACE_PATTERN
from core.orgs import views as org_views
from . import views

urlpatterns = [
    re_path(r'^$', views.UserListView.as_view(), name='userprofile-list'),
    url('login/', views.TokenAuthenticationView.as_view(), name='user-login'),
    url('signup/', views.UserSignup.as_view(), name='user-signup'),
    re_path(
        r'^(?P<user>' + NAMESPACE_PATTERN + ')/$',
        views.UserDetailView.as_view(),
        name='userprofile-detail'
    ),
    path(
        '<str:user>/verify/<str:verification_token>/',
        views.UserEmailVerificationView.as_view(),
        name='userprofile-email-verify'
    ),
    path(
        'password/reset/',
        views.UserPasswordResetView.as_view(),
        name='userprofile-email-verify'
    ),
    re_path(
        r'^(?P<user>' + NAMESPACE_PATTERN + ')/logo/$',
        views.UserLogoView.as_view(),
        name='userprofile-logo'
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
        r'^(?P<user>' + NAMESPACE_PATTERN + ')/extras/$',
        views.UserExtrasView.as_view(),
        name='user-extras'
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
    re_path(
        r"^(?P<user>{pattern})/extras/(?P<extra>{pattern})/$".format(pattern=NAMESPACE_PATTERN),
        views.UserExtraRetrieveUpdateDestroyView.as_view(),
        name='user-extra'
    ),
    re_path(r'^(?P<user>' + NAMESPACE_PATTERN + ')/sources/', include('core.sources.urls')),
    re_path(r'^(?P<user>' + NAMESPACE_PATTERN + ')/collections/', include('core.collections.urls')),
    re_path(r'^(?P<user>' + NAMESPACE_PATTERN + ')/pins/', include('core.pins.urls')),
]
