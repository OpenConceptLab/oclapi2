##### 2.0.50 - Wed Sep 8 03:27:52 2021 +0000
- updated pydash to 5.0.2
- Merge pull request #36 from OpenConceptLab/dependabot/pip/django-ordered-model-3.4.3
- Merge pull request #37 from OpenConceptLab/dependabot/pip/boto3-1.18.36
- Bump boto3 from 1.14.37 to 1.18.36
- Bump django-ordered-model from 3.4.1 to 3.4.3
- Merge pull request #33 from OpenConceptLab/dependabot/pip/django-cors-headers-3.8.0
- removed six from requirements
- Explicitly adding mock (python core) deps
- [OpenConceptLab/ocl_issues#957](https://github.com/OpenConceptLab/ocl_issues/issues/957) | parallel importers | memory optimiztion | getting rid of content once queued
- Bump django-cors-headers from 3.4.0 to 3.8.0
- Merge pull request #34 from OpenConceptLab/dependabot/pip/django-elasticsearch-dsl-7.2.0
- Bump django-elasticsearch-dsl from 7.1.4 to 7.2.0
- Merge pull request #31 from OpenConceptLab/dependabot/pip/moto-2.2.6
- Merge pull request #26 from OpenConceptLab/dependabot/pip/factory-boy-3.2.0
- Bump moto from 1.3.14 to 2.2.6
- Merge pull request #27 from OpenConceptLab/dependabot/pip/pyyaml-5.4.1
- Merge pull request #29 from OpenConceptLab/dependabot/pip/psycopg2-2.9.1
- [OpenConceptLab/ocl_issues#957](https://github.com/OpenConceptLab/ocl_issues/issues/957) | parallel importers | memory optimiztion | getting rid of content once queued
- Bump psycopg2 from 2.8.5 to 2.9.1
- Bump pyyaml from 5.4 to 5.4.1
- Bump factory-boy from 2.12.0 to 3.2.0
- Fixing celery permissions issue when running locally in dev mode
- updated drf-yasg
- Merge pull request #24 from OpenConceptLab/dependabot/pip/requests-2.26.0
- Bump requests from 2.24.0 to 2.26.0
- Merge pull request #25 from OpenConceptLab/dependabot/pip/django-3.2.7
- Merge pull request #23 from OpenConceptLab/dependabot/pip/djangorestframework-3.12.4
- Merge pull request #22 from OpenConceptLab/dependabot/pip/coverage-5.5
- Merge pull request #21 from OpenConceptLab/dependabot/pip/python-dateutil-2.8.2
- Create codeql-analysis.yml
- Create SECURITY.md
- Bump django from 3.1.12 to 3.2.7
- Bump djangorestframework from 3.11.2 to 3.12.4
- Bump coverage from 5.3.1 to 5.5
- Bump python-dateutil from 2.8.1 to 2.8.2
- Create dependabot.yml
- Source/Collection last latest version force delete on org delete
- [OpenConceptLab/ocl_issues#955](https://github.com/OpenConceptLab/ocl_issues/issues/955) | CSV importer test for OpenMRS schema
- [OpenConceptLab/ocl_issues#897](https://github.com/OpenConceptLab/ocl_issues/issues/897) Adding envs and args to runtime docker image
- [OpenConceptLab/ocl_issues#897](https://github.com/OpenConceptLab/ocl_issues/issues/897) Adding missing curl
- [OpenConceptLab/ocl_issues#897](https://github.com/OpenConceptLab/ocl_issues/issues/897) Adding missing permissions
- [OpenConceptLab/ocl_issues#897](https://github.com/OpenConceptLab/ocl_issues/issues/897) Fixing tests
- Revert "Revert "OpenConceptLab/ocl_issues#897 Run OCL API using gunicorn""
##### 2.0.47 - Tue Aug 31 07:27:09 2021 +0000
- Org delete to delete children first
##### 2.0.46 - Fri Aug 27 09:14:25 2021 +0000
- Org delete to use bulk_delete
- [OpenConceptLab/ocl_issues#947](https://github.com/OpenConceptLab/ocl_issues/issues/947) | Handling ES error of max pagination
##### 2.0.45 - Fri Aug 27 03:37:30 2021 +0000
- [OpenConceptLab/ocl_issues#949](https://github.com/OpenConceptLab/ocl_issues/issues/949) | Source/collection last child updated at | using max query
- [OpenConceptLab/ocl_issues#949](https://github.com/OpenConceptLab/ocl_issues/issues/949) | Concept hierarchy | avoiding join
- [OpenConceptLab/ocl_issues#949](https://github.com/OpenConceptLab/ocl_issues/issues/949) | Mapping import | removed like query | reduced parent/owner joins
- [OpenConceptLab/ocl_issues#911](https://github.com/OpenConceptLab/ocl_issues/issues/911) | +@akhilkala | Orgs List with no members using query parameter
- [OpenConceptLab/ocl_issues#936](https://github.com/OpenConceptLab/ocl_issues/issues/936) | can request facets only from search routes
- Importers | Added deleted count and details in results
- [OpenConceptLab/ocl_issues#935](https://github.com/OpenConceptLab/ocl_issues/issues/935) | Parallel Importer | Mapping Importer to consider id (mnemonic) attribute for exists check
- [OpenConceptLab/ocl_issues#935](https://github.com/OpenConceptLab/ocl_issues/issues/935) | Parallel Importer | Fixing tests
- [OpenConceptLab/ocl_issues#935](https://github.com/OpenConceptLab/ocl_issues/issues/935) | Parallel Importer | Source/Collection version create to append results in created and not updated
##### 2.0.41 - Mon Aug 16 04:19:11 2021 +0000
- Including source/collection summaries in user/org pins listing
- [OpenConceptLab/ocl_issues#910](https://github.com/OpenConceptLab/ocl_issues/issues/910) | export mappings | not loading relations eagerly
- [OpenConceptLab/ocl_issues#910](https://github.com/OpenConceptLab/ocl_issues/issues/910) | ordering concepts/mappings | fixing batch size typo
- [OpenConceptLab/ocl_issues#910](https://github.com/OpenConceptLab/ocl_issues/issues/910) | Export queries | limit/offset on lookup table only
- [OpenConceptLab/ocl_issues#910](https://github.com/OpenConceptLab/ocl_issues/issues/910) | Fixing collection export concepts/mappings queryset
- [OpenConceptLab/ocl_issues#910](https://github.com/OpenConceptLab/ocl_issues/issues/910) | Slow Query | concept/mapping exports to use less joins
- Authoring Report | Added summary and description in swagger
##### 2.0.38 - Thu Aug 12 01:45:01 2021 +0000
- concept/mappings | Removed uri LIKE criteria
- Update README.md
- Amend hierarchy api to take input as parent->child uri map
- pylint | Fixing indentation
- [OpenConceptLab/ocl_issues#845](https://github.com/OpenConceptLab/ocl_issues/issues/845) Adding missing composite index
##### 2.0.37 - Wed Aug 11 08:47:34 2021 +0000
- using raw query for dormant locales count
- Source exports | concepts to have child and parent concept urls
- Admin API amend the concept hierarchy
- Fixing unsued import
- [OpenConceptLab/ocl_issues#845](https://github.com/OpenConceptLab/ocl_issues/issues/845) Reverting IN unnest custom lookups
##### 2.0.35 - Tue Aug 10 03:33:12 2021 +0000
- [OpenConceptLab/ocl_issues#895](https://github.com/OpenConceptLab/ocl_issues/issues/895) | concept/mapping | Admin API to delete (hard) a version
##### 2.0.34 - Mon Aug 9 11:28:23 2021 +0000
- user(s) authoring report | counts of resources created/updated
- Indexing | making sure re-run of delete job doesn't fail if the instance is already deleted
##### 2.0.33 - Fri Aug 6 08:19:53 2021 +0000
- delete duplicate locales task | Updated log statement
- Indexes API | can index resources by uri filter
- Limiting locales for each concept to max 500
- integration test for different concept response modes (verbose/standard/brief)
- Concept brief response '?brief=true' | returns uuid and id only
- [OpenConceptLab/ocl_issues#45](https://github.com/OpenConceptLab/ocl_issues/issues/45) | not validating retired concept locales
- Fixing concept new/version leaving dormant locales
- [OpenConceptLab/ocl_issues#860](https://github.com/OpenConceptLab/ocl_issues/issues/860) | self mappings | mappings can be created with same from/to concept
- [OpenConceptLab/ocl_issues#857](https://github.com/OpenConceptLab/ocl_issues/issues/857) Frequent 504 gateway timeout when requesting export on staging
- [OpenConceptLab/ocl_issues#852](https://github.com/OpenConceptLab/ocl_issues/issues/852) | Monthly usage report | added collection references in serializer
- [OpenConceptLab/ocl_issues#852](https://github.com/OpenConceptLab/ocl_issues/issues/852) | added date range in monthly usage report
- locales dormant/duplicate routes under admin namespace
- Concept/Mapping | simplifying version get criteria
- Concept summary API to return concept and not latest version when no version is specified
- Concept hard delete to not leave any dormant locales behind
- Concept POST/PUT | fixing parent concept urls not accepted
- api to delete dormant locales in batches
- Added version info in swagger UI
- api to get count of dormant locales
- logging count of dormant locales deleted
- api/task to get concept/version summary, clean dormant locales
- api/task for sys admin to delete dormant locales
- async concept hard delete sys admin api
- Task to cleanup duplicate locales | processing in batches
- [OpenConceptLab/ocl_issues#845](https://github.com/OpenConceptLab/ocl_issues/issues/845) Adding missing indexes
- [OpenConceptLab/ocl_issues#857](https://github.com/OpenConceptLab/ocl_issues/issues/857) | Source/collection child max updated at to select only updated_at field
- Source concept/mapping export to eager load source's parent correctly
##### 2.0.21 - Wed Jul 28 05:30:54 2021 +0000
- [OpenConceptLab/ocl_issues#845](https://github.com/OpenConceptLab/ocl_issues/issues/845) | exports | reducing batch size to 100
- [OpenConceptLab/ocl_issues#852](https://github.com/OpenConceptLab/ocl_issues/issues/852) | monthly usage report under admin/report/ namespace
- [OpenConceptLab/ocl_issues#838](https://github.com/OpenConceptLab/ocl_issues/issues/838) | User List can be filtered by dateJoinedBefore and dateJoinedSince
##### 2.0.20 - Mon Jul 26 10:28:11 2021 +0000
- [OpenConceptLab/ocl_issues#845](https://github.com/OpenConceptLab/ocl_issues/issues/845) | performance | merging excludes
##### 2.0.19 - Fri Jul 23 03:09:59 2021 +0000
- [OpenConceptLab/ocl_issues#853](https://github.com/OpenConceptLab/ocl_issues/issues/853) | search results to also consider org and user scope permissions
##### 2.0.18 - Thu Jul 22 09:55:18 2021 +0000
- [OpenConceptLab/ocl_issues#852](https://github.com/OpenConceptLab/ocl_issues/issues/852) | user monthly report | added collection/source versions and collection references in verbose mode
- [OpenConceptLab/ocl_issues#852](https://github.com/OpenConceptLab/ocl_issues/issues/852) | users monthly report API
- [OpenConceptLab/ocl_issues#845](https://github.com/OpenConceptLab/ocl_issues/issues/845) Adding indexes for public_access fields
- [OpenConceptLab/ocl_issues#845](https://github.com/OpenConceptLab/ocl_issues/issues/845) Adding indexes for LocalizedText
- [OpenConceptLab/ocl_issues#845](https://github.com/OpenConceptLab/ocl_issues/issues/845) Adding upper index for sources_mnemonic
- [OpenConceptLab/ocl_issues#845](https://github.com/OpenConceptLab/ocl_issues/issues/845) | Concept List API performance | fixing n+1 query | versions url to be guessed rather than computed
- [OpenConceptLab/ocl_issues#828](https://github.com/OpenConceptLab/ocl_issues/issues/828) | changelog to be autoupdated with release version update
##### 2.0.13 - Mon Jul 19 08:54:47 2021 +0000
- [OpenConceptLab/ocl_issues#846](https://github.com/OpenConceptLab/ocl_issues/issues/846) | Concept/Mapping queryset (without search) | refactoring and combining filters in criterion | removes duplicate results
- [OpenConceptLab/ocl_issues#830](https://github.com/OpenConceptLab/ocl_issues/issues/830) | /changelog API to read changelog file directly from github
- [OpenConceptLab/ocl_issues#845](https://github.com/OpenConceptLab/ocl_issues/issues/845) Timeout fetching Locales, adding migration files
##### 2.0.12 - Fri Jul 9 13:26:39 2021 +0000
- Caching result of export path till the duration of self
- Created indexes on concept/mapping updated_at
- [OpenConceptLab/ocl_issues#830](https://github.com/OpenConceptLab/ocl_issues/issues/830) | /changelog API to listdown changelog (HTML)
- [OpenConceptLab/ocl_issues#830](https://github.com/OpenConceptLab/ocl_issues/issues/830) | change logs to have issue numbers as links
- [OpenConceptLab/ocl_issues#830](https://github.com/OpenConceptLab/ocl_issues/issues/830) | python script to generate changelog/release-notes
##### 2.0.11 - Fri Jul 9 03:49:04 2021 +0000
- [OpenConceptLab/ocl_issues#823](https://github.com/OpenConceptLab/ocl_issues/issues/823) | includeMappings/includeInverseMappings for a collection's concept will now use the collection's scope
- [OpenConceptLab/ocl_issues#829](https://github.com/OpenConceptLab/ocl_issues/issues/829) | users lists can be filtered by last login before/since
- on org save adding creator/updater as member
- collection concept reference add to decode concept uri
- Fixing concept get for encoded strings
