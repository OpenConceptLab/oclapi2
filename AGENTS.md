# Repository Guidelines

## Project Structure & Module Organization
`core/` contains the apps (e.g., `core/concepts`, `core/mappings`), plus integration helpers like `core/celery.py` and API wiring in `core/urls.py`. Entrypoints live at repo root (`manage.py`, `startup.sh`), environment tooling sits in `docker-compose*.yml`, and CLI helpers/scripts are under `tools/` (imports, releases, versioning). Tests ship alongside each app in `core/*/tests` with cross-cutting suites in `core/integration_tests/`.
`core/graphql/` hosts the GraphQL service built with Strawberry; `schema.py` wires the root schema, `types.py` defines GraphQL types, and `queries.py` holds resolvers and data loaders. Routes are exposed via `core/graphql/urls.py` and included in `core/urls.py` at `/graphql` with GraphiQL enabled for local exploration. Keep GraphQL tests in `core/graphql/tests/` aligned with the API surface and prefer reusing serializers/query helpers from the owning apps rather than duplicating logic.

## Build, Test, and Development Commands
- `docker compose up -d` boots the API, Postgres, Elasticsearch, Redis, and workers for local hacking.
- `docker compose run --rm api python manage.py test --keepdb -v3` runs the Django test suite quickly against the dev database.
- `docker exec -it oclapi2-api-1 pylint -j2 core` enforces lint rules.
- `docker compose -f docker-compose.yml -f docker-compose.ci.yml run --rm api bash coverage.sh` reports coverage and fails under the CI thresholds.
- `docker exec -it oclapi2-api-1 python manage.py search_index --populate -f --parallel` rebuilds Elasticsearch indexes after model or serializer changes.

## Coding Style & Naming Conventions
Use 4-space indentation, `snake_case` for functions/modules, and `PascalCase` for Django models and serializers. Keep imports sorted (stdlib, third-party, local) and lean on `pylint` to catch regressions. Favor explicit settings toggles by extending `core/toggles/` and document feature flags in docstrings above the flag definition. Apply KISS, DRY, and YAGNI: keep views/serializers simple, consolidate helpers per app, and skip speculative features.
Always comment code, in English only: add concise docstrings for functions, classes, and business rules, and favor descriptive names for variables and resolvers. For GraphQL schema fields, keep `description` attributes accurate and updated when behavior changes. Follow Django principles by centralizing shared logic, avoiding duplication across REST/GraphQL layers, and keeping resolvers thin by delegating to existing services or serializers.

## Testing Guidelines
Target ≥93% coverage by pairing unit tests with integration cases. Name files `test_<feature>.py` and co-locate fixtures inside the app’s `tests/fixtures/`. Smoke Elasticsearch-dependent tests live in `core/integration_tests/` and should be guarded with `@skipUnless(settings.ES_ENABLED, ...)`. Use `python manage.py test app.tests --keepdb` for focused runs and include representative payloads in `core/samples/` when asserting serialization.

## Commit & Pull Request Guidelines
Structure commit subjects using Conventional Commits (`feat(core): add repoType filter`) or issue references such as `OpenConceptLab/ocl_issues#2252 repo facets`. Keep one logical change per commit so release tooling can tag accurately. Follow GitFlow branches—start work on `feature/<issue-id>-slug` from `develop`, reserve `hotfix/*` for production fixes, and merge back through PRs only. Every PR must describe the motivation, list verification steps (`docker compose run --rm api python manage.py test…`), link the tracked issue, attach evidence when API responses change, and call out migrations, indexing, or env var impacts before requesting review.

## Security & Configuration Tips
Keep `vm.max_map_count=262144` set before starting Elasticsearch. For SSO, export `OIDC_SERVER_URL`, `OIDC_SERVER_INTERNAL_URL`, and `OIDC_REALM`; omitting them falls back to Django auth. Never commit secrets—use `.env` overrides or Docker compose profiles instead.
