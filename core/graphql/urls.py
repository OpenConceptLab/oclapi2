from django.urls import path
from strawberry.django.views import AsyncGraphQLView

from .schema import schema

graphql_view = AsyncGraphQLView.as_view(schema=schema, graphiql=True)

urlpatterns = [
    path('graphql/', graphql_view, name='graphql'),
    path('graphql', graphql_view),
]
