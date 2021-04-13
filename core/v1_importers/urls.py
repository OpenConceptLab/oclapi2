from django.urls import path

from core.v1_importers import views

urlpatterns = [
    path('orgs/', views.OrganizationsImporterView.as_view(), name='v1-orgs-import'),
    path('users/', views.UsersImporterView.as_view(), name='v1-users-import'),
    path('sources/', views.SourcesImporterView.as_view(), name='v1-sources-import'),
    path('source-versions/', views.SourceVersionsImporterView.as_view(), name='v1-source-versions-import'),
    path('source-ids/', views.SourceIdsImporterView.as_view(), name='v1-source-ids-import'),
    path('concepts/', views.ConceptsImporterView.as_view(), name='v1-concepts-import'),
    path('concept-versions/', views.ConceptVersionsImporterView.as_view(), name='v1-concept-versions-import'),
    path('concept-ids/', views.ConceptIdsImporterView.as_view(), name='v1-concept-ids-import'),
    path('mappings/', views.MappingsImporterView.as_view(), name='v1-mappings-import'),
    path('mapping-versions/', views.MappingVersionsImporterView.as_view(), name='v1-mapping-versions-import'),
    path('collections/', views.CollectionsImporterView.as_view(), name='v1-collections-import'),
    path('collection-versions/', views.CollectionVersionsImporterView.as_view(), name='v1-collection-versions-import'),
    path('collection-ids/', views.CollectionIdsImporterView.as_view(), name='v1-collection-ids-import'),
    path('web-user-credentials/', views.WebUserCredentialsImporterView.as_view(), name='v1-web-credentials-import'),
    path('references/', views.CollectionReferenceImporterView.as_view(), name='v1-references-import'),
    path(
        'mapping-references/', views.CollectionMappingReferenceImporterView.as_view(),
        name='v1-mapping-references-import'
    ),
    path(
        'collection-parent-connector/', views.CollectionParentConnectorView.as_view(),
        name='collection-parent-connector'
    ),
    path('tokens/', views.UserTokensImporterView.as_view(), name='v1-tokens-import'),
    path('concept/', views.ConceptView.as_view(), name='v1-concept'),
    path('mapping/', views.MappingView.as_view(), name='v1-mapping'),
]
