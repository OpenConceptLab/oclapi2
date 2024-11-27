from django.urls import path

from . import views

urlpatterns = [
    path('', views.CodeSystemListView.as_view(), name='code-system-list'),
    path('$lookup/', views.CodeSystemListLookupView.as_view(), name='code-system-list-lookup'),
    path('$lookup', views.CodeSystemListLookupView.as_view(), name='code-system-list-lookup_no_slash'),
    path('$validate-code/', views.CodeSystemValidateCodeView.as_view(), name='code-system-validate-code'),
    path('$validate-code', views.CodeSystemValidateCodeView.as_view(), name='code-system-validate-code_no_slash'),
    path("<str:source>/", views.CodeSystemRetrieveUpdateView.as_view(), name='code-system-detail'),
    path("<str:source>", views.CodeSystemRetrieveUpdateView.as_view(), name='code-system-detail_no_slash'),
]
