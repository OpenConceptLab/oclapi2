from django.urls import path

from . import views

urlpatterns = [
    path('', views.CapabilityStatementView.as_view(), name='capability-statement'),
    path('metadata', views.CapabilityStatementView.as_view(), name='capability-statement'),
    path('metadata/', views.CapabilityStatementView.as_view(), name='capability-statement-slash'),
    path('$versions', views.VersionsView.as_view(), name='fhir-versions'),
    path('$versions/', views.VersionsView.as_view(), name='fhir-versions-slash'),
]
