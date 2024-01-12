from django.urls import path

from core.url_registry import views

urlpatterns = [
    path('', views.URLRegistriesView.as_view(), name='url-registries'),
    path('<int:id>/', views.URLRegistryView.as_view(), name='url-registry'),
]
