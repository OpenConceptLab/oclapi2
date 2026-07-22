"""Strawberry schema extensions used to enforce cross-cutting GraphQL policies."""

from typing import Iterator

from graphql import ExecutionResult
from strawberry.extensions import SchemaExtension

from .constants import AUTHENTICATION_FAILED, build_expected_graphql_error


class AuthStatusExtension(SchemaExtension):
    """Reject requests with invalid credentials before any resolver runs."""

    def on_execute(self) -> Iterator[None]:
        context = self.execution_context.context
        if getattr(context, 'auth_status', 'none') == 'invalid':
            self.execution_context.result = ExecutionResult(
                data=None,
                errors=[build_expected_graphql_error(AUTHENTICATION_FAILED)],
            )
        yield
