# GraphQL concepts and source MVP

This work carries the permission architecture reviewed in [PR #838](https://github.com/OpenConceptLab/oclapi2/pull/838)
onto master `ab03e1c0`, and adds source metadata and selection-driven retrieval. The API version is `2.3.202-dev`.
Master already contains the [PR #877](https://github.com/OpenConceptLab/oclapi2/pull/877) Strawberry bump:
`strawberry-graphql==0.315.7`, with `strawberry-graphql-django==0.80.0`. These versions were retained.

## Queries

Open `/graphql/` for GraphiQL. Query arguments, returned fields, and summary fields include schema descriptions.
Use the existing OCL token, OIDC bearer token, or session authentication. Invalid credentials are rejected before
resolvers run; authenticated users still require the existing `graphql_api` group. Anonymous queries see public data.

```graphql
query Dictionary($org: String!, $source: String!, $version: String) {
  source(org: $org, source: $source, version: $version) {
    name
    description
    canonicalUrl
    uri
    classes
    datatypes
    mapTypes
    externalSources { name uri }
    summary { activeConcepts mappings }
  }
}
```

Variables: `{"org":"CIEL","source":"CIEL"}`. For personal repositories, replace `org` with `owner` (username).
`uri` is the stored OCL relative URI, for example `/orgs/CIEL/sources/CIEL/`; releases include their version.
The canonical field is spelled `canonicalUrl`.

```graphql
query FindConcepts($org: String, $source: String, $query: String!, $page: Int, $limit: Int) {
  concepts(org: $org, source: $source, query: $query, page: $page, limit: $limit) {
    totalCount
    hasNextPage
    versionResolved
    results { conceptId display description conceptClass datatype { name } }
  }
}
```

Variables: `{"org":"CIEL","source":"CIEL","query":"hypertension","page":1,"limit":20}`.
Omit both `org` and `source` for global search. `conceptIds` performs exact, case-sensitive mnemonic matching,
deduplicates the input, and preserves its order; it takes precedence over `query`. Supply `page` and `limit`
together; the supported result window is 10,000. Without pagination, index responses are capped at 10,000;
`totalCount` remains the total number of matches. Omitted versions use HEAD, falling back to the latest released
version only if HEAD is absent; explicit missing versions do not fall back.

## Data access and permissions

| Selected payload | Retrieval |
| --- | --- |
| Source `name`, `canonicalUrl`, `uri` | Source index projection, including source/version resolution. `uri` is rebuilt from owner, owner type, mnemonic and version rather than stored |
| Concept `id`, `conceptId`, `externalId`, `display`, `conceptClass`, `datatype { name }` | Concept index projection; no ORM concept hydration |
| Source `description`, concept `description` | Not indexed; selecting either routes that request through the ORM |
| Only concept counts/pagination metadata | Elasticsearch request with zero result hits |
| Concept names, mappings, extras, audit metadata, datatype details | ORM hydration with selected concept columns and relations |
| Source classes, datatypes, map types, external sources, summary | Existing version-scoped model querysets; only selected aggregates execute |

Aliases, fragments, `@skip`, `@include`, and nested `__typename` selections participate in planning.
Elasticsearch `_source` is restricted to selected fields. An empty successful direct projection is authoritative;
it does not trigger a database scan. Expected index/transport failures fall back to permission-checked ORM queries.
The older hydrated text-search path retains its empty-index database fallback.

Counts and distinct labels use active, non-retired records. `summary.mappings` counts active, non-retired mappings.
`externalSources` is the deduplicated set of outbound target repositories, excluding the current source and linked
private targets the caller cannot view. Unresolved external URIs are taken from visible mappings.

Repository permission checks reuse the shared REST visibility rule directly, without fabricated requests.
Both owner mnemonic and owner type scope index lookups. Concept visibility relies on the indexed
`public_can_view` flag that `core/sources/signals.py` already propagates from the parent repository, and
mapping hydration independently checks target visibility. HEAD uses the same versioned-object identity as
`Source.get_concepts_queryset()`, while releases use their membership lists.

SQL-free data retrieval does not mean SQL-free authentication: session/token lookup and organization membership
resolution can query the database. Tests verify zero SQL for anonymous public index projections. As with the
existing REST index, indexed results reflect Elasticsearch refresh and indexing propagation latency.

## Rollout

No database migrations or new environment variables are introduced. Refresh the source and concept indexes
before serving this GraphQL version: older concept documents lack the `is_active`, `is_head` and `display_name`
projection fields, and older source documents lack `is_active`. Do not use incomplete indexes during the
rollout; concept projections filter on `is_active` and `is_head`, so an unrefreshed index returns zero
concepts without raising an error.

Use the existing indexing procedure to apply the additive mappings and repopulate both models. For a deployment
that recreates indexes, use its established rebuild procedure; do not rebuild live indexes without accounting for
REST search availability. A full population command for the existing application container is:

```sh
docker exec oclapi2-api-1 python manage.py search_index --populate --models sources.Source concepts.Concept -f --parallel
```

Source permission/activity propagation refreshes the corresponding concept projection flags. Existing
REST search relevance and excluded-word semantics are preserved; unrelated search refactors from PR #838 were
not carried over. Its corrected permission sharing and documented Strawberry auth extension were retained.

## Verification

```sh
docker exec oclapi2-api-1 python manage.py test core.graphql.tests --keepdb --noinput -v2
docker exec oclapi2-api-1 pylint -j2 core/graphql core/common/permissions.py core/common/search.py core/common/views.py core/sources/signals.py core/integration_tests/test_graphql_projection.py
```

`core.integration_tests.test_graphql_projection` requires `settings.ES_ENABLED=True`. It creates uniquely named
indexes and removes them after each test. Run it only against a test Elasticsearch service: shared fixture setup
can also exercise normal indexing hooks. It covers real index preparation, zero SQL, owner isolation, HEAD/release
selection, inactive/retired filtering, private-repository visibility, the rebuilt source URI, and the
database fallback for `description`.

For this worktree, verification used a copy at `/tmp/graphql-sources-20260906` inside the existing API container,
the dedicated database `test_graphql_sources_20260906`, and a temporary Elasticsearch container. The running app's
`/code` checkout and search indexes were not changed. Coverage uses a temporary runner that selects Python's YAML
loader because the container's C YAML loader fails under coverage instrumentation; application dependencies were
not modified to work around that test-environment issue.

Verified results: **75 distinct tests passed**, including six tests against real Elasticsearch and the focused
REST/source-signal regressions. Coverage of `core.graphql` (excluding test files) is **98%**: 739 of 753 statements.
Pylint completed without findings. No changes were made to the application's installed dependency versions.
