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
