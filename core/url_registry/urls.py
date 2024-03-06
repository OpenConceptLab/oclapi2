from django.urls import path, re_path

from core.url_registry import views

urlpatterns = [
    path('', views.URLRegistriesView.as_view(), name='url-registries'),
    path('<int:id>/', views.URLRegistryView.as_view(), name='url-registry'),
    re_path(r'^\$lookup/$', views.URLRegistryLookupView.as_view(), name='url-registry-lookup'),
]
