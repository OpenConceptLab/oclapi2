from django.urls import path

from core.events import views

urlpatterns = [
    path('', views.EventsView.as_view(), name='event-list'),
]
