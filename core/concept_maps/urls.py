from django.urls import path

from . import views

urlpatterns = [
    path('', views.ConceptMapListView.as_view(), name='concept-map-list'),
    path('$translate/', views.ConceptMapTranslateView.as_view(), name='concept-map-list-translate'),
    path('$translate', views.ConceptMapTranslateView.as_view(), name='concept-map-list-translate_no_slash'),
    path("<str:source>/", views.ConceptMapRetrieveUpdateView.as_view(), name='concept-map-detail'),
    path("<str:source>", views.ConceptMapRetrieveUpdateView.as_view(), name='concept-map-detail_no_slash')
]
