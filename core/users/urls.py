from django.urls import include, path

from core.orgs import views as org_views
from core.tasks import views as task_views
from . import views
from ..events.views import UserEventsView
from ..repos.views import OrganizationRepoListView
from ..url_registry.views import OrganizationURLRegistryListView

urlpatterns = [
    path('', views.UserListView.as_view(), name='userprofile-list'),
    path('api-token/', views.TokenExchangeView.as_view(), name='user-oid-django-token-exchange'),
    path('oidc/code-exchange/', views.OIDCodeExchangeView.as_view(), name='user-oid-code-exchange'),
    path('login/', views.TokenAuthenticationView.as_view(), name='user-login'),
    path('logout/', views.OIDCLogoutView.as_view(), name='user-logout'),
    path('signup/', views.UserSignup.as_view(), name='user-signup'),
    path('<str:user>/', views.UserDetailView.as_view(), name='userprofile-detail'),
    path('<str:user>/sso-migrate/', views.SSOMigrateView.as_view(), name='userprofile-sso-migrate'),
    path('<str:user>/tasks/', task_views.UserTaskListView.as_view(), name='user-tasks-list'),
    path(
        '<str:user>/verify/<str:verification_token>/',
        views.UserEmailVerificationView.as_view(), name='userprofile-email-verify'),
    path('password/reset/', views.UserPasswordResetView.as_view(), name='userprofile-email-verify'),
    path('<str:user>/logo/', views.UserLogoView.as_view(), name='userprofile-logo'),
    path('<str:user>/reactivate/', views.UserReactivateView.as_view(), name='userprofile-reactivate'),
    path('<str:user>/staff/', views.UserStaffToggleView.as_view(), name='userprofile-reactivate'),
    path('<str:user>/following/', views.UserFollowingListView.as_view(), name='userprofile-following-list'),
    path('<str:user>/following/<int:id>/', views.UserFollowingView.as_view(), name='userprofile-following'),
    path('<str:user>/orgs/', org_views.OrganizationListView.as_view(), name='userprofile-orgs'),
    path('<str:user>/extras/', views.UserExtrasView.as_view(), name='user-extras'),
    path(
        '<str:user>/orgs/sources/',
        org_views.OrganizationSourceListView.as_view(), name='userprofile-organization-source-list'),
    path(
        '<str:user>/orgs/collections/',
        org_views.OrganizationCollectionListView.as_view(), name='userprofile-organization-collection-list'),
    path('<str:user>/orgs/repos/', OrganizationRepoListView.as_view(), name='userprofile-organization-repo-list',),
    path(
        '<str:user>/orgs/url-registry/',
        OrganizationURLRegistryListView.as_view(), name='userprofile-organization-url-registry-list',),
    path("<str:user>/extras/<str:extra>/", views.UserExtraRetrieveUpdateDestroyView.as_view(), name='user-extra'),
    path('<str:user>/repos/', include('core.repos.urls')),
    path('<str:user>/url-registry/', include('core.url_registry.urls')),
    path('<str:user>/sources/', include('core.sources.urls')),
    path('<str:user>/collections/', include('core.collections.urls')),
    path('<str:user>/pins/', include('core.pins.urls')),
    path('<str:user>/events/', UserEventsView.as_view(), name='user-events'),

    # TODO: require FHIR subdomain
    path('<str:user>/CodeSystem/', include('core.code_systems.urls'), name='code_systems_urls'),
    path('<str:user>/CodeSystem', include('core.code_systems.urls'), name='code_systems_urls_no_slash'),
    path('<str:user>/ValueSet/', include('core.value_sets.urls'), name='value_sets_urls'),
    path('<str:user>/ValueSet', include('core.value_sets.urls'), name='value_sets_urls_no_slash'),
    path('<str:user>/ConceptMap/', include('core.concept_maps.urls'), name='concept_maps_urls'),
    path('<str:user>/ConceptMap', include('core.concept_maps.urls'), name='concept_maps_urls_no_slash'),
]
