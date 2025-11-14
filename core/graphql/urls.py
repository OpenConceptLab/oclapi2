from django.urls import path

from .schema import schema
from .views import AuthenticatedGraphQLView

graphql_view = AuthenticatedGraphQLView.as_view(schema=schema, graphiql=True)

urlpatterns = [
    path('graphql/', graphql_view, name='graphql'),
    path('graphql', graphql_view),
]
