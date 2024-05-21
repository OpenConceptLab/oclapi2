from django.urls import path

from . import views

urlpatterns = [
    path('', views.CapabilityStatementView.as_view(), name='capability-statement'),
    path('metadata', views.CapabilityStatementView.as_view(), name='capability-statement'),
]
