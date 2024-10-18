##### 2.3.141 - Fri Oct 11 06:55:38 2024 +0000
- Reverting local changes
- Feedback | fixing sorting
##### 2.3.140 - Fri Oct 11 04:36:11 2024 +0000
- Importers | Test for partial concept (extras) update
##### 2.3.139 - Fri Oct 11 02:40:19 2024 +0000
- Errbit | fixing clone mapping
##### 2.3.138 - Tue Oct 8 02:42:26 2024 +0000
- Bug | Repo new version to also update children counts
##### 2.3.137 - Mon Oct 7 01:54:54 2024 +0000
- [OpenConceptLab/ocl_issues#1936](https://github.com/OpenConceptLab/ocl_issues/issues/1936) | Adding more things to Collection brief serializer
- [OpenConceptLab/ocl_issues#1936](https://github.com/OpenConceptLab/ocl_issues/issues/1936) | Added OCL Joined and Org Joined events | management task to run on bootup to seed missed events
- [OpenConceptLab/ocl_issues#1776](https://github.com/OpenConceptLab/ocl_issues/issues/1776) | fixing tests
- [OpenConceptLab/ocl_issues#1776](https://github.com/OpenConceptLab/ocl_issues/issues/1776) | adding entity's description to events responses
##### 2.3.136 - Mon Sep 30 06:34:22 2024 +0000
- [OpenConceptLab/ocl_issues#1861](https://github.com/OpenConceptLab/ocl_issues/issues/1861) | fixing typo
##### 2.3.135 - Mon Sep 30 05:19:30 2024 +0000
- [OpenConceptLab/ocl_issues#1861](https://github.com/OpenConceptLab/ocl_issues/issues/1861) | env var for username to get highlighted events from
##### 2.3.134 - Fri Sep 27 07:50:34 2024 +0000
- [OpenConceptLab/ocl_issues#1921](https://github.com/OpenConceptLab/ocl_issues/issues/1921) | OpenMRS custom validation schema | excluding empty name type
- Revert "OpenConceptLab/ocl_issues#1921 | OpenMRS custom validation schema | excluding Synonyms in uniq check"
- [OpenConceptLab/ocl_issues#1921](https://github.com/OpenConceptLab/ocl_issues/issues/1921) | OpenMRS custom validation schema | excluding Synonyms in uniq check
##### 2.3.133 - Thu Sep 26 07:56:40 2024 +0000
- [OpenConceptLab/ocl_issues#1861](https://github.com/OpenConceptLab/ocl_issues/issues/1861) | Guest Events API to redirect to user's events API if requested by authenticated user
- [OpenConceptLab/ocl_issues#1861](https://github.com/OpenConceptLab/ocl_issues/issues/1861) | Guest events are admin/superuser's non-user following's create/release/follow events
- [OpenConceptLab/ocl_issues#1861](https://github.com/OpenConceptLab/ocl_issues/issues/1861) | fixing pylint and tests
- [OpenConceptLab/ocl_issues#1861](https://github.com/OpenConceptLab/ocl_issues/issues/1861) | Anonymouse user events API | Repo version release event
##### 2.3.132 - Thu Sep 19 04:21:01 2024 +0000
- Fixing typo and removing print
- [OpenConceptLab/ocl_issues#1791](https://github.com/OpenConceptLab/ocl_issues/issues/1791) Fix listing dependencies in progress
- [OpenConceptLab/ocl_issues#1922](https://github.com/OpenConceptLab/ocl_issues/issues/1922) Fix overwriting ConceptMap version, OpenConceptLab/ocl_issues#1917 Add S3 storage support
- [OpenConceptLab/ocl_issues#1917](https://github.com/OpenConceptLab/ocl_issues/issues/1917) Add S3 storage support for new bulk import
- [OpenConceptLab/ocl_issues#1862](https://github.com/OpenConceptLab/ocl_issues/issues/1862) | User events API with different scopes
- [OpenConceptLab/ocl_issues#1780](https://github.com/OpenConceptLab/ocl_issues/issues/1780) | fixing followers in user details view
##### 2.3.131 - Wed Sep 4 02:54:19 2024 +0000
- [OpenConceptLab/ocl_issues#1884](https://github.com/OpenConceptLab/ocl_issues/issues/1884) | fixing tests | mocking indexing job
- [OpenConceptLab/ocl_issues#1884](https://github.com/OpenConceptLab/ocl_issues/issues/1884) | fixing tests | mocking indexing job
- [OpenConceptLab/ocl_issues#1884](https://github.com/OpenConceptLab/ocl_issues/issues/1884) | new version for collection to mantain tasks
- [OpenConceptLab/ocl_issues#1884](https://github.com/OpenConceptLab/ocl_issues/issues/1884) | refactoring seed new version task
- [OpenConceptLab/ocl_issues#1780](https://github.com/OpenConceptLab/ocl_issues/issues/1780) | fixing pylint error
- [OpenConceptLab/ocl_issues#1780](https://github.com/OpenConceptLab/ocl_issues/issues/1780) | User events vs org events criteria | org events includes where org is object or referenced object
- [OpenConceptLab/ocl_issues#1780](https://github.com/OpenConceptLab/ocl_issues/issues/1780) | Follow model type
##### 2.3.130 - Fri Aug 30 06:40:57 2024 +0000
- [OpenConceptLab/ocl_issues#1780](https://github.com/OpenConceptLab/ocl_issues/issues/1780) | can follow any entity
- [OpenConceptLab/ocl_issues#1884](https://github.com/OpenConceptLab/ocl_issues/issues/1884) | source version different states/tasks
- [OpenConceptLab/ocl_issues#1884](https://github.com/OpenConceptLab/ocl_issues/issues/1884) | released source version creation indexing refactoring
- [OpenConceptLab/ocl_issues#1791](https://github.com/OpenConceptLab/ocl_issues/issues/1791) Fixing paths for NPM support
- [OpenConceptLab/ocl_issues#1780](https://github.com/OpenConceptLab/ocl_issues/issues/1780) | Event | saving referenced object in case it gets deleted
##### 2.3.129 - Wed Aug 21 03:38:06 2024 +0000
- Org delete | fixing test for sync delete S3 files
- Org delete from importer to delete repos cached imports in sync
- [OpenConceptLab/ocl_issues#1780](https://github.com/OpenConceptLab/ocl_issues/issues/1780) | fixing test
- [OpenConceptLab/ocl_issues#1780](https://github.com/OpenConceptLab/ocl_issues/issues/1780) | events for different actions
- [OpenConceptLab/ocl_issues#1780](https://github.com/OpenConceptLab/ocl_issues/issues/1780) | events creation on source/org create and user follow/unfollow and obj representation
- [OpenConceptLab/ocl_issues#1780](https://github.com/OpenConceptLab/ocl_issues/issues/1780) | fixing pylint errors
- [OpenConceptLab/ocl_issues#1780](https://github.com/OpenConceptLab/ocl_issues/issues/1780) | events model and API
- bundles.__init__.py | added VERSION
- [OpenConceptLab/ocl_issues#1791](https://github.com/OpenConceptLab/ocl_issues/issues/1791) Adding forgotten file
- [OpenConceptLab/ocl_issues#1791](https://github.com/OpenConceptLab/ocl_issues/issues/1791) Add support for importing NPM packages
##### 2.3.128 - Thu Aug 8 03:50:51 2024 +0000
- [OpenConceptLab/ocl_issues#1912](https://github.com/OpenConceptLab/ocl_issues/issues/1912) | renamed detailed_summary to message | only json result
- Pointing to specific ocldev release
##### 2.3.127 - Wed Aug 7 05:38:05 2024 +0000
- [OpenConceptLab/ocl_issues#1912](https://github.com/OpenConceptLab/ocl_issues/issues/1912) | Import result | responding with json/report/summary result | correcting serialization
- [OpenConceptLab/ocl_issues#1780](https://github.com/OpenConceptLab/ocl_issues/issues/1780) | fixing pylint
- [OpenConceptLab/ocl_issues#1780](https://github.com/OpenConceptLab/ocl_issues/issues/1780) | fixing pylint
- [OpenConceptLab/ocl_issues#1780](https://github.com/OpenConceptLab/ocl_issues/issues/1780) | API for user unfollow
- [OpenConceptLab/ocl_issues#1780](https://github.com/OpenConceptLab/ocl_issues/issues/1780) | APIs for user followers/following
##### 2.3.126 - Mon Aug 5 04:35:21 2024 +0000
- Test for importing existing mapping to mark it retired
##### 2.3.125 - Thu Aug 1 03:35:23 2024 +0000
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | fixing tests
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | removed dead test
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | fixing pylint
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | fixing pylint
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | using ocldev checksum to generate checksums
- Update README.md (#733)
- removed unused import
- [OpenConceptLab/ocl_issues#1907](https://github.com/OpenConceptLab/ocl_issues/issues/1907)  | importer | reference delete with strict check
- Repo summary view | Reusing mixins | removed unused base class
- [OpenConceptLab/ocl_issues#1907](https://github.com/OpenConceptLab/ocl_issues/issues/1907)  | fixing tests
- [OpenConceptLab/ocl_issues#1907](https://github.com/OpenConceptLab/ocl_issues/issues/1907)  | importer | reference delete
##### 2.3.124 - Fri Jul 26 03:14:15 2024 +0000
- [OpenConceptLab/ocl_issues#1617](https://github.com/OpenConceptLab/ocl_issues/issues/1617) | added bio field on user
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | removed redundant python script
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | Checksum | ignoring locale_preferred=false, name_type/description_type empty
##### 2.3.123 - Fri Jul 19 04:18:14 2024 +0000
- Collection references put | list request
##### 2.3.122 - Wed Jul 17 06:25:10 2024 +0000
- Search criteria to apply leading wildcard for code fields
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | JS checksum generate | correcting sorting and encoding to match with python
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | checksum to conder locale's external_id
##### 2.3.121 - Mon Jul 15 02:14:16 2024 +0000
- fix: update ES_ENABLE_SNIFFING environment variable (#732)
- chore: add elastic search sniffing configuration option setting. (#731)
##### 2.3.120 - Wed Jul 10 10:12:20 2024 +0000
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | fixing test
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | consider external_id from locale for checksum | remove empty object values from checksums | remove empty hierarchy from checksum
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | cleanup fields to treat float and int same if there is no decimal value
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | mapping sort weight to float for checksum generation
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | mapping sort weight to float for checksum generation
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | printing fields used for checksum
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | fixing test
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | script to generate concept/mapping standard/smart checksum
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | script to generate concept/mapping standard/smart checksum
- [OpenConceptLab/ocl_issues#1867](https://github.com/OpenConceptLab/ocl_issues/issues/1867) | fixing typo
##### 2.3.119 - Mon Jul 8 07:03:40 2024 +0000
- [OpenConceptLab/ocl_issues#1617](https://github.com/OpenConceptLab/ocl_issues/issues/1617) | API to redirect to SSO reset password url
##### 2.3.118 - Wed Jul 3 06:00:38 2024 +0000
- [OpenConceptLab/ocl_issues#1829](https://github.com/OpenConceptLab/ocl_issues/issues/1829) | fixing pylint
- [OpenConceptLab/ocl_issues#1829](https://github.com/OpenConceptLab/ocl_issues/issues/1829) | Source update to only update is_active and public_access on children if it changed
- [OpenConceptLab/ocl_issues#1829](https://github.com/OpenConceptLab/ocl_issues/issues/1829) | Source update to only update is_active on children if it changed
- [OpenConceptLab/ocl_issues#1829](https://github.com/OpenConceptLab/ocl_issues/issues/1829) | Source update to only update is_active on children if it changed
##### 2.3.117 - Tue Jul 2 10:09:20 2024 +0000
- [OpenConceptLab/ocl_issues#1860](https://github.com/OpenConceptLab/ocl_issues/issues/1860) | expansion concept cascade view
##### 2.3.116 - Tue Jul 2 06:23:48 2024 +0000
- [OpenConceptLab/ocl_issues#1860](https://github.com/OpenConceptLab/ocl_issues/issues/1860) | expansion concept cascade view | fixing conflict with uri param
- [OpenConceptLab/ocl_issues#1860](https://github.com/OpenConceptLab/ocl_issues/issues/1860) | expansion concept cascade view | refactoring and merging views
- [OpenConceptLab/ocl_issues#1860](https://github.com/OpenConceptLab/ocl_issues/issues/1860) | expansion conflicting concepts
- [OpenConceptLab/ocl_issues#1860](https://github.com/OpenConceptLab/ocl_issues/issues/1860) | expansion conflicting concepts resolution using uri param
##### 2.3.115 - Thu Jun 27 03:48:12 2024 +0000
- Can do OR search for exact match
##### 2.3.114 - Mon Jun 24 04:59:56 2024 +0000
- Source mappings summary | including self as to/from source
##### 2.3.113 - Mon Jun 24 02:56:14 2024 +0000
- Source mappings summary | fixing tests
- Source mappings summary | not removing self
- [OpenConceptLab/ocl_issues#1845](https://github.com/OpenConceptLab/ocl_issues/issues/1845) | fixing empty exclude references call
##### 2.3.112 - Mon Jun 17 11:43:43 2024 +0000
- fixing pylint
- Errbit | handling ValueError in mapping create | to/mapping source set as collection
##### 2.3.111 - Fri Jun 14 08:55:03 2024 +0000
- [OpenConceptLab/ocl_issues#1850](https://github.com/OpenConceptLab/ocl_issues/issues/1850) | fixing pylint
- [OpenConceptLab/ocl_issues#1850](https://github.com/OpenConceptLab/ocl_issues/issues/1850) | accepting expression in new format without system/valueset | reference can accept extras filters
##### 2.3.110 - Wed Jun 12 09:30:06 2024 +0000
- [OpenConceptLab/ocl_issues#1849](https://github.com/OpenConceptLab/ocl_issues/issues/1849) | handle too long input for search
##### 2.3.109 - Mon Jun 10 09:40:38 2024 +0000
- [OpenConceptLab/ocl_issues#1844](https://github.com/OpenConceptLab/ocl_issues/issues/1844) |  | expanding verbosity levels
##### 2.3.108 - Mon Jun 10 08:22:30 2024 +0000
- FHIR Errbit | unsupported query param to return http400 exception
##### 2.3.107 - Mon Jun 10 04:07:23 2024 +0000
- Errbit | CodeSystem List | text field serialization fix
- Errbit | ConceptMap List | mappings without from/to-source-url fix
##### 2.3.106 - Mon Jun 3 11:24:21 2024 +0000
- [OpenConceptLab/ocl_issues#1844](https://github.com/OpenConceptLab/ocl_issues/issues/1844) | / | version1 is older and vesion2 is newer
- Importer | save summary with more details
##### 2.3.105 - Thu May 30 02:35:19 2024 +0000
- [OpenConceptLab/ocl_issues#1842](https://github.com/OpenConceptLab/ocl_issues/issues/1842) | root view | correcting FHIR URLs
##### 2.3.104 - Tue May 28 12:04:18 2024 +0000
- [OpenConceptLab/ocl_issues#1844](https://github.com/OpenConceptLab/ocl_issues/issues/1844) | generate checksums for latest concept/mapping versions of repo
##### 2.3.103 - Tue May 28 10:42:44 2024 +0000
- [OpenConceptLab/ocl_issues#1844](https://github.com/OpenConceptLab/ocl_issues/issues/1844) | fixing checksum check and tests
- [OpenConceptLab/ocl_issues#1844](https://github.com/OpenConceptLab/ocl_issues/issues/1844) | checksums comprison to prevent duplicate version creation fix
- Import json result handling parse exception
##### 2.3.102 - Tue May 21 12:08:05 2024 +0000
- [OpenConceptLab/ocl_issues#1839](https://github.com/OpenConceptLab/ocl_issues/issues/1839) Add code searchParam for CodeSystem (fix)
- [OpenConceptLab/ocl_issues#1839](https://github.com/OpenConceptLab/ocl_issues/issues/1839) Add code searchParam for CodeSystem
##### 2.3.101 - Tue May 21 08:09:54 2024 +0000
- Errbit | CodeSystem serializer text field | literal eval try catch
- [OpenConceptLab/ocl_issues#1841](https://github.com/OpenConceptLab/ocl_issues/issues/1841) | not using any pattern for kwargs in url | repo version can accept special characters | uri will be encoded
##### 2.3.100 - Mon May 20 10:38:46 2024 +0000
- Code System validateCode | fixing errbit
- [OpenConceptLab/ocl_issues#1840](https://github.com/OpenConceptLab/ocl_issues/issues/1840) CodeSystem listing should include total
- [OpenConceptLab/ocl_issues#1833](https://github.com/OpenConceptLab/ocl_issues/issues/1833) Fix import read timeout
##### 2.3.99 - Thu May 16 11:10:58 2024 +0000
- [OpenConceptLab/ocl_issues#1833](https://github.com/OpenConceptLab/ocl_issues/issues/1833) 502 Bad Gateway for Large CodeSystem resources
- Increase gunicorn request timeout (fix)
- Increase gunicorn request timeout
##### 2.3.98 - Thu May 16 02:57:00 2024 +0000
Wed Mar 6 07:57:03 2024 +0530
Mon Dec 11 15:18:33 2023 +0530
Wed Aug 2 08:28:38 2023 +0530
- monthly resource report can accept an email
- [OpenConceptLab/ocl_issues#1237](https://github.com/OpenConceptLab/ocl_issues/issues/1237) FHIR Capability Statement
##### 2.3.97 - Wed May 15 14:28:23 2024 +0000
- [OpenConceptLab/ocl_issues#1237](https://github.com/OpenConceptLab/ocl_issues/issues/1237) FHIR Capability Statement
- [OpenConceptLab/ocl_issues#1833](https://github.com/OpenConceptLab/ocl_issues/issues/1833) | refactoring code system create with concepts to do indexing async
- Removed checksums toggle
- Removed print
- [OpenConceptLab/ocl_issues#1834](https://github.com/OpenConceptLab/ocl_issues/issues/1834) | Collection Reference filter 'property' to accept any value | reference evaluation to ignore random values
- [OpenConceptLab/ocl_issues#1836](https://github.com/OpenConceptLab/ocl_issues/issues/1836) FHIR ConceptMap fails to import due to 'null' map_type
- [OpenConceptLab/ocl_issues#1834](https://github.com/OpenConceptLab/ocl_issues/issues/1834) | Test for Valueset with just system and no filter in include
##### 2.3.96 - Fri May 10 02:30:00 2024 +0000
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | fixing typo
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | fixing typo
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | task list view cannot return result
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | task result needs to be JSON
##### 2.3.95 - Thu May 9 02:44:18 2024 +0000
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | fixing pylint
- [OpenConceptLab/ocl_issues#1815](https://github.com/OpenConceptLab/ocl_issues/issues/1815) | added missed migration
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | changelog | using operations | refactoring and added tests
##### 2.3.94 - Mon May 6 04:04:01 2024 +0000
Wed Mar 6 07:57:03 2024 +0530
Mon Dec 11 15:18:33 2023 +0530
Wed Aug 2 08:28:38 2023 +0530
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | fixing typo
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | changelog | making it async
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | changelog | updated result keys and added diff with verbosity
- Refactoring method names
- [OpenConceptLab/ocl_issues#1815](https://github.com/OpenConceptLab/ocl_issues/issues/1815) FHIR CodeSystem Fixes
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | changelog | fixing duplicates
##### 2.3.93 - Fri May 3 10:56:48 2024 +0000
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | changelog | fixing pylint
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | changelog | using smart changed before changed
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | fixing task get view
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | checksum save should not trigger indexing
##### 2.3.92 - Thu May 2 11:46:11 2024 +0000
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | fixing pylint
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | fixing typo
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | added task result serializer
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | moving changelog/diff to async task
- [OpenConceptLab/ocl_issues#1761](https://github.com/OpenConceptLab/ocl_issues/issues/1761) | fixing middleware and pylint
- [OpenConceptLab/ocl_issues#1825](https://github.com/OpenConceptLab/ocl_issues/issues/1825) | fixing tests
- [OpenConceptLab/ocl_issues#1825](https://github.com/OpenConceptLab/ocl_issues/issues/1825) | fixing pylint
- [OpenConceptLab/ocl_issues#1825](https://github.com/OpenConceptLab/ocl_issues/issues/1825) | reference errors are more description with conflicting concept/name/reference
- [OpenConceptLab/ocl_issues#1761](https://github.com/OpenConceptLab/ocl_issues/issues/1761) Add FHIR xml support
- Fixing sort without search
##### 2.3.91 - Tue Apr 30 04:08:33 2024 +0000
- sort with search should work
##### 2.3.90 - Mon Apr 29 05:39:07 2024 +0000
Wed Mar 6 07:57:03 2024 +0530
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | fixing pylint
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | celery tasks cleanup job to remove any task older than 7 days
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | celery tasks to not store args and store result as str and not json
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | changelog to reflect correct mappings in concepts
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | changelog to reflect concepts with only mappings changes
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | changelog of two source versions
##### 2.3.89 - Wed Apr 24 05:37:40 2024 +0000
Wed Mar 6 07:57:03 2024 +0530
Mon Dec 11 15:18:33 2023 +0530
Wed Aug 2 08:28:38 2023 +0530
- [OpenConceptLab/ocl_issues#1824](https://github.com/OpenConceptLab/ocl_issues/issues/1824) | Reference transform extensional
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | fixing pylint
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | added task post run signal to close db connections
##### 2.3.88 - Tue Apr 23 02:59:09 2024 +0000
Wed Mar 6 07:57:03 2024 +0530
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | using SHA-256 to fix the queue for same user and import queue
##### 2.3.87 - Thu Apr 4 05:42:46 2024 +0000
Wed Mar 6 07:57:03 2024 +0530
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | fixing queue filtering
- Source/Collection | search by full_name and correcting other attributes
- Upgrading to django 4.2.11
##### 2.3.86 - Wed Apr 3 06:48:06 2024 +0000
Wed Mar 6 07:57:03 2024 +0530
- Merge branch 'dev'
- [OpenConceptLab/ocl_issues#1817](https://github.com/OpenConceptLab/ocl_issues/issues/1817) | fixing pylint
- [OpenConceptLab/ocl_issues#1817](https://github.com/OpenConceptLab/ocl_issues/issues/1817) | fixing test
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | seralizing results of task
- [OpenConceptLab/ocl_issues#1742](https://github.com/OpenConceptLab/ocl_issues/issues/1742) | resolve to return source/collection response for HEAD version
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | removed unused flower task serializer
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | task response should reflect user submitted queue and not actual queue
- Revert "OpenConceptLab/ocl_issues#1777 | queue should be saved as user submitted"
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | queue should be saved as user submitted
- [OpenConceptLab/ocl_issues#1779](https://github.com/OpenConceptLab/ocl_issues/issues/1779) | streamed response should not have content-length header
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | queue should not override
- [OpenConceptLab/ocl_issues#1779](https://github.com/OpenConceptLab/ocl_issues/issues/1779) | export stream response to have content length header
- [OpenConceptLab/ocl_issues#1819](https://github.com/OpenConceptLab/ocl_issues/issues/1819) | removed print | pylint fix
- [OpenConceptLab/ocl_issues#1819](https://github.com/OpenConceptLab/ocl_issues/issues/1819) | batch delete to not use paginator/iterator
- [OpenConceptLab/ocl_issues#1819](https://github.com/OpenConceptLab/ocl_issues/issues/1819) | using paginator in place of iterator (#645)
- dropping coverage to 91
- No coverage for azure
- [OpenConceptLab/ocl_issues#1779](https://github.com/OpenConceptLab/ocl_issues/issues/1779) | exports download via streaming
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | after_return to handle exception | added try catch in indexing
- [OpenConceptLab/ocl_issues#1753](https://github.com/OpenConceptLab/ocl_issues/issues/1753) | added mappings in repo version diff with verbosity levels | admin API to calculate checksums of repo resources
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | settting task state before start
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | fixing tests
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | fixing tests
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | children tasks to be uniq
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | fixing get task | added task which is same as id
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | setting finished_at on success
- fixing tests | merge conflicts
- duplicate method | merge conflicts
- [OpenConceptLab/ocl_issues#1777](https://github.com/OpenConceptLab/ocl_issues/issues/1777) | Async Task State Management using Postgres
- fixing test
- fixing test
- Fixing merge conflicts
- Merge branch 'master' into dev
- [OpenConceptLab/ocl_issues#1635](https://github.com/OpenConceptLab/ocl_issues/issues/1635) | mapping serializers | added latest_source_version
- [OpenConceptLab/ocl_issues#1635](https://github.com/OpenConceptLab/ocl_issues/issues/1635) | fixing tests
- [OpenConceptLab/ocl_issues#1635](https://github.com/OpenConceptLab/ocl_issues/issues/1635) | source repo version release/unreleased to reindex resources
- [OpenConceptLab/ocl_issues#1635](https://github.com/OpenConceptLab/ocl_issues/issues/1635) | Mapping search to have latest repo version field
- [OpenConceptLab/ocl_issues#1635](https://github.com/OpenConceptLab/ocl_issues/issues/1635) | fixing tests for default version HEAD when not global search
- [OpenConceptLab/ocl_issues#1635](https://github.com/OpenConceptLab/ocl_issues/issues/1635) | Search to use latest or latest released based on kwargs
- [OpenConceptLab/ocl_issues#1635](https://github.com/OpenConceptLab/ocl_issues/issues/1635) | returning latest_source_version in version detail API
- [OpenConceptLab/ocl_issues#1635](https://github.com/OpenConceptLab/ocl_issues/issues/1635) | fixing facets filters
- [OpenConceptLab/ocl_issues#1635](https://github.com/OpenConceptLab/ocl_issues/issues/1635) | fixing search latest attr
- [OpenConceptLab/ocl_issues#1635](https://github.com/OpenConceptLab/ocl_issues/issues/1635) | Concept search latest repo if search param there | return latest_source_version in list and detail only
- [OpenConceptLab/ocl_issues#1635](https://github.com/OpenConceptLab/ocl_issues/issues/1635) | fixing merge conflicts
- [OpenConceptLab/ocl_issues#1635](https://github.com/OpenConceptLab/ocl_issues/issues/1635) | removed auth group check
- [OpenConceptLab/ocl_issues#1635](https://github.com/OpenConceptLab/ocl_issues/issues/1635) | search with latest released repo version
- [OpenConceptLab/ocl_issues#1399](https://github.com/OpenConceptLab/ocl_issues/issues/1399) | not using atomic migrations | batching inserts/updates
- [OpenConceptLab/ocl_issues#1399](https://github.com/OpenConceptLab/ocl_issues/issues/1399) | using SQL raw queries in migrations
- Merge branch 'master' into dev
- Merge branch 'master' into dev
- [OpenConceptLab/ocl_issues#1399](https://github.com/OpenConceptLab/ocl_issues/issues/1399) | coverage to 92
- [OpenConceptLab/ocl_issues#1399](https://github.com/OpenConceptLab/ocl_issues/issues/1399) | fixing pylint
- Merge branch 'master' into dev
- Merge remote-tracking branch 'origin/issue_1399' into dev
- [OpenConceptLab/ocl_issues#1399](https://github.com/OpenConceptLab/ocl_issues/issues/1399) | updated coverage to 93
- [OpenConceptLab/ocl_issues#1399](https://github.com/OpenConceptLab/ocl_issues/issues/1399) | names/descriptions migrations to split names and descriptions
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | login/logout APIs to redirect to keycloak
- OpenMRS mapping validation schema to ignore old retired versions of mappings
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | added empty client id/secret vars
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | removed client login/logout redirect URL env vars
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | removed client id/secret as env vars
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | added comments
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | added comments
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | correcting readme
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | updated readme for dev setup with keycloak:
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | separating keycloak docker-compose file
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | removed dead code
- Merge branch 'master' into dev
- Fixing test | IntegrityError -> ValidationError
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | removed unused code
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | OID To Django Token API
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | fixing imports
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | custom auth/auth-backend to switch between django or OIDP | can use valid django token
- Merge branch 'master' into dev
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | API to migrate user from django to SSO
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | removing local client secret
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | logout from OIDP view
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | API to exchange code with OID token
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | settings for login redirect
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | middleware to manage token from session or from headers
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | OIDBackend | update user
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | refactoring and extracting routes
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | pylint fixes
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | Migrate user from django auth to OIDP method
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | user forgot password flow
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | user signup and mark verified views
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | temp enabling oidc endpoints
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | user token view to use auth service
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | auth service to inject django or OID provider auth
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | user instance can figure out OIDC token
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | refactoring views/mixins to have write import order
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | oidc settings and package
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | oidc overriden backend | to create and find user
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | docker-compose | env vars
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | KeyCloak service in docker-compose
##### 2.3.85 - Tue Apr 2 09:24:46 2024 +0000
- Errbit | handling URI generation for special characters in concept/mapping ID
- Errbit | concept extras list API should raise 404 if concept not found
- Errbit | handling URI generation for special characters in concept/mapping ID
- [OpenConceptLab/ocl_issues#1814](https://github.com/OpenConceptLab/ocl_issues/issues/1814) | separated retired results
##### 2.3.84 - Tue Apr 2 06:13:27 2024 +0000
- Source compare allowed for logged in users only
##### 2.3.83 - Mon Apr 1 11:21:40 2024 +0000
- [OpenConceptLab/ocl_issues#1817](https://github.com/OpenConceptLab/ocl_issues/issues/1817) | fixing pylint
- [OpenConceptLab/ocl_issues#1817](https://github.com/OpenConceptLab/ocl_issues/issues/1817) | fixing test
- [OpenConceptLab/ocl_issues#1742](https://github.com/OpenConceptLab/ocl_issues/issues/1742) | fixing pylint
- [OpenConceptLab/ocl_issues#1742](https://github.com/OpenConceptLab/ocl_issues/issues/1742) | url registry entry lookup to return entry relative URI
##### 2.3.82 - Wed Mar 27 03:32:58 2024 +0000
- [OpenConceptLab/ocl_issues#1806](https://github.com/OpenConceptLab/ocl_issues/issues/1806) | storing export time in version.extras | excluding extras.__<key> from checksums
- [OpenConceptLab/ocl_issues#1819](https://github.com/OpenConceptLab/ocl_issues/issues/1819) | removed print | pylint fix
- [OpenConceptLab/ocl_issues#1819](https://github.com/OpenConceptLab/ocl_issues/issues/1819) | batch delete to not use paginator/iterator
- [OpenConceptLab/ocl_issues#1819](https://github.com/OpenConceptLab/ocl_issues/issues/1819) | using paginator in place of iterator (#645)
- Story-OpenConceptLab/ocl_issues#1816 | URL Registry | search and facets
##### 2.3.81 - Tue Mar 26 06:56:18 2024 +0000
- [OpenConceptLab/ocl_issues#1816](https://github.com/OpenConceptLab/ocl_issues/issues/1816) | resolve to use lookup | lookup to handle version
- [OpenConceptLab/ocl_issues#1816](https://github.com/OpenConceptLab/ocl_issues/issues/1816) | url registry entry to cache resolved repo | repo save to update entries
##### 2.3.80 - Wed Mar 20 06:12:17 2024 +0000
- [OpenConceptLab/ocl_issues#1753](https://github.com/OpenConceptLab/ocl_issues/issues/1753) | updated response structure
##### 2.3.79 - Mon Mar 18 11:02:43 2024 +0000
Wed Mar 6 07:57:03 2024 +0530
Mon Dec 11 15:18:33 2023 +0530
- [OpenConceptLab/ocl_issues#1753](https://github.com/OpenConceptLab/ocl_issues/issues/1753) | find total of resources cheaper way
- [OpenConceptLab/ocl_issues#1753](https://github.com/OpenConceptLab/ocl_issues/issues/1753) | added mappings in repo version diff with verbosity levels | admin API to calculate checksums of repo resources
##### 2.3.78 - Mon Mar 18 03:20:51 2024 +0000
- [OpenConceptLab/ocl_issues#1809](https://github.com/OpenConceptLab/ocl_issues/issues/1809) | Mapping | added more fields to standard checksum
- [OpenConceptLab/ocl_issues#1775](https://github.com/OpenConceptLab/ocl_issues/issues/1775) | User detail serializers can include pins
- Enabling match search on users/orgs username/mnemonic attrs
- [OpenConceptLab/ocl_issues#1746](https://github.com/OpenConceptLab/ocl_issues/issues/1746) | returning 208 when concept/mapping update is unchanged
##### 2.3.77 - Tue Mar 12 07:22:24 2024 +0000
- [OpenConceptLab/ocl_issues#1794](https://github.com/OpenConceptLab/ocl_issues/issues/1794) | added external id in match search
##### 2.3.76 - Fri Mar 8 04:58:43 2024 +0000
- [OpenConceptLab/ocl_issues#1760](https://github.com/OpenConceptLab/ocl_issues/issues/1760) | fixing cascade queryset for report
- [OpenConceptLab/ocl_issues#1789](https://github.com/OpenConceptLab/ocl_issues/issues/1789) | fixing hyphen search
##### 2.3.75 - Thu Mar 7 04:33:13 2024 +0000
- [OpenConceptLab/ocl_issues#1788](https://github.com/OpenConceptLab/ocl_issues/issues/1788) | resolveReference operation to return resolved url registry entry relative URI as well
- [OpenConceptLab/ocl_issues#1760](https://github.com/OpenConceptLab/ocl_issues/issues/1760) | ordering summary in report
- [OpenConceptLab/ocl_issues#1760](https://github.com/OpenConceptLab/ocl_issues/issues/1760) | added more summaries in monthly usage report
##### 2.3.74 - Wed Mar 6 03:31:07 2024 +0000
- Merge pull request #644 from OpenConceptLab/contributor_start
- Contribution doc with getting started process
- Collection Reference Delete | Job to readd references to take less
- Collection reference remove timing out
- [OpenConceptLab/ocl_issues#1757](https://github.com/OpenConceptLab/ocl_issues/issues/1757) | added logo_url in users list response | removed redundant condition
- [OpenConceptLab/ocl_issues#1764](https://github.com/OpenConceptLab/ocl_issues/issues/1764) | reference expression cascade within valueset context
- Added 3 map types
- Updated db index for concept/mapping repo resources query
- Collection concepts/mappings view to set instance on request for references
- [OpenConceptLab/ocl_issues#1756](https://github.com/OpenConceptLab/ocl_issues/issues/1756) | added logo url in org list response
- [OpenConceptLab/ocl_issues#1760](https://github.com/OpenConceptLab/ocl_issues/issues/1760) | fixing sorting
- [OpenConceptLab/ocl_issues#1760](https://github.com/OpenConceptLab/ocl_issues/issues/1760) | fixing monthly report mapping count
- Migrations for indexes
- [OpenConceptLab/ocl_issues#1756](https://github.com/OpenConceptLab/ocl_issues/issues/1756) | user summary to have bookmark count
- Concept/Mapping Indexes for repo version listing
- [OpenConceptLab/ocl_issues#1746](https://github.com/OpenConceptLab/ocl_issues/issues/1746) | fixing toggles fixtures
- [OpenConceptLab/ocl_issues#1746](https://github.com/OpenConceptLab/ocl_issues/issues/1746) | concept/mapping new version creation error
- [OpenConceptLab/ocl_issues#1746](https://github.com/OpenConceptLab/ocl_issues/issues/1746) | concept/mapping new version creation to compare checksum and fail if same
- Errbit | fixing autoexand off collection summary
- [OpenConceptLab/ocl_issues#1746](https://github.com/OpenConceptLab/ocl_issues/issues/1746) | Concepts/Mappings standard checksums | added external_id, retired and descriptions
- [OpenConceptLab/ocl_issues#1746](https://github.com/OpenConceptLab/ocl_issues/issues/1746) | refactoring | extracting mapping/concept common method for version creation
- [OpenConceptLab/ocl_issues#1746](https://github.com/OpenConceptLab/ocl_issues/issues/1746) | refactoring mappings version create method
- [OpenConceptLab/ocl_issues#1746](https://github.com/OpenConceptLab/ocl_issues/issues/1746) | refactoring concepts version create method
- [OpenConceptLab/ocl_issues#1747](https://github.com/OpenConceptLab/ocl_issues/issues/1747) | Mappings to use resolve operations
- Update README.md
- Update README.md
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | added toggle for canonical resolution
- Trying to address verify_certs=False not working
- Follow up to adding ES_VERIFY_CERTS and proper handling of bool
- Add ES_VERIFY_CERTS to support https traffic without configuring certificates
- Merge pull request #643 from OpenConceptLab/issue-1729
- [OpenConceptLab/ocl_issues#1729](https://github.com/OpenConceptLab/ocl_issues/issues/1729) | buffered file reading
- [OpenConceptLab/ocl_issues#1729](https://github.com/OpenConceptLab/ocl_issues/issues/1729) | removed unused package
- [OpenConceptLab/ocl_issues#1729](https://github.com/OpenConceptLab/ocl_issues/issues/1729) | Azure Blob Storage Class for exports/uploads
- utils | removed unused methods and added missing tests
- [OpenConceptLab/ocl_issues#1691](https://github.com/OpenConceptLab/ocl_issues/issues/1691) | checksums repo version diff | return new mnemonics
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | removed redundant check
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | refactoring entry and lookup methods
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | raise 404 if owner is provided in request URL but not found
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | lookup operation is not using resolve reference operation
- Adding missing test coverage
- Removed system/admin API to dedupe concept versions
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | resolve reference operation to resolve canonical using new rules of url registry
- [OpenConceptLab/ocl_issues#1691](https://github.com/OpenConceptLab/ocl_issues/issues/1691) | extract checksums diff class
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | URL registry lookup operation | using owner entries or global not both
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | URL registry lookup operation | fixing when repo not found
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | URL registry lookup operation
- [OpenConceptLab/ocl_issues#1691](https://github.com/OpenConceptLab/ocl_issues/issues/1691) | source compare is not swagger ready
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | URL registry | updated type
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | URL registry | search scopes and owner
- [OpenConceptLab/ocl_issues#1691](https://github.com/OpenConceptLab/ocl_issues/issues/1691) | fixing pylint
- [OpenConceptLab/ocl_issues#1691](https://github.com/OpenConceptLab/ocl_issues/issues/1691) | source version compare API using checksums
- [OpenConceptLab/ocl_issues#1735](https://github.com/OpenConceptLab/ocl_issues/issues/1735) | version exports | fixing test
- [OpenConceptLab/ocl_issues#1735](https://github.com/OpenConceptLab/ocl_issues/issues/1735) | version exports | added time taken
- Turning on checksums toggle on staging/prod
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | URL registry | fixing namespace and uniq clauses
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | URL registry | fixing namespace and uniq clauses
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | fixing pylint
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | URL registry CRUD
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | fixing uniq check
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | fixing pylint
- Collections | added search meta results
- [OpenConceptLab/ocl_issues#1732](https://github.com/OpenConceptLab/ocl_issues/issues/1732) | Global/Org/User URL Registry List/Search/Create APIs
- [OpenConceptLab/ocl_issues#1729](https://github.com/OpenConceptLab/ocl_issues/issues/1729) | Refactoring services | creating dir structure
- [OpenConceptLab/ocl_issues#1729](https://github.com/OpenConceptLab/ocl_issues/issues/1729) | refactoring export service to have an interface for other cloud service to implement
- [OpenConceptLab/ocl_issues#1728](https://github.com/OpenConceptLab/ocl_issues/issues/1728) Fixing test
- [OpenConceptLab/ocl_issues#1728](https://github.com/OpenConceptLab/ocl_issues/issues/1728) Support db user, redis and es credentials
- [OpenConceptLab/ocl_issues#957](https://github.com/OpenConceptLab/ocl_issues/issues/957) | fixing typo
- [OpenConceptLab/ocl_issues#957](https://github.com/OpenConceptLab/ocl_issues/issues/957) | importers | indexing job to take a bigger batch and iterate using iterator
- [OpenConceptLab/ocl_issues#957](https://github.com/OpenConceptLab/ocl_issues/issues/957) | importers | indexing current latest and version objects for concept/mapping
- [OpenConceptLab/ocl_issues#957](https://github.com/OpenConceptLab/ocl_issues/issues/957) | importers | using pop more
- [OpenConceptLab/ocl_issues#957](https://github.com/OpenConceptLab/ocl_issues/issues/957) | ES batch indexes | extract doc
- [OpenConceptLab/ocl_issues#957](https://github.com/OpenConceptLab/ocl_issues/issues/957) | DB Indexes for source/collection/mapping/concept for ES indexing
- removed ES strict mapping from concepts/mappings
- [OpenConceptLab/ocl_issues#1720](https://github.com/OpenConceptLab/ocl_issues/issues/1720) | added logs for Openmrs validation schema collection conflicting name
- [OpenConceptLab/ocl_issues#1682](https://github.com/OpenConceptLab/ocl_issues/issues/1682) | Added index
- [OpenConceptLab/ocl_issues#1686](https://github.com/OpenConceptLab/ocl_issues/issues/1686) | Repos facets class
- [OpenConceptLab/ocl_issues#1703](https://github.com/OpenConceptLab/ocl_issues/issues/1703) | removed unused facets
- [OpenConceptLab/ocl_issues#1682](https://github.com/OpenConceptLab/ocl_issues/issues/1682) | Auto assign sort order based on max prev sort order
- [OpenConceptLab/ocl_issues#1713](https://github.com/OpenConceptLab/ocl_issues/issues/1713) Fix reading DB_CURSOR_ON env
- [OpenConceptLab/ocl_issues#1710](https://github.com/OpenConceptLab/ocl_issues/issues/1710) | facets exlusions for global scope
- [OpenConceptLab/ocl_issues#1713](https://github.com/OpenConceptLab/ocl_issues/issues/1713) POST CodeSystem and ValueSet fails due to DB cursor being unsupported
- [OpenConceptLab/ocl_issues#1710](https://github.com/OpenConceptLab/ocl_issues/issues/1710) | facets to not include source versions in global results
- [OpenConceptLab/ocl_issues#1710](https://github.com/OpenConceptLab/ocl_issues/issues/1710) | added swagger header for latest repo search header
- [OpenConceptLab/ocl_issues#1710](https://github.com/OpenConceptLab/ocl_issues/issues/1710) | include latest search by header
- [OpenConceptLab/ocl_issues#937](https://github.com/OpenConceptLab/ocl_issues/issues/937) | updated Readme:
- [OpenConceptLab/ocl_issues#937](https://github.com/OpenConceptLab/ocl_issues/issues/937) | disabling profiler by default in dev env
- [OpenConceptLab/ocl_issues#937](https://github.com/OpenConceptLab/ocl_issues/issues/937) | django-silk profiler
- [OpenConceptLab/ocl_issues#1709](https://github.com/OpenConceptLab/ocl_issues/issues/1709) | fixing pylint
- [OpenConceptLab/ocl_issues#1683](https://github.com/OpenConceptLab/ocl_issues/issues/1683) | Source setting for autoid uuid for locales
- [OpenConceptLab/ocl_issues#1709](https://github.com/OpenConceptLab/ocl_issues/issues/1709) | OpenMRS Validation schema to accept all different forms of index term and short name name types
- Add ability to only clear org
- [OpenConceptLab/ocl_issues#1415](https://github.com/OpenConceptLab/ocl_issues/issues/1415) Adjusting import script
- [OpenConceptLab/ocl_issues#1702](https://github.com/OpenConceptLab/ocl_issues/issues/1702) | SSO registration url redirect API
- Fixing swagger error
- [OpenConceptLab/ocl_issues#1663](https://github.com/OpenConceptLab/ocl_issues/issues/1663) | fixing test
- [OpenConceptLab/ocl_issues#1663](https://github.com/OpenConceptLab/ocl_issues/issues/1663) | added company and location to user from SSO claims
- API/tasks to remove duplicate versions of concepts | adding logs
- API/tasks to remove duplicate versions of concepts
- Revert "Added default keycloak env var for local"
- Added default keycloak env var for local
- Removed retry policy
- Never retry bulk import tasks
- batch index to have single batch mode for source/collections of an org/user
- fixing tests
- batch index to use iterator in non test mode
- batch index to use iterator on queryset and not limit offset
- [OpenConceptLab/ocl_issues#1701](https://github.com/OpenConceptLab/ocl_issues/issues/1701) | search by canonical url
- [OpenConceptLab/ocl_issues#1686](https://github.com/OpenConceptLab/ocl_issues/issues/1686) | removed unused import
- [OpenConceptLab/ocl_issues#1686](https://github.com/OpenConceptLab/ocl_issues/issues/1686) | user org repo search API
- [OpenConceptLab/ocl_issues#1686](https://github.com/OpenConceptLab/ocl_issues/issues/1686) | Repos search API
- [OpenConceptLab/ocl_issues#1415](https://github.com/OpenConceptLab/ocl_issues/issues/1415) Fixing tests
- [OpenConceptLab/ocl_issues#1415](https://github.com/OpenConceptLab/ocl_issues/issues/1415) Implement automated import scripts for FHIR HL7 content
- Root View | URL to have correct HOST scheme
- [OpenConceptLab/ocl_issues#1679](https://github.com/OpenConceptLab/ocl_issues/issues/1679) | removing ref concepts/mappings correctly
- [OpenConceptLab/ocl_issues#1674](https://github.com/OpenConceptLab/ocl_issues/issues/1674) | Adding checksums to bundle response | checksums in all concept/mapping responses
- [OpenConceptLab/ocl_issues#1674](https://github.com/OpenConceptLab/ocl_issues/issues/1674) | added checksums in export
- [OpenConceptLab/ocl_issues#1674](https://github.com/OpenConceptLab/ocl_issues/issues/1674) | reafactoring and adding tests
- [OpenConceptLab/ocl_issues#1674](https://github.com/OpenConceptLab/ocl_issues/issues/1674) | cleaning checksum model methods
- [OpenConceptLab/ocl_issues#1674](https://github.com/OpenConceptLab/ocl_issues/issues/1674) | Adding checksums response headers
- [OpenConceptLab/ocl_issues#1674](https://github.com/OpenConceptLab/ocl_issues/issues/1674) | Adding checksums calculations
- [OpenConceptLab/ocl_issues#1674](https://github.com/OpenConceptLab/ocl_issues/issues/1674) | reverting algo back | added more test
- [OpenConceptLab/ocl_issues#1674](https://github.com/OpenConceptLab/ocl_issues/issues/1674) | standard and smart checksums for orgs and users
- [OpenConceptLab/ocl_issues#1674](https://github.com/OpenConceptLab/ocl_issues/issues/1674) | Checksum Algo | not using sorting in values
- [OpenConceptLab/ocl_issues#1674](https://github.com/OpenConceptLab/ocl_issues/issues/1674) | fixing typo
- [OpenConceptLab/ocl_issues#1674](https://github.com/OpenConceptLab/ocl_issues/issues/1674) | standard and smart checksums should ignore None and retired/is_active when its false
- [OpenConceptLab/ocl_issues#1674](https://github.com/OpenConceptLab/ocl_issues/issues/1674) | standard and smart checksums for source/collections
- [OpenConceptLab/ocl_issues#1674](https://github.com/OpenConceptLab/ocl_issues/issues/1674) | standard and smart checksums for concepts/mappings
- Concepts/Mapping updated by dynamic string mapping for ES
- Concepts/Mapping updated by dynamic string mapping for ES
- [OpenConceptLab/ocl_issues#1675](https://github.com/OpenConceptLab/ocl_issues/issues/1675) | adding verbosity to exception
- Removed data migration for updated by API/tasks
- Revert "OpenConceptLab/ocl_issues#1664 | added logs for concept next valid ID"
- [OpenConceptLab/ocl_issues#1664](https://github.com/OpenConceptLab/ocl_issues/issues/1664) | casting mnemonic to int
- [OpenConceptLab/ocl_issues#1664](https://github.com/OpenConceptLab/ocl_issues/issues/1664) | added logs for concept next valid ID
- pylint | remove unused import
- Fixing batch index processing when filters are None
- Fixing collection/verison hard delete failure | should delete references and expansions first
- [OpenConceptLab/ocl_issues#1672](https://github.com/OpenConceptLab/ocl_issues/issues/1672) | remove duplicate lookup values
- [OpenConceptLab/ocl_issues#1664](https://github.com/OpenConceptLab/ocl_issues/issues/1664) | mapping/concept create to verify parent autoid seq next valiud id
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | Mapping initial version to have the right updated_by:
- [OpenConceptLab/ocl_issues#1664](https://github.com/OpenConceptLab/ocl_issues/issues/1664) | clone process to verify auto id seq after excluding non-number IDs
- [OpenConceptLab/ocl_issues#1664](https://github.com/OpenConceptLab/ocl_issues/issues/1664) | clone process to verify auto id for concept/mapping
- [OpenConceptLab/ocl_issues#1670](https://github.com/OpenConceptLab/ocl_issues/issues/1670) | swagger request body for references delete
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | using outer ref for mapping update by update
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | updated lables/notes
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | not using iterator | connection pool doesn't like it.
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | reduced chunk size
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | removed newline
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | Updated/Refactored Resources usage report
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | fixing mappings API for concepts
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | ignoring coverage
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | refactoring queryset for better lookup
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | renaming var and not indexing
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | using iterator and chunks for migration
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | refactoring tasks
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | refactoring tasks to be more efficient
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | Concept/Mapping updated_by migration via API/task
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | Concept/Mapping migration to update updated by
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | Concept/Mapping version create should updated updated by on versioned object
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | fixing pylint
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | added updated by in indexes | Repo summary to have contributors | Facets to have updated by filters
- [OpenConceptLab/ocl_issues#1415](https://github.com/OpenConceptLab/ocl_issues/issues/1415) Implement automated import scripts for FHIR HL7 content
- Merge pull request #581 from IanMinash/codesystemlookup-fix
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | fixing test
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | Resource usage reports | updated swagger | can add custom dates
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | Resource usage reports | feedbacks and refactoring
- Switch body to OperationOutcome for empty queryset
##### 2.3.50 - Sat Oct 7 00:24:12 2023 +0000
- Revert "OpenConceptLab/ocl_issues#1664 | added logs for concept next valid ID"
- [OpenConceptLab/ocl_issues#1664](https://github.com/OpenConceptLab/ocl_issues/issues/1664) | casting mnemonic to int
- [OpenConceptLab/ocl_issues#1664](https://github.com/OpenConceptLab/ocl_issues/issues/1664) | added logs for concept next valid ID
- pylint | remove unused import
- Fixing batch index processing when filters are None
- Fixing collection/verison hard delete failure | should delete references and expansions first
- [OpenConceptLab/ocl_issues#1672](https://github.com/OpenConceptLab/ocl_issues/issues/1672) | remove duplicate lookup values
- [OpenConceptLab/ocl_issues#1664](https://github.com/OpenConceptLab/ocl_issues/issues/1664) | mapping/concept create to verify parent autoid seq next valiud id
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | Mapping initial version to have the right updated_by:
- [OpenConceptLab/ocl_issues#1664](https://github.com/OpenConceptLab/ocl_issues/issues/1664) | clone process to verify auto id seq after excluding non-number IDs
- [OpenConceptLab/ocl_issues#1664](https://github.com/OpenConceptLab/ocl_issues/issues/1664) | clone process to verify auto id for concept/mapping
- [OpenConceptLab/ocl_issues#1670](https://github.com/OpenConceptLab/ocl_issues/issues/1670) | swagger request body for references delete
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | using outer ref for mapping update by update
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | updated lables/notes
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | not using iterator | connection pool doesn't like it.
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | reduced chunk size
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | removed newline
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | Updated/Refactored Resources usage report
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | fixing mappings API for concepts
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | ignoring coverage
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | refactoring queryset for better lookup
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | renaming var and not indexing
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | using iterator and chunks for migration
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | refactoring tasks
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | refactoring tasks to be more efficient
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | Concept/Mapping updated_by migration via API/task
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | Concept/Mapping migration to update updated by
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | Concept/Mapping version create should updated updated by on versioned object
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | fixing pylint
- [OpenConceptLab/ocl_issues#1659](https://github.com/OpenConceptLab/ocl_issues/issues/1659) | added updated by in indexes | Repo summary to have contributors | Facets to have updated by filters
- [OpenConceptLab/ocl_issues#1415](https://github.com/OpenConceptLab/ocl_issues/issues/1415) Implement automated import scripts for FHIR HL7 content
- Merge pull request #581 from IanMinash/codesystemlookup-fix
- Switch body to OperationOutcome for empty queryset
##### 2.3.49 - Tue Sep 5 07:38:46 2023 +0000
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | fixing test
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | Resource usage reports | updated swagger | can add custom dates
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | Resource usage reports | feedbacks and refactoring
##### 2.3.48 - Fri Sep 1 04:01:08 2023 +0000
- [OpenConceptLab/ocl_issues#1656](https://github.com/OpenConceptLab/ocl_issues/issues/1656) | fixing facets when no search criteria is given
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | correcting name of task
##### 2.3.47 - Wed Aug 30 10:08:57 2023 +0000
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | correcting concept/mapping retired criteria
##### 2.3.46 - Wed Aug 30 07:25:28 2023 +0000
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | correcting blank rows | removed duplicates
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | added blank row
##### 2.3.45 - Tue Aug 29 03:42:58 2023 +0000
- [OpenConceptLab/ocl_issues#1587](https://github.com/OpenConceptLab/ocl_issues/issues/1587) | correcting wildcard
- [OpenConceptLab/ocl_issues#1587](https://github.com/OpenConceptLab/ocl_issues/issues/1587) | fixing pylint
- [OpenConceptLab/ocl_issues#1587](https://github.com/OpenConceptLab/ocl_issues/issues/1587) | must have and must not have with correct operators +/-
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | fixing test
- [OpenConceptLab/ocl_issues#1633](https://github.com/OpenConceptLab/ocl_issues/issues/1633) | Monthly usage report refactoring and using CSV format
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, fix timeout
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, move retry_policy
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, move socket_timeout_* for result_backend
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, move socket_timeout_
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, disable broker heartbeat
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, fix timeouts
- using search queries to get facets
- Removed exact match param from swagger
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, fix Retry kombu error
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, fix sentinels list
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, using sentinel_kwargs
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, use merge
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, fixing settings.py
- [OpenConceptLab/ocl_issues#1587](https://github.com/OpenConceptLab/ocl_issues/issues/1587) | must have in search using quotes
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, fix formatting
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, unify retry policy
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | optimizing/refactoring search queries
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, another attempt at setting timeouts
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | Concept search term/prefix to use keyword name field
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, enable back heartbeat
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, fixing formatting
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, fixing celery_once
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, fixing celery startup
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, fixing celery config
##### 2.3.44 - Wed Aug 23 03:46:34 2023 +0000
- Fixing celery once config
##### 2.3.43 - Tue Aug 22 12:35:09 2023 +0000
- [OpenConceptLab/ocl_issues#730](https://github.com/OpenConceptLab/ocl_issues/issues/730) Implement clustering for ES, set retries and sniffing
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, adding cache and celery_once retries
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, adding result backend retries
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, adjusting task publish retries
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, setup timeouts and retries
##### 2.3.42 - Mon Aug 21 06:05:43 2023 +0000
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | Concept search to use text field name and not keyword field
- Bump gunicorn from 20.1.0 to 21.2.0 (#562)
- Bump factory-boy from 3.2.1 to 3.3.0 (#546)
- Bump pylint from 2.17.4 to 2.17.5 (#560)
- Bump coverage from 7.2.7 to 7.3.0 (#564)
- Bump django-cors-headers from 4.1.0 to 4.2.0 (#547)
- Bump django from 4.2.3 to 4.2.4 (#559)
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, correct location
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, add missing SentinelConnectionFactory
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, attempt to fix RedisService
- Bump markdown from 3.4.3 to 3.4.4 (#556)
- Bump psycopg2 from 2.9.6 to 2.9.7 (#555)
- Bump pydash from 7.0.4 to 7.0.6 (#557)
- Bump mock from 5.0.2 to 5.1.0 (#558)
##### 2.3.41 - Wed Aug 9 03:30:33 2023 +0000
- [OpenConceptLab/ocl_issues#1646](https://github.com/OpenConceptLab/ocl_issues/issues/1646) | Collection References | ignoring new search parameters
- [OpenConceptLab/ocl_issues#1645](https://github.com/OpenConceptLab/ocl_issues/issues/1645) | Source/Collection | canonical_url is searchable by phrase
- [OpenConceptLab/ocl_issues#1595](https://github.com/OpenConceptLab/ocl_issues/issues/1595) | Removed migrate from old export path to new | remove repo old export path code
- Revert "OpenConceptLab/ocl_issues#1595 | coverage fix"
- Revert "OpenConceptLab/ocl_issues#1595 | collections export migrate"
- Revert "Admin API to dedupe concept/mapping latest-versions"
##### 2.3.40 - Thu Aug 3 02:51:49 2023 +0000
- ES | returning real exception
- ES | returning real exception
- Added use_ssl and verify certs for https schema for ES connection configuration
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing search tests for fuzzy search on ID
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing search tests for fuzzy search on ID
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing search tests for fuzzy search on ID
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing search tests for fuzzy search on ID
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | Fuzzy search is not applied on id and codes
##### 2.3.39 - Sat Jul 29 00:59:32 2023 +0000
- Admin API to dedupe concept/mapping latest-versions
- Temporarily lower required coverage
-  OpenConceptLab/ocl_issues#927 Redis clustering, adding connection pool class
- Increasing test coverage
- [OpenConceptLab/ocl_issues#1410](https://github.com/OpenConceptLab/ocl_issues/issues/1410) Schedule vacuum and analyse DB
- Fixing concept/mapping list queryset
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | Concept Index | synonyms not lowercase
- Merge pull request #545 from Salaton/dynamic_es_scheme
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, fix tests
- chore: make scheme configurable for elastic search connection
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, fixing build
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, fixing redis healthcheck
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, fixing formatting
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering, fixing healthcheck
- Upgrading redis and celery to support sentinel
- Adding gevent for celery
- [OpenConceptLab/ocl_issues#1501](https://github.com/OpenConceptLab/ocl_issues/issues/1501) | fixing pylint errors
- Upgrading pyyaml to fix build
- Revert "Revert "OpenConceptLab/ocl_issues#1588 | Can except OCL Source version export for import into same or different owner and as same or different version""
- Revert "Revert "OpenConceptLab/ocl_issues#1501 | Accepting zip format in importers""
- Revert "OpenConceptLab/ocl_issues#1501 | Accepting zip format in importers"
- Revert "OpenConceptLab/ocl_issues#1588 | Can except OCL Source version export for import into same or different owner and as same or different version"
- [OpenConceptLab/ocl_issues#1588](https://github.com/OpenConceptLab/ocl_issues/issues/1588) | Can except OCL Source version export for import into same or different owner and as same or different version
- [OpenConceptLab/ocl_issues#1501](https://github.com/OpenConceptLab/ocl_issues/issues/1501) | Accepting zip format in importers
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing pylint
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | concept word match on name keyword field
- [OpenConceptLab/ocl_issues#1595](https://github.com/OpenConceptLab/ocl_issues/issues/1595) | collections export migrate
- [OpenConceptLab/ocl_issues#1595](https://github.com/OpenConceptLab/ocl_issues/issues/1595) | coverage fix
- [OpenConceptLab/ocl_issues#1595](https://github.com/OpenConceptLab/ocl_issues/issues/1595) | Source/Collection version export path and file name to be more user friendly
- Upgrading ES and adding Kibana
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | Expansion text paramter and reference expression to use exact match search only
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | Expansion text paramter and reference expression to use exact match search only
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | Expansion text paramter and reference expression to use exact match search only
- Adding mapping serializer tests
- Refactoring | extracting truthy values
- Mapping serializers | passing context to nested concept serializers
- Fixing formatting
- [OpenConceptLab/ocl_issues#927](https://github.com/OpenConceptLab/ocl_issues/issues/927) Redis clustering
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | indexing API | can pass filters
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing search tests
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing search tests
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing search tests
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing tests
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing tests
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing tests
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | external_id match should be exact (term match) only
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing tests
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing search serializer and tests
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | removed duplicate method
##### 2.3.38 - Sat Jul 8 02:57:50 2023 +0000
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | Search updates
- [OpenConceptLab/ocl_issues#1598](https://github.com/OpenConceptLab/ocl_issues/issues/1598) | fixing pylint
- Revert "Bump drf-yasg from 1.21.5 to 1.21.6 (#513)"
- [OpenConceptLab/ocl_issues#1598](https://github.com/OpenConceptLab/ocl_issues/issues/1598) | concept extras keys for source in summary
- Bump tblib from 1.7.0 to 2.0.0 (#516)
- Bump drf-yasg from 1.21.5 to 1.21.6 (#513)
- Bump django from 4.2.2 to 4.2.3 (#528)
##### 2.3.37 - Thu Jun 29 06:05:22 2023 +0000
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing test
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing test
- [OpenConceptLab/ocl_issues#1471](https://github.com/OpenConceptLab/ocl_issues/issues/1471) | added toggle
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | updated concepts/mappings exact match fields
##### 2.3.36 - Wed Jun 28 14:00:21 2023 +0000
- [OpenConceptLab/ocl_issues#730](https://github.com/OpenConceptLab/ocl_issues/issues/730) Fix hosts
##### 2.3.35 - Wed Jun 28 13:20:49 2023 +0000
- Fixing pylint
- [OpenConceptLab/ocl_issues#730](https://github.com/OpenConceptLab/ocl_issues/issues/730) Implement clustering for ES
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing pylint
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing tests
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | Concept searchable through target codes
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | User name is sortable and exact match searchable
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | fixing test
- Revert "Bump celery[redis] from 5.2.7 to 5.3.1 (#511)"
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | Concept Search | added highlighting
- [OpenConceptLab/ocl_issues#1583](https://github.com/OpenConceptLab/ocl_issues/issues/1583) | Enhancing search API
- Bump celery[redis] from 5.2.7 to 5.3.1 (#511)
- Bump django-cors-headers from 3.14.0 to 4.1.0 (#514)
- Revert "Bump drf-yasg from 1.21.5 to 1.21.6 (#509)"
- Bump drf-yasg from 1.21.5 to 1.21.6 (#509)
- Bump whitenoise from 6.4.0 to 6.5.0 (#507)
- Bump kombu from 5.3.0 to 5.3.1 (#506)
- Bump coverage from 6.5.0 to 7.2.7 (#486)
- Bump pydash from 7.0.3 to 7.0.4 (#504)
- Bump pylint from 2.17.2 to 2.17.4 (#499)
- Update README.md | added ES indexing commands
- Bump kombu from 5.2.4 to 5.3.0 (#501)
- Bump mock from 5.0.1 to 5.0.2 (#498)
- Bump psycopg2 from 2.9.5 to 2.9.6 (#495)
- Bump django from 4.1.9 to 4.2.2 (#496)
- Bump requests from 2.28.1 to 2.31.0 (#479)
- Bump redis from 4.5.4 to 4.5.5 (#470)
- Bump markdown from 3.4.1 to 3.4.3 (#469)
##### 2.3.34 - Thu Jun 8 08:41:26 2023 +0000
- [OpenConceptLab/ocl_issues#1591](https://github.com/OpenConceptLab/ocl_issues/issues/1591) | fixing retired + exact match/wildcard search/facets
- [OpenConceptLab/ocl_issues#1593](https://github.com/OpenConceptLab/ocl_issues/issues/1593) | fixing mapping from/to concept code search
##### 2.3.33 - Wed May 24 01:52:16 2023 +0000
- Refactoring Mapping resolve relations
- [OpenConceptLab/ocl_issues#1561](https://github.com/OpenConceptLab/ocl_issues/issues/1561) | Org delete async by default
- [OpenConceptLab/ocl_issues#1549](https://github.com/OpenConceptLab/ocl_issues/issues/1549) | fixing typo
- [OpenConceptLab/ocl_issues#1584](https://github.com/OpenConceptLab/ocl_issues/issues/1584) | added default/supported-locales in source version verbose summary
- [OpenConceptLab/ocl_issues#1549](https://github.com/OpenConceptLab/ocl_issues/issues/1549) | Source Auto id attributes editable
##### 2.3.32 - Mon May 22 04:14:06 2023 +0000
- Fixing collections/sources urls for org/users
##### 2.3.31 - Thu May 18 03:13:44 2023 +0000
- [OpenConceptLab/ocl_issues#1579](https://github.com/OpenConceptLab/ocl_issues/issues/1579) | fixing tests
- [OpenConceptLab/ocl_issues#1579](https://github.com/OpenConceptLab/ocl_issues/issues/1579) | collection/source minimal versions serializers
##### 2.3.30 - Mon May 15 09:03:30 2023 +0000
- Expansions refactoring | using either expansion system parameter or ref's system, not both
##### 2.3.29 - Sun May 14 15:48:36 2023 +0000
- Expansions | on new expansion creation re-evaluation references deduping system/valueset versions
##### 2.3.28 - Sun May 14 14:26:04 2023 +0000
- Expansions | on new expansion creation re-evaluation of all references fixing performance issues
- Expansions | on new expansion creation re-evaluation of all references should cache system version resolves
##### 2.3.27 - Sun May 14 11:52:05 2023 +0000
- fixing typo
- Adding concepts/mappings to expansion query to load less in memory
- [OpenConceptLab/ocl_issues#1551](https://github.com/OpenConceptLab/ocl_issues/issues/1551) | removed deprecated importers urls from root view
- Logging middleware on top
##### 2.3.26 - Fri May 12 02:17:45 2023 +0000
- removed unused import
- Errbit | hardcoded user's org uri method
- Bump django-dirtyfields from 1.9.1 to 1.9.2 (#453)
- Bump pydash from 6.0.2 to 7.0.3 (#464)
- Bump django from 4.1.7 to 4.1.9 (#468)
##### 2.3.25 - Tue May 9 13:56:35 2023 +0000
- Revert "Moving logging middleware on top"
##### 2.3.24 - Tue May 9 13:38:57 2023 +0000
- Moving logging middleware on top
- Errbit | hardcoded user/org calculate uri method
- [OpenConceptLab/ocl_issues#1561](https://github.com/OpenConceptLab/ocl_issues/issues/1561) | source/collection delete always async and waits for result
##### 2.3.23 - Thu Apr 27 14:00:41 2023 +0000
- removed overloading of equal and hash
##### 2.3.22 - Thu Apr 27 13:35:39 2023 +0000
- Source/collection delete | added errbit and more logs
- [OpenConceptLab/ocl_issues#1551](https://github.com/OpenConceptLab/ocl_issues/issues/1551) | Deprecated legacy importers
##### 2.3.21 - Thu Apr 27 03:50:58 2023 +0000
- Merge branch 'master' of github.com:OpenConceptLab/oclapi2
- [OpenConceptLab/ocl_issues#1566](https://github.com/OpenConceptLab/ocl_issues/issues/1566) | collection reference cascade to consider more parameters
- Bump django-celery-beat from 2.4.0 to 2.5.0 (#440)
- Bump django-ordered-model from 3.7.1 to 3.7.4 (#441)
- [OpenConceptLab/ocl_issues#1561](https://github.com/OpenConceptLab/ocl_issues/issues/1561) | Collection references add is always async | waiting 15 seconds to finish
##### 2.3.20 - Wed Apr 26 04:01:39 2023 +0000
- [OpenConceptLab/ocl_issues#1562](https://github.com/OpenConceptLab/ocl_issues/issues/1562) | async references add to not have any cascade limit
- [OpenConceptLab/ocl_issues#1213](https://github.com/OpenConceptLab/ocl_issues/issues/1213) | checksums for source/collection metadata
- [OpenConceptLab/ocl_issues#1564](https://github.com/OpenConceptLab/ocl_issues/issues/1564) | removed name from importers
- [OpenConceptLab/ocl_issues#1564](https://github.com/OpenConceptLab/ocl_issues/issues/1564) | removed name from concept serializers
- [OpenConceptLab/ocl_issues#1564](https://github.com/OpenConceptLab/ocl_issues/issues/1564) | removed name from concept factory
- [OpenConceptLab/ocl_issues#1564](https://github.com/OpenConceptLab/ocl_issues/issues/1564) | removed concept unused fields
- [OpenConceptLab/ocl_issues#1564](https://github.com/OpenConceptLab/ocl_issues/issues/1564) | removed concept unused fields
##### 2.3.19 - Tue Apr 25 03:33:40 2023 +0000
- [OpenConceptLab/ocl_issues#1563](https://github.com/OpenConceptLab/ocl_issues/issues/1563) | cascade and transform | dedupe references
##### 2.3.18 - Tue Apr 25 02:40:29 2023 +0000
- [OpenConceptLab/ocl_issues#1557](https://github.com/OpenConceptLab/ocl_issues/issues/1557) | fixing export url check
##### 2.3.17 - Tue Apr 25 02:09:08 2023 +0000
- [OpenConceptLab/ocl_issues#1559](https://github.com/OpenConceptLab/ocl_issues/issues/1559) | Source/Concept create to update mappings attributes asynchronously
- [OpenConceptLab/ocl_issues#1213](https://github.com/OpenConceptLab/ocl_issues/issues/1213) | checksums | updated fields
- [OpenConceptLab/ocl_issues#1213](https://github.com/OpenConceptLab/ocl_issues/issues/1213) | added checksum toggle
- [OpenConceptLab/ocl_issues#1213](https://github.com/OpenConceptLab/ocl_issues/issues/1213) | checksum save to not updated updated_at
- [OpenConceptLab/ocl_issues#1213](https://github.com/OpenConceptLab/ocl_issues/issues/1213) | fixing task
- [OpenConceptLab/ocl_issues#1213](https://github.com/OpenConceptLab/ocl_issues/issues/1213) | computing checksums on association changes
- Upgrade to pylint 2.17.2
- [OpenConceptLab/ocl_issues#1213](https://github.com/OpenConceptLab/ocl_issues/issues/1213) | concept/mapping checksums to recompute on version creation | locales to return checksum | locales to use name/description_type and not type in checksums
- [OpenConceptLab/ocl_issues#1556](https://github.com/OpenConceptLab/ocl_issues/issues/1556) Fix formatting
- [OpenConceptLab/ocl_issues#1556](https://github.com/OpenConceptLab/ocl_issues/issues/1556) CodeSystem/ returns result if code and system missing
- [OpenConceptLab/ocl_issues#1213](https://github.com/OpenConceptLab/ocl_issues/issues/1213) | checksum algo to return same checksum for array of one element vs element
- [OpenConceptLab/ocl_issues#1213](https://github.com/OpenConceptLab/ocl_issues/issues/1213) | API for  operation
- resolve reference operation | correcting url for swagger and added swagger schema
- [OpenConceptLab/ocl_issues#1213](https://github.com/OpenConceptLab/ocl_issues/issues/1213) | fixing typo
- [OpenConceptLab/ocl_issues#1213](https://github.com/OpenConceptLab/ocl_issues/issues/1213) | toggle for checksums
- [OpenConceptLab/ocl_issues#1213](https://github.com/OpenConceptLab/ocl_issues/issues/1213) | Checksums for concept/mapping-versions
- Fixing Source create for supported locales
- Collections Export | fixing export check
##### 2.3.16 - Tue Apr 18 02:45:46 2023 +0000
- [OpenConceptLab/ocl_issues#1550](https://github.com/OpenConceptLab/ocl_issues/issues/1550) | bulk importer to acknowledge id as optional for Concept | handling auto assign id
##### 2.3.15 - Fri Apr 14 02:35:57 2023 +0000
- [OpenConceptLab/ocl_issues#1547](https://github.com/OpenConceptLab/ocl_issues/issues/1547) | fixing for non-admin logged in user's concepts/mappings listing showing duplicates
- [OpenConceptLab/ocl_issues#1533](https://github.com/OpenConceptLab/ocl_issues/issues/1533) | putting back mappings/collections uri index
- [OpenConceptLab/ocl_issues#1533](https://github.com/OpenConceptLab/ocl_issues/issues/1533) | removed concept descriptions indexes
- [OpenConceptLab/ocl_issues#1533](https://github.com/OpenConceptLab/ocl_issues/issues/1533) | removed datatype indexes
- [OpenConceptLab/ocl_issues#1533](https://github.com/OpenConceptLab/ocl_issues/issues/1533) | removed concept_class indexes
- [OpenConceptLab/ocl_issues#1533](https://github.com/OpenConceptLab/ocl_issues/issues/1533) | removed locale indexes
- [OpenConceptLab/ocl_issues#1533](https://github.com/OpenConceptLab/ocl_issues/issues/1533) | removed public access indexes
- [OpenConceptLab/ocl_issues#1533](https://github.com/OpenConceptLab/ocl_issues/issues/1533) | removed mnemonic indexes
- [OpenConceptLab/ocl_issues#1533](https://github.com/OpenConceptLab/ocl_issues/issues/1533) | removed mappings map_type exact match index
- [OpenConceptLab/ocl_issues#1533](https://github.com/OpenConceptLab/ocl_issues/issues/1533) | removed index from is_latest_version field
- [OpenConceptLab/ocl_issues#1533](https://github.com/OpenConceptLab/ocl_issues/issues/1533) | removed mapping version field index
- [OpenConceptLab/ocl_issues#1533](https://github.com/OpenConceptLab/ocl_issues/issues/1533) | removed uri indexes from some places
- Bump django-elasticsearch-dsl from 7.2.2 to 7.3 (#410)
- Bump mozilla-django-oidc from 2.0.0 to 3.0.0 (#299)
- Bump redis from 4.5.1 to 4.5.4 (#428)
##### 2.3.14 - Wed Apr 12 10:21:42 2023 +0000
- [OpenConceptLab/ocl_issues#1544](https://github.com/OpenConceptLab/ocl_issues/issues/1544) | Source/Collection export for non HEAD should not check last child update except when writing the file
- [OpenConceptLab/ocl_issues#1528](https://github.com/OpenConceptLab/ocl_issues/issues/1528) | Source/Collection include resources behaviors
- Errbit | params integer type casting
- Errbit | params integer type casting
- Errbit | fixing collection export when no expansion exists
- Errbit | fixing collection export when no expansion exists
- Errbit | fixing bad limit param
- [OpenConceptLab/ocl_issues#1540](https://github.com/OpenConceptLab/ocl_issues/issues/1540) | operations panel access based on auth group
##### 2.3.13 - Tue Apr 11 10:45:43 2023 +0000
- [OpenConceptLab/ocl_issues#1541](https://github.com/OpenConceptLab/ocl_issues/issues/1541) Fix 'dict' object has no attribute 'udpate' in FHIR
##### 2.3.12 - Tue Apr 11 09:25:46 2023 +0000
- [OpenConceptLab/ocl_issues#1309](https://github.com/OpenConceptLab/ocl_issues/issues/1309) | fixing duplicate mapping issue in import of CIEL mappings on production
##### 2.3.11 - Thu Apr 6 10:56:00 2023 +0000
- [OpenConceptLab/ocl_issues#1533](https://github.com/OpenConceptLab/ocl_issues/issues/1533) Clean up duplicated indexes and merge indexes in mappings
##### 2.3.10 - Wed Apr 5 13:10:08 2023 +0000
- [OpenConceptLab/ocl_issues#1533](https://github.com/OpenConceptLab/ocl_issues/issues/1533) Adding missing migrations
- [OpenConceptLab/ocl_issues#1533](https://github.com/OpenConceptLab/ocl_issues/issues/1533) Clean up duplicated indexes and merge indexes in concepts
##### 2.3.9 - Wed Apr 5 09:32:58 2023 +0000
- [OpenConceptLab/ocl_issues#1533](https://github.com/OpenConceptLab/ocl_issues/issues/1533) Review changes to indexes, revert 0dfbe5c
- [OpenConceptLab/ocl_issues#1495](https://github.com/OpenConceptLab/ocl_issues/issues/1495) | collection summary
- [OpenConceptLab/ocl_issues#1527](https://github.com/OpenConceptLab/ocl_issues/issues/1527) | reusing collection serializers
- [OpenConceptLab/ocl_issues#1527](https://github.com/OpenConceptLab/ocl_issues/issues/1527) | reusing summary serializers
##### 2.3.8 - Mon Mar 27 05:14:47 2023 +0000
- [OpenConceptLab/ocl_issues#1524](https://github.com/OpenConceptLab/ocl_issues/issues/1524) | getting Source mapped sources in separate APIs
##### 2.3.7 - Thu Mar 23 09:03:07 2023 +0000
Mon May 16 16:11:46 2022 +0530
- [OpenConceptLab/ocl_issues#1524](https://github.com/OpenConceptLab/ocl_issues/issues/1524) | getting Source mapped sources in separate APIs
##### 2.3.6 - Thu Mar 23 06:05:59 2023 +0000
- skipping big import tests only on CI
- running facets test on CI
- [OpenConceptLab/ocl_issues#1524](https://github.com/OpenConceptLab/ocl_issues/issues/1524) | attempting test fix on CI
- [OpenConceptLab/ocl_issues#1524](https://github.com/OpenConceptLab/ocl_issues/issues/1524) | attempting test fix on CI
- [OpenConceptLab/ocl_issues#1524](https://github.com/OpenConceptLab/ocl_issues/issues/1524) | attempting test fix on CI
- [OpenConceptLab/ocl_issues#1524](https://github.com/OpenConceptLab/ocl_issues/issues/1524) | attempting test fix on CI
- [OpenConceptLab/ocl_issues#1524](https://github.com/OpenConceptLab/ocl_issues/issues/1524) | inspecting CI failure | added print for facets exception
- [OpenConceptLab/ocl_issues#1524](https://github.com/OpenConceptLab/ocl_issues/issues/1524) | using ES facets for field distribution
- [OpenConceptLab/ocl_issues#1524](https://github.com/OpenConceptLab/ocl_issues/issues/1524) | added index for retired counts
##### 2.3.5 - Wed Mar 22 04:04:51 2023 +0000
- [OpenConceptLab/ocl_issues#1458](https://github.com/OpenConceptLab/ocl_issues/issues/1458) | removed feature toggle
- Fixing tests
- [OpenConceptLab/ocl_issues#1521](https://github.com/OpenConceptLab/ocl_issues/issues/1521) | listing public criteria fix
##### 2.3.4 - Fri Mar 17 04:18:25 2023 +0000
- [OpenConceptLab/ocl_issues#1513](https://github.com/OpenConceptLab/ocl_issues/issues/1513) | concepts search in collection fix
##### 2.3.3 - Fri Mar 17 02:50:49 2023 +0000
- [OpenConceptLab/ocl_issues#1513](https://github.com/OpenConceptLab/ocl_issues/issues/1513) | concepts search in collection fix
- Bug | collection concepts after search were not removable
##### 2.3.2 - Wed Mar 15 04:24:47 2023 +0000
- [OpenConceptLab/ocl_issues#1458](https://github.com/OpenConceptLab/ocl_issues/issues/1458) | sort_weight can be null
- Reference filter schema to remove check from operation value
- [OpenConceptLab/ocl_issues#1415](https://github.com/OpenConceptLab/ocl_issues/issues/1415) Implement automated import scripts for FHIR HL7 content
##### 2.3.0 - Mon Mar 13 10:36:40 2023 +0000
##### 2.2.79 - Mon Mar 13 10:36:40 2023 +0000
- Bump whitenoise from 6.2.0 to 6.4.0 (#407)
- handling already queued exception
- Tasks | indexing tasks queue once with same args
##### 2.2.78 - Mon Mar 13 08:49:44 2023 +0000
- [OpenConceptLab/ocl_issues#1507](https://github.com/OpenConceptLab/ocl_issues/issues/1507) | fixing typo
- Fixing formatting
- [OpenConceptLab/ocl_issues#1511](https://github.com/OpenConceptLab/ocl_issues/issues/1511) ValueSet returns expansions for HEAD and not the latest version
- [OpenConceptLab/ocl_issues#1497](https://github.com/OpenConceptLab/ocl_issues/issues/1497) Fixing validate-code returning false positives
##### 2.2.77 - Fri Mar 10 03:53:23 2023 +0000
- [OpenConceptLab/ocl_issues#1510](https://github.com/OpenConceptLab/ocl_issues/issues/1510) | added feature toggles
##### 2.2.76 - Fri Mar 10 02:33:11 2023 +0000
- [OpenConceptLab/ocl_issues#1507](https://github.com/OpenConceptLab/ocl_issues/issues/1507) | import get to check for pending tasks
- Importers | checking workers are alive for alive tasks
- Bump django-ordered-model from 3.6 to 3.7.1 (#404)
- Bump django-cors-headers from 3.13.0 to 3.14.0 (#401)
- Fix formatting
- [OpenConceptLab/ocl_issues#1235](https://github.com/OpenConceptLab/ocl_issues/issues/1235) ConcetpMap operations fix  parameters logic
- Include production like docker-compose with web
- [OpenConceptLab/ocl_issues#1503](https://github.com/OpenConceptLab/ocl_issues/issues/1503) Collectstatic in api when building instead of at runtime
- importers tasks tests
- Test for port import update resource count task
- [OpenConceptLab/ocl_issues#1495](https://github.com/OpenConceptLab/ocl_issues/issues/1495) | collections summary tests
- [OpenConceptLab/ocl_issues#1495](https://github.com/OpenConceptLab/ocl_issues/issues/1495) | collections summary tests
- refactoring permission
- auth backend | added test case
- Bump redis from 4.3.4 to 4.5.1 (#391)
- Minor fixes and refactoring
- [OpenConceptLab/ocl_issues#1495](https://github.com/OpenConceptLab/ocl_issues/issues/1495) | collections summary
##### 2.2.75 - Fri Mar 3 03:43:46 2023 +0000
- [OpenConceptLab/ocl_issues#1467](https://github.com/OpenConceptLab/ocl_issues/issues/1467) | added index on concept_class and datatype
##### 2.2.74 - Fri Mar 3 02:55:31 2023 +0000
- Concept/Mapping | correcting index
- Removed debug apis
##### 2.2.73 - Thu Mar 2 03:16:53 2023 +0000
- Removed rendundant code
- Removed expansion/references data backfill APIs/tasks
- Removed expansion/references data backfill APIs/tasks
- Monthly usage report | fixing date formats
- Imports to queue summary calculations and not do inline
##### 2.2.72 - Tue Feb 28 02:44:01 2023 +0000
- [OpenConceptLab/ocl_issues#1499](https://github.com/OpenConceptLab/ocl_issues/issues/1499) | fixing test
- [OpenConceptLab/ocl_issues#1499](https://github.com/OpenConceptLab/ocl_issues/issues/1499) | fixing test
- [OpenConceptLab/ocl_issues#1499](https://github.com/OpenConceptLab/ocl_issues/issues/1499) | source | hierarchy_meaning indexing empty/null as None
- [OpenConceptLab/ocl_issues#1499](https://github.com/OpenConceptLab/ocl_issues/issues/1499) | source | hierarchy_meaning converting empty to None | data migration
- [OpenConceptLab/ocl_issues#1499](https://github.com/OpenConceptLab/ocl_issues/issues/1499) | source/collection | custom_validation_schema is mandatory field | data migration to set None for empty/null
- [OpenConceptLab/ocl_issues#1498](https://github.com/OpenConceptLab/ocl_issues/issues/1498) | batch delete to use transaction
- Bump pydash from 5.1.1 to 6.0.2 (#389)
- Bump mock from 4.0.3 to 5.0.1 (#383)
- Errbit | fixing retire of concept/mapping with no latest version | probable bad data
- [OpenConceptLab/ocl_issues#1458](https://github.com/OpenConceptLab/ocl_issues/issues/1458) | Mapping test to update sort_weight
- Bump drf-yasg from 1.21.4 to 1.21.5 (#377)
- Bump django from 4.1.6 to 4.1.7 (#380)
##### 2.2.71 - Wed Feb 15 10:22:02 2023 +0000
- Tests for OID views
- Revert - batch index | revert exception handling
- Facets | added fields to facets search
- correcting view hierarchy
- Indexing | ignoring exception
- Indexing | ignoring exception
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | not returning self
##### 2.2.70 - Mon Feb 13 12:16:39 2023 +0000
- Bump django-dirtyfields from 1.9.0 to 1.9.1 (#374)
- [OpenConceptLab/ocl_issues#1467](https://github.com/OpenConceptLab/ocl_issues/issues/1467) | Source version summary | using active concepts/mappings queryset
- Reports | Fixing months calculation
- Bump psycopg2 from 2.9.3 to 2.9.5 (#297)
##### 2.2.69 - Fri Feb 10 10:04:02 2023 +0000
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | Clone operation | fixing for mapping with non-existant concept
- [OpenConceptLab/ocl_issues#1467](https://github.com/OpenConceptLab/ocl_issues/issues/1467) | fixing flaky test
- [OpenConceptLab/ocl_issues#1467](https://github.com/OpenConceptLab/ocl_issues/issues/1467) | API response for different field distributions
- [OpenConceptLab/ocl_issues#1467](https://github.com/OpenConceptLab/ocl_issues/issues/1467) | updated api response structure
- [OpenConceptLab/ocl_issues#1467](https://github.com/OpenConceptLab/ocl_issues/issues/1467) | correcting source version concepts queryset
- [OpenConceptLab/ocl_issues#1467](https://github.com/OpenConceptLab/ocl_issues/issues/1467) | fixing serializer
- [OpenConceptLab/ocl_issues#1467](https://github.com/OpenConceptLab/ocl_issues/issues/1467) | fixing serializer
- [OpenConceptLab/ocl_issues#1467](https://github.com/OpenConceptLab/ocl_issues/issues/1467) | fixing pylint
- [OpenConceptLab/ocl_issues#1467](https://github.com/OpenConceptLab/ocl_issues/issues/1467) | Source version summary verbose API
- Fixing token check
##### 2.2.68 - Tue Feb 7 05:22:41 2023 +0000
- [OpenConceptLab/ocl_issues#1412](https://github.com/OpenConceptLab/ocl_issues/issues/1412) Enabling version endpoint for FHIR
- [OpenConceptLab/ocl_issues#1412](https://github.com/OpenConceptLab/ocl_issues/issues/1412) Migrate new FHIR endpoint to fhir subdomain
- fixing pylint
- fixing pylint
- API to trigger monthly usage report
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | Bundle | serializer can return list format response | can exclude self if no results in flat cascade
- Bump django from 4.1.3 to 4.1.6 (#367)
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | Clone operation | creating equivalent mapping from equivalent map type provided
- OpenConcetpLab/ocl_issues#1411 Fix formatting
- OpenConcetpLab/ocl_issues#1411 Fix POSTing to CodeSystem and ValueSet validate-code
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | Clone operation | fixing for non-existent target concept
##### 2.2.67 - Fri Feb 3 07:42:59 2023 +0000
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | Clone operation | added to/from-concept-code in mapping while cloning
- Fixing recursion in flat cascade query
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | Clone operation | handling schema validation exceptions
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | Clone operation | cloning concepts first and then mappings
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | Clone operation | not cloning external_id
- [OpenConceptLab/ocl_issues#1422](https://github.com/OpenConceptLab/ocl_issues/issues/1422) Refactor how OCL FHIR Core  interacts with OCL expansions
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | Clone operation to use equivalency map type to check existing concept and concept mnemonic is based on parent and not copied
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | fixing mapping clone for non-existing target concept
##### 2.2.66 - Wed Feb 1 06:45:03 2023 +0000
- Creating index for concept/mapping count
- fixing pylint
- [OpenConceptLab/ocl_issues#1449](https://github.com/OpenConceptLab/ocl_issues/issues/1449) | correcting current month range for scheduled report
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | bundle clone fixes
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | fixing clone
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | bundle clone parameters
- Source active concepts/mappings count for HEAD correction
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | fixing pylint
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | Source concepts clone API (similar to references)
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | concept clone API (similar to cascade)
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | bundle clone resource
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | clone behavior on source
- [OpenConceptLab/ocl_issues#1453](https://github.com/OpenConceptLab/ocl_issues/issues/1453) | clone with cascade behaviour for concept
- [OpenConceptLab/ocl_issues#1417](https://github.com/OpenConceptLab/ocl_issues/issues/1417) Fix formatting
- [OpenConceptLab/ocl_issues#1417](https://github.com/OpenConceptLab/ocl_issues/issues/1417) Fix FHIR global space loading time
##### 2.2.65 - Thu Jan 26 04:52:07 2023 +0000
- Concept/Mapping | indexes optimization
- [OpenConceptLab/ocl_issues#1416](https://github.com/OpenConceptLab/ocl_issues/issues/1416) Properly logging exception
- [OpenConceptLab/ocl_issues#1416](https://github.com/OpenConceptLab/ocl_issues/issues/1416) Do not fail if cannot represent resource as FHIR
##### 2.2.64 - Tue Jan 24 09:59:26 2023 +0000
- Fixing concept map views serializer for swagger
##### 2.2.63 - Tue Jan 24 09:37:53 2023 +0000
- [OpenConceptLab/ocl_issues#1463](https://github.com/OpenConceptLab/ocl_issues/issues/1463) | fixing cascade mapping serializer for target concept name
##### 2.2.62 - Wed Jan 18 11:06:49 2023 +0000
- Errbit client | checking for cause exists or not
##### 2.2.61 - Wed Jan 18 10:50:20 2023 +0000
- Errbit client | adding exception as cause in message and backtrace
- fixing pylint
- Refactoring concept/mappings listing
- Tests for OCL SSO auth backend
##### 2.2.60 - Wed Jan 18 07:28:02 2023 +0000
- [OpenConceptLab/ocl_issues#1452](https://github.com/OpenConceptLab/ocl_issues/issues/1452) | Bundle | changing repo_url to repo_version_url
- [OpenConceptLab/ocl_issues#1399](https://github.com/OpenConceptLab/ocl_issues/issues/1399) | fixing locale create
- [OpenConceptLab/ocl_issues#1399](https://github.com/OpenConceptLab/ocl_issues/issues/1399) | removed redundant admin APIs for locales cleanup
- [OpenConceptLab/ocl_issues#1399](https://github.com/OpenConceptLab/ocl_issues/issues/1399) | ConceptName | concept_id is mandatory | fixing tests
- [OpenConceptLab/ocl_issues#1399](https://github.com/OpenConceptLab/ocl_issues/issues/1399) | migrations | removed dormant locales and old M2M relations
##### 2.2.59 - Wed Jan 18 03:38:17 2023 +0000
- [OpenConceptLab/ocl_issues#1235](https://github.com/OpenConceptLab/ocl_issues/issues/1235) ConceptMap Operations: fixing test
- [OpenConceptLab/ocl_issues#1235](https://github.com/OpenConceptLab/ocl_issues/issues/1235) ConceptMap Operations: use assertRaises
- updated docker-compose version
- bumped coverage to 93
- [OpenConceptLab/ocl_issues#1235](https://github.com/OpenConceptLab/ocl_issues/issues/1235) ConceptMap Operations: follow up
- [OpenConceptLab/ocl_issues#1235](https://github.com/OpenConceptLab/ocl_issues/issues/1235) ConceptMap Operations: translate
- [OpenConceptLab/ocl_issues#1430](https://github.com/OpenConceptLab/ocl_issues/issues/1430) | separating pre_startup script | includes migrate and other tasks
- Concept Search | fixing wild card search
- coverage to 92
- coverage to 92
- AuthService | missing tests
- Importers | fixing mocks
- Importers | missing assertions
- Concept Search | search with multiple words and anything between them in synonyms
- [OpenConceptLab/ocl_issues#1399](https://github.com/OpenConceptLab/ocl_issues/issues/1399) | removed unused API
- [OpenConceptLab/ocl_issues#1399](https://github.com/OpenConceptLab/ocl_issues/issues/1399) | fixing pylints
- [OpenConceptLab/ocl_issues#1399](https://github.com/OpenConceptLab/ocl_issues/issues/1399) | names/descriptions migrations to split names and descriptions
- [OpenConceptLab/ocl_issues#1457](https://github.com/OpenConceptLab/ocl_issues/issues/1457) | cascade param equivalencyMapType
- [OpenConceptLab/ocl_issues#1451](https://github.com/OpenConceptLab/ocl_issues/issues/1451) | omitIfExistsIn to exclude all resource versions
- Concept Search | search with multiple words and anything between them
- [OpenConceptLab/ocl_issues#1451](https://github.com/OpenConceptLab/ocl_issues/issues/1451) | Concept cascade | Omit if exists in repo version
##### 2.2.58 - Mon Jan 2 10:19:30 2023 +0000
- [OpenConceptLab/ocl_issues#1449](https://github.com/OpenConceptLab/ocl_issues/issues/1449) | Errbit | fixing monthly usage report duration
- [OpenConceptLab/ocl_issues#1452](https://github.com/OpenConceptLab/ocl_issues/issues/1452) | added repo_url in cascade response
- [OpenConceptLab/ocl_issues#1450](https://github.com/OpenConceptLab/ocl_issues/issues/1450) | fixing tests
- updated changelog
- [OpenConceptLab/ocl_issues#1450](https://github.com/OpenConceptLab/ocl_issues/issues/1450) | removed uuid from cascade response
##### 2.2.57 - Wed Dec 28 03:22:31 2022 +0000
- [OpenConceptLab/ocl_issues#1447](https://github.com/OpenConceptLab/ocl_issues/issues/1447) | fixing mapping importer for duplicate mappings
- [OpenConceptLab/ocl_issues#1449](https://github.com/OpenConceptLab/ocl_issues/issues/1449) | fixing pylint
- [OpenConceptLab/ocl_issues#1449](https://github.com/OpenConceptLab/ocl_issues/issues/1449) | changing subject
- [OpenConceptLab/ocl_issues#1449](https://github.com/OpenConceptLab/ocl_issues/issues/1449) | fixing tests
- [OpenConceptLab/ocl_issues#1449](https://github.com/OpenConceptLab/ocl_issues/issues/1449) | fixing pylint
- [OpenConceptLab/ocl_issues#1449](https://github.com/OpenConceptLab/ocl_issues/issues/1449) | monthly usage report to show current month results and trend over last 3 months
- [OpenConceptLab/ocl_issues#1446](https://github.com/OpenConceptLab/ocl_issues/issues/1446) | mapping sort_weight field to be populated in versions | tests for importer
- [OpenConceptLab/ocl_issues#1448](https://github.com/OpenConceptLab/ocl_issues/issues/1448) | fixing pylint
- [OpenConceptLab/ocl_issues#1448](https://github.com/OpenConceptLab/ocl_issues/issues/1448) | all owned orgs/sources/collections properties on user | handling user hard delete exception
##### 2.2.56 - Thu Dec 22 05:33:32 2022 +0000
- Exception handling for import deadlock
- [OpenConceptLab/ocl_issues#1446](https://github.com/OpenConceptLab/ocl_issues/issues/1446) | mapping sort_weight field and ordering
##### 2.2.55 - Wed Dec 21 07:49:47 2022 +0000
- request full url in header
- Extracting env vars for email and web url
##### 2.2.54 - Wed Dec 14 04:06:51 2022 +0000
- [OpenConceptLab/ocl_issues#1430](https://github.com/OpenConceptLab/ocl_issues/issues/1430) | skipping other tasks if migrations are skipped
- Correcting job schedule
- [OpenConceptLab/ocl_issues#1430](https://github.com/OpenConceptLab/ocl_issues/issues/1430) Adjusting logging
- [OpenConceptLab/ocl_issues#1430](https://github.com/OpenConceptLab/ocl_issues/issues/1430) Support DB migrations in background
- [OpenConceptLab/ocl_issues#1135](https://github.com/OpenConceptLab/ocl_issues/issues/1135) | Logged in user to be able to view other user details
##### 2.2.53 - Fri Dec 9 05:51:23 2022 +0000
- [OpenConceptLab/ocl_issues#1408](https://github.com/OpenConceptLab/ocl_issues/issues/1408) | fixing queryset
- [OpenConceptLab/ocl_issues#1408](https://github.com/OpenConceptLab/ocl_issues/issues/1408) | API to get mapped sources for a source
- Contributions doc (#282)
##### 2.2.52 - Tue Nov 29 04:04:11 2022 +0000
- [OpenConceptLab/ocl_issues#1437](https://github.com/OpenConceptLab/ocl_issues/issues/1437) | fixing OpenMRS cascade system version resolution for cascade
- Code systems operations | fixing tests
- CodeSystem operations URL to support with and without /
- [OpenConceptLab/ocl_issues#1363](https://github.com/OpenConceptLab/ocl_issues/issues/1363) | added missing fields
##### 2.2.51 - Wed Nov 16 10:44:17 2022 +0000
- Bump coverage from 6.2 to 6.5.0 (#290)
- Bump django-dirtyfields from 1.8.2 to 1.9.0 (#287)
- Bump djangorestframework from 3.13.1 to 3.14.0 (#289)
##### 2.2.50 - Sun Nov 13 06:07:30 2022 +0000
- [OpenConceptLab/ocl_issues#1424](https://github.com/OpenConceptLab/ocl_issues/issues/1424) | refactoring
- [OpenConceptLab/ocl_issues#1424](https://github.com/OpenConceptLab/ocl_issues/issues/1424) | Exclude resource from expansion test and fix
- [OpenConceptLab/ocl_issues#1424](https://github.com/OpenConceptLab/ocl_issues/issues/1424) | OpenMRS Cascade fixes
- [OpenConceptLab/ocl_issues#1424](https://github.com/OpenConceptLab/ocl_issues/issues/1424) | For cascade + transform | return the concluded expression in response
- [OpenConceptLab/ocl_issues#1424](https://github.com/OpenConceptLab/ocl_issues/issues/1424) | added transform in reference serializer
- [OpenConceptLab/ocl_issues#1038](https://github.com/OpenConceptLab/ocl_issues/issues/1038) | fixing test
- [OpenConceptLab/ocl_issues#1038](https://github.com/OpenConceptLab/ocl_issues/issues/1038) | fixing test
- [OpenConceptLab/ocl_issues#1038](https://github.com/OpenConceptLab/ocl_issues/issues/1038) | monthly usage report scheduled to run on 1st of every month to report prev month's usage
- [OpenConceptLab/ocl_issues#1424](https://github.com/OpenConceptLab/ocl_issues/issues/1424) | fixing tranform reference when nothing returns from queryset
- [OpenConceptLab/ocl_issues#1038](https://github.com/OpenConceptLab/ocl_issues/issues/1038) | monthly usage report task
- fixing flaky test
- [OpenConceptLab/ocl_issues#1424](https://github.com/OpenConceptLab/ocl_issues/issues/1424) | OpenMRS cascade | accepting cascade expanded structure
- [OpenConceptLab/ocl_issues#1309](https://github.com/OpenConceptLab/ocl_issues/issues/1309) | fixing mappings importers query for existence check for special characters
- Bulk importer update counts async
- Source HEAD last child updated at query optimisation
##### 2.2.49 - Sun Nov 6 01:56:03 2022 +0000
- Can force queue an export
- [OpenConceptLab/ocl_issues#1233](https://github.com/OpenConceptLab/ocl_issues/issues/1233) ConceptMap CRUD
##### 2.2.48 - Fri Nov 4 02:44:05 2022 +0000
Tue Sep 14 18:39:45 2021 +0530
- [OpenConceptLab/ocl_issues#1406](https://github.com/OpenConceptLab/ocl_issues/issues/1406) | expansion parameter | system-version can be multiple comma separated
- Bump requests from 2.27.1 to 2.28.1 (#283)
- Bump pydash from 5.1.0 to 5.1.1 (#284)
- Bump drf-yasg from 1.20.0 to 1.21.4 (#285)
- Bump django from 4.1.1 to 4.1.3 (#286)
##### 2.2.47 - Thu Nov 3 04:19:47 2022 +0000
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | cascade | fixing hierarchy for repo version cascade
- [OpenConceptLab/ocl_issues#1387](https://github.com/OpenConceptLab/ocl_issues/issues/1387) | Fixed healthchecks for celery and celery_beat
- [OpenConceptLab/ocl_issues#1387](https://github.com/OpenConceptLab/ocl_issues/issues/1387) | added beat task for healthcheck | added management command to check for beat health
- [OpenConceptLab/ocl_issues#1387](https://github.com/OpenConceptLab/ocl_issues/issues/1387) | updated celery command line
- [OpenConceptLab/ocl_issues#1387](https://github.com/OpenConceptLab/ocl_issues/issues/1387) | updated celery command line
- [OpenConceptLab/ocl_issues#1387](https://github.com/OpenConceptLab/ocl_issues/issues/1387) | Using custom fork of flower | fixes https://github.com/mher/flower/issues/1231
- [OpenConceptLab/ocl_issues#1387](https://github.com/OpenConceptLab/ocl_issues/issues/1387) | added django-celery-beat | upgraded celery/redis/kombu/flower
- [OpenConceptLab/ocl_issues#1387](https://github.com/OpenConceptLab/ocl_issues/issues/1387) | fixing typo
- [OpenConceptLab/ocl_issues#1387](https://github.com/OpenConceptLab/ocl_issues/issues/1387) | local beat setup
##### 2.2.46 - Wed Nov 2 05:54:56 2022 +0000
- [OpenConceptLab/ocl_issues#1356](https://github.com/OpenConceptLab/ocl_issues/issues/1356) | migration to populate extras in repo versions from HEAD
- [OpenConceptLab/ocl_issues#1356](https://github.com/OpenConceptLab/ocl_issues/issues/1356) | source/collection version extras
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | cascade | fixing pylint
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | cascade | to also return requested url
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | cascade | not returning uuid
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | cascade | removed includeMappings
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | Source version detail serializer | Added hierarchy root url
- Refactoring | Extracting constant for "*" symbol
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | concept cascade with return map types false
- [OpenConceptLab/ocl_issues#1364](https://github.com/OpenConceptLab/ocl_issues/issues/1364) | fixing accented character
- [OpenConceptLab/ocl_issues#1364](https://github.com/OpenConceptLab/ocl_issues/issues/1364) | caching default locales API
- [OpenConceptLab/ocl_issues#1364](https://github.com/OpenConceptLab/ocl_issues/issues/1364) | added source description
- [OpenConceptLab/ocl_issues#1364](https://github.com/OpenConceptLab/ocl_issues/issues/1364) | OCL default locales API | GET /locales/
- [OpenConceptLab/ocl_issues#1364](https://github.com/OpenConceptLab/ocl_issues/issues/1364) | ISO/iso639-1/locales fixtures
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | removed redundant name field
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | cascade return map types to use filter map types criteria
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | cascade returnMapTypes behaviour
- [OpenConceptLab/ocl_issues#1338](https://github.com/OpenConceptLab/ocl_issues/issues/1338) | SSO with KeyCloak
- OpenMRSMappingValidator | better query
- OpenMRSMappingValidator | do not validate if mapping is retired
- bulk import | better query for indexes update
##### 2.2.45 - Mon Oct 17 10:49:16 2022 +0000
- Mappings import | Correcting mappings exists check
##### 2.2.44 - Mon Oct 17 09:43:33 2022 +0000
- Mappings Validation | ignoring retired
##### 2.2.43 - Sun Oct 16 02:01:26 2022 +0000
- OpenMRS mapping validation schema to ignore old retired versions of mappings
- [OpenConceptLab/ocl_issues#1364](https://github.com/OpenConceptLab/ocl_issues/issues/1364) | added iso-637-1 locale in lookup data
- [OpenConceptLab/ocl_issues#1382](https://github.com/OpenConceptLab/ocl_issues/issues/1382) | Source/Collection | supported locales to always have default locale first
- Revert "Disabling server side cursors | fixing connection pooling"
- Disabling server side cursors | fixing connection pooling
- [OpenConceptLab/ocl_issues#1245](https://github.com/OpenConceptLab/ocl_issues/issues/1245) | extras search query to replace '-' with '_'
- [OpenConceptLab/ocl_issues#1288](https://github.com/OpenConceptLab/ocl_issues/issues/1288) Upgrade Postgres to 14.4
- Revert "OpenConceptLab/ocl_issues#1288 Upgrade Postgres to latest stable (13.7)"
- [OpenConceptLab/ocl_issues#1288](https://github.com/OpenConceptLab/ocl_issues/issues/1288) Upgrade Postgres to latest stable (13.7)
- [OpenConceptLab/ocl_issues#1354](https://github.com/OpenConceptLab/ocl_issues/issues/1354) | fixing pylint error
- [OpenConceptLab/ocl_issues#1354](https://github.com/OpenConceptLab/ocl_issues/issues/1354) | retired mapping csv test
- [OpenConceptLab/ocl_issues#1215](https://github.com/OpenConceptLab/ocl_issues/issues/1215) | Imports | handling invalid/bad CSV uploads
##### 2.2.42 - Wed Sep 28 04:42:00 2022 +0000
Tue Sep 14 18:39:45 2021 +0530
- [OpenConceptLab/ocl_issues#1354](https://github.com/OpenConceptLab/ocl_issues/issues/1354) | sample csv and test for retired mapping (CSV -> JSON)
- Source/Collection delete | already queued handling
- Reference | fixing translation for encoded codes
- removed unused import
- Fixing test
- [OpenConceptLab/ocl_issues#1354](https://github.com/OpenConceptLab/ocl_issues/issues/1354) | concept/mapping importers | delete action
- [OpenConceptLab/ocl_issues#1348](https://github.com/OpenConceptLab/ocl_issues/issues/1348) | Source/Collection | converting json attributes to json
- Bump whitenoise from 5.3.0 to 6.2.0 (#273)
- Bump markdown from 3.3.7 to 3.4.1 (#272)
- Bump django-cors-headers from 3.12.0 to 3.13.0 (#271)
- Bump django-dirtyfields from 1.8.1 to 1.8.2 (#270)
- [OpenConceptLab/ocl_issues#1353](https://github.com/OpenConceptLab/ocl_issues/issues/1353) | removed collection.repository_type
- [OpenConceptLab/ocl_issues#1352](https://github.com/OpenConceptLab/ocl_issues/issues/1352) | concept facets for encoded characters
- [OpenConceptLab/ocl_issues#1145](https://github.com/OpenConceptLab/ocl_issues/issues/1145) | API to get full result of task by taskID
##### 2.2.41 - Thu Sep 8 03:32:59 2022 +0000
- [OpenConceptLab/ocl_issues#1351](https://github.com/OpenConceptLab/ocl_issues/issues/1351) | external_id exact searchable
- Bump djangorestframework from 3.12.4 to 3.13.1 (#269)
- Upgraded pylint
- Bump django from 4.0.6 to 4.1.1 (#268)
- Fixing test
- [OpenConceptLab/ocl_issues#1343](https://github.com/OpenConceptLab/ocl_issues/issues/1343) | Reference translation additions
##### 2.2.40 - Fri Sep 2 07:50:20 2022 +0000
- Fixing ocladmin orgs membership data reset on api deploy
- [OpenConceptLab/ocl_issues#1343](https://github.com/OpenConceptLab/ocl_issues/issues/1343) | added translation in collection references
- [OpenConceptLab/ocl_issues#1348](https://github.com/OpenConceptLab/ocl_issues/issues/1348) | Org Listing API | added type
- [OpenConceptLab/ocl_issues#1347](https://github.com/OpenConceptLab/ocl_issues/issues/1347) | fixing pylints
- [OpenConceptLab/ocl_issues#1347](https://github.com/OpenConceptLab/ocl_issues/issues/1347) | Source/Collection version | members and admin can recompute summary
##### 2.2.39 - Fri Aug 5 08:58:41 2022 +0000
- [OpenConceptLab/ocl_issues#1309](https://github.com/OpenConceptLab/ocl_issues/issues/1309) | MappingImporter | fixing queryset for exists check
##### 2.2.38 - Thu Aug 4 05:53:21 2022 +0000
- Errbit | Collection reference filters to query fix | fixing test
- Errbit | Collection reference filters to query fix
- Collection add expressions can be requested as async task
- docker-compose | added volume for postgres db
##### 2.2.37 - Fri Jul 29 02:57:48 2022 +0000
- concept serializer | fixing test
- concept flat cascade | fixing hierarchical concepts | added retired flag
##### 2.2.36 - Wed Jul 27 05:57:02 2022 +0000
- Extracting env vars for email setting
##### 2.2.35 - Tue Jul 26 05:04:41 2022 +0000
- [OpenConceptLab/ocl_issues#1339](https://github.com/OpenConceptLab/ocl_issues/issues/1339) | concept cascade to include/exclude retired results
##### 2.2.34 - Mon Jul 25 09:22:42 2022 +0000
- Extract export service (S3) | can plugin upload/download service via settings
##### 2.2.33 - Fri Jul 22 02:26:02 2022 +0000
- Errbit | Bulk create of mapping/concept via POST is not allowed
- Fixing version export with version creation
##### 2.2.32 - Thu Jul 21 03:24:07 2022 +0000
- Importer | Added Failed in summary
- Importer | delete action needs to be in sync with others in the same group
##### 2.2.31 - Tue Jul 19 02:41:13 2022 +0000
- Collection last child update query fix
##### 2.2.30 - Mon Jul 18 03:17:34 2022 +0000
- [OpenConceptLab/ocl_issues#1335](https://github.com/OpenConceptLab/ocl_issues/issues/1335) | User Management | verification and admin toggle
- [OpenConceptLab/ocl_issues#1336](https://github.com/OpenConceptLab/ocl_issues/issues/1336) | Speed up tests by getting rid of delete_all and relying on rollback
- [OpenConceptLab/ocl_issues#1335](https://github.com/OpenConceptLab/ocl_issues/issues/1335) | Admin can force mark verified any user
- Logs | added request method in response headers
- Logs | added request url in response headers
##### 2.2.28 - Wed Jul 13 05:32:06 2022 +0000
- Bump boto3 from 1.23.0 to 1.24.28 (#261)
- Bump django-ordered-model from 3.4.3 to 3.6 (#260)
- Collection version references | verbose response
- Bump django-cid from 2.2 to 2.3 (#259)
- Bump psycopg2 from 2.9.2 to 2.9.3 (#257)
##### 2.2.26 - Fri Jul 8 08:59:11 2022 +0000
- [OpenConceptLab/ocl_issues#1332](https://github.com/OpenConceptLab/ocl_issues/issues/1332) | fixing task to load less
- [OpenConceptLab/ocl_issues#1332](https://github.com/OpenConceptLab/ocl_issues/issues/1332) | fixing method signature
- Fixing task view test
- [OpenConceptLab/ocl_issues#1332](https://github.com/OpenConceptLab/ocl_issues/issues/1332) | API/task for backfilling repo versions to expansions
##### 2.2.25 - Fri Jul 8 05:36:11 2022 +0000
- CollectionReference | API to resolve reference
##### 2.2.24 - Thu Jul 7 07:02:23 2022 +0000
- Errbit | fixing exception class import
- Errbit | ES search exception | data too large
- Fixing Mapping creation without from/to source
##### 2.2.23 - Wed Jul 6 06:01:54 2022 +0000
- removed unused import
- Bump django from 4.0.5 to 4.0.6 (#258)
- API/Task to link all references resources
- Task to migrate references
- Task to migrate references | ignoring coverage
- Task to migrate from old to new reference structure | added logs
- APIs to Link reference with resources and to migrate from old to new structure via job
- Reference | migrating old reference to new structure via management command
- Revert "Reference | migrating old reference to new structure"
- Reference | migrating old reference to new structure
- Utils | Test for more scenarios
- Merge pull request #254 from OpenConceptLab/dependabot/pip/django-request-logging-0.7.5
- [OpenConceptLab/ocl_issues#1145](https://github.com/OpenConceptLab/ocl_issues/issues/1145) | API to get any task info by ID from Flower
- Bump django-request-logging from 0.7.3 to 0.7.5
##### 2.2.20 - Wed Jun 29 05:10:39 2022 +0000
- Expansions | corrected user signatures on create
- Skipping csv test | getting hung sometimes
- Errbit | not using cache for openmrs concept validator lookups
- [OpenConceptLab/ocl_issues#1330](https://github.com/OpenConceptLab/ocl_issues/issues/1330) | LocalizedText.name is a Hash Index
- [OpenConceptLab/ocl_issues#1329](https://github.com/OpenConceptLab/ocl_issues/issues/1329) | Source/Collection serializers | canonical_url as char field
- [OpenConceptLab/ocl_issues#1329](https://github.com/OpenConceptLab/ocl_issues/issues/1329) | canonical_url check works for any uri scheme
- [OpenConceptLab/ocl_issues#1329](https://github.com/OpenConceptLab/ocl_issues/issues/1329) | canonical_url can take any URI
##### 2.2.18 - Fri Jun 24 07:36:16 2022 +0000
- Concept/Mapping | repo version query to not check for public access | added indexes for repo versions
- Revert "OpenConceptLab/ocl_issues#1320 | reference cascade to use unique resources"
##### 2.2.16 - Fri Jun 24 05:36:31 2022 +0000
- [OpenConceptLab/ocl_issues#1320](https://github.com/OpenConceptLab/ocl_issues/issues/1320) | expansion to add unique resources
##### 2.2.15 - Fri Jun 24 04:50:56 2022 +0000
- Errbit | fixing mapping collection membership API
- Errbit | fixing concept collection membership API
- [OpenConceptLab/ocl_issues#1320](https://github.com/OpenConceptLab/ocl_issues/issues/1320) | reference cascade to use unique resources
- [OpenConceptLab/ocl_issues#1307](https://github.com/OpenConceptLab/ocl_issues/issues/1307) | expansions to keep resolved repo versions
- Added concept indexes for repo version query
##### 2.2.14 - Mon Jun 20 08:43:13 2022 +0000
- [OpenConceptLab/ocl_issues#1325](https://github.com/OpenConceptLab/ocl_issues/issues/1325) | Mapping target concept name reverse
- [OpenConceptLab/ocl_issues#1325](https://github.com/OpenConceptLab/ocl_issues/issues/1325) | Mapping target concept name
##### 2.2.13 - Fri Jun 17 04:59:06 2022 +0000
- importers.models | ignoring logs from coverage
- non-negative validator test
##### 2.2.12 - Wed Jun 15 03:27:20 2022 +0000
- Mocking Redis service
- Bump django from 4.0.4 to 4.0.5
- increased coverage to 93
- Repo version export delete test
- Repo version processing view integration tests
- missing S3 test
- revived s3 test
- upgraded moto to latest
- unit tests for postgresql service for sequence CRUD
- [OpenConceptLab/ocl_issues#1116](https://github.com/OpenConceptLab/ocl_issues/issues/1116) | Added Response time header
- Errbit | bulk import task to throw error when 'type' is missing on any line
- Fixing source mnemonic sequence not present for older sources
- [OpenConceptLab/ocl_issues#1232](https://github.com/OpenConceptLab/ocl_issues/issues/1232) Fixing test
- [OpenConceptLab/ocl_issues#1232](https://github.com/OpenConceptLab/ocl_issues/issues/1232) Adding tests and fixes
##### 2.2.11 - Fri Jun 10 07:59:57 2022 +0000
- [OpenConceptLab/ocl_issues#1311](https://github.com/OpenConceptLab/ocl_issues/issues/1311) | resources search attributes | correcting boost and attr meta
- [OpenConceptLab/ocl_issues#1311](https://github.com/OpenConceptLab/ocl_issues/issues/1311) | org search attributes | correcting boost and attr meta
##### 2.2.10 - Fri Jun 10 05:40:43 2022 +0000
- [OpenConceptLab/ocl_issues#1311](https://github.com/OpenConceptLab/ocl_issues/issues/1311) | wildcard search boost=0
##### 2.2.9 - Fri Jun 10 03:22:12 2022 +0000
- CollectionReference | not using expression to compute concepts/mappings
- [OpenConceptLab/ocl_issues#1321](https://github.com/OpenConceptLab/ocl_issues/issues/1321) | ES extras | replacing hyphens with underscores
- [OpenConceptLab/ocl_issues#1215](https://github.com/OpenConceptLab/ocl_issues/issues/1215) | Errbit | Imports | Handling invalid JSON error
- Errbit | throwing error in repo version import when HEAD could not be found
- [OpenConceptLab/ocl_issues#1319](https://github.com/OpenConceptLab/ocl_issues/issues/1319) | pylintrc | disable false positive cyclic-import
- [OpenConceptLab/ocl_issues#1319](https://github.com/OpenConceptLab/ocl_issues/issues/1319) | sources | updated as per pylint2.14
- [OpenConceptLab/ocl_issues#1319](https://github.com/OpenConceptLab/ocl_issues/issues/1319) | valuesets | updated as per pylint2.14
- [OpenConceptLab/ocl_issues#1319](https://github.com/OpenConceptLab/ocl_issues/issues/1319) | common | updated as per pylint2.14
- [OpenConceptLab/ocl_issues#1319](https://github.com/OpenConceptLab/ocl_issues/issues/1319) | importers | updated as per pylint2.14
- [OpenConceptLab/ocl_issues#1319](https://github.com/OpenConceptLab/ocl_issues/issues/1319) | concepts | updated as per pylint2.14
- [OpenConceptLab/ocl_issues#1319](https://github.com/OpenConceptLab/ocl_issues/issues/1319) | collections | updated as per pylint2.14
- [OpenConceptLab/ocl_issues#1319](https://github.com/OpenConceptLab/ocl_issues/issues/1319) | pylint | no-self-use is a separate plugin now
- [OpenConceptLab/ocl_issues#1319](https://github.com/OpenConceptLab/ocl_issues/issues/1319) | pylint | fixing utils to ignore dunder calls
- [OpenConceptLab/ocl_issues#1295](https://github.com/OpenConceptLab/ocl_issues/issues/1295) | exposing route to cascade concept within expansion context
- [OpenConceptLab/ocl_issues#1278](https://github.com/OpenConceptLab/ocl_issues/issues/1278) | Exclude Expression | when resource version is not specified, it will exclude all versions
- [OpenConceptLab/ocl_issues#1311](https://github.com/OpenConceptLab/ocl_issues/issues/1311) | Making search attributes for each resource seperate and added boost
- Bump pylint from 2.12.2 to 2.14.0
- [OpenConceptLab/ocl_issues#1210](https://github.com/OpenConceptLab/ocl_issues/issues/1210) | correcting seq reset
- [OpenConceptLab/ocl_issues#1210](https://github.com/OpenConceptLab/ocl_issues/issues/1210) | source autoid | source update can update autoid sequence
- [OpenConceptLab/ocl_issues#1210](https://github.com/OpenConceptLab/ocl_issues/issues/1210) | source autoid | can set start from
- [OpenConceptLab/ocl_issues#1210](https://github.com/OpenConceptLab/ocl_issues/issues/1210) | source autoid | not reseting on concept/mapping delete
- [OpenConceptLab/ocl_issues#1210](https://github.com/OpenConceptLab/ocl_issues/issues/1210) | Concept id optional for autoid set sources
- [OpenConceptLab/ocl_issues#1295](https://github.com/OpenConceptLab/ocl_issues/issues/1295) | concept cascade forward/backward flat/hierarchy for collection version
- [OpenConceptLab/ocl_issues#1265](https://github.com/OpenConceptLab/ocl_issues/issues/1265) | expansion parameters | include system version switches version of code system
- [OpenConceptLab/ocl_issues#1210](https://github.com/OpenConceptLab/ocl_issues/issues/1210) | Source autoid for concepts/mappings mnemonic/external_id
- Errbit | fixing collection summary compute
- [OpenConceptLab/ocl_issues#1278](https://github.com/OpenConceptLab/ocl_issues/issues/1278) | reference add | fixing special characters in code
- [OpenConceptLab/ocl_issues#1278](https://github.com/OpenConceptLab/ocl_issues/issues/1278) | added references in concept version detail serializer
- Errbit | fixing Collection/Sources json field indexing
- [OpenConceptLab/ocl_issues#1305](https://github.com/OpenConceptLab/ocl_issues/issues/1305) | parallel import | chunking with resource versions in same chunk
- [OpenConceptLab/ocl_issues#1289](https://github.com/OpenConceptLab/ocl_issues/issues/1289) | reference | cascade params to use all parameters
- [OpenConceptLab/ocl_issues#1289](https://github.com/OpenConceptLab/ocl_issues/issues/1289) | reference | fixing cascade params
- [OpenConceptLab/ocl_issues#1289](https://github.com/OpenConceptLab/ocl_issues/issues/1289) | reference parser | fixing include/exclude parsing
- [OpenConceptLab/ocl_issues#1301](https://github.com/OpenConceptLab/ocl_issues/issues/1301) | collection concept/mapping GET | using correct serializers
- [OpenConceptLab/ocl_issues#1299](https://github.com/OpenConceptLab/ocl_issues/issues/1299) | encoding concept id for cascade
- [OpenConceptLab/ocl_issues#1289](https://github.com/OpenConceptLab/ocl_issues/issues/1289) | updated parsers to handle few more scenarios
- APIs to get reference's concepts/mappings
- [OpenConceptLab/ocl_issues#1278](https://github.com/OpenConceptLab/ocl_issues/issues/1278) | reference delete to exclude resource or resource-version based on resource result
- [OpenConceptLab/ocl_issues#1278](https://github.com/OpenConceptLab/ocl_issues/issues/1278) | reference add with filter to use versioned_object_id
- [OpenConceptLab/ocl_issues#1234](https://github.com/OpenConceptLab/ocl_issues/issues/1234) Fixes to CodeSystem
- Not logging Load balancer requests
- Fixing paginator assignment
- CodeSystem/ValueSet | considering _count param for page size
- CodeSystem/ValueSet | added pagination links in serializer
- FHIRBundleSerializer | added links for pagination
- Bump django-cors-headers from 3.10.1 to 3.12.0
- [OpenConceptLab/ocl_issues#1278](https://github.com/OpenConceptLab/ocl_issues/issues/1278) | fixing delete
- [OpenConceptLab/ocl_issues#1278](https://github.com/OpenConceptLab/ocl_issues/issues/1278) | reference delete to queue indexing for removed resourecs
- API to index expansion concepts/mappings
- [OpenConceptLab/ocl_issues#1234](https://github.com/OpenConceptLab/ocl_issues/issues/1234) Fixing identifier parsing
- [OpenConceptLab/ocl_issues#1234](https://github.com/OpenConceptLab/ocl_issues/issues/1234) Adding more debug info
- [OpenConceptLab/ocl_issues#1234](https://github.com/OpenConceptLab/ocl_issues/issues/1234) Fixing datetime issue
- using HEAD constant
- CodeSystem/ValueSet | using constants
- Cleaning CodeSystem views
- [OpenConceptLab/ocl_issues#123](https://github.com/OpenConceptLab/ocl_issues/issues/123) | ValueSet expand to be sync
- [OpenConceptLab/ocl_issues#123](https://github.com/OpenConceptLab/ocl_issues/issues/123) | Fixing Valueset expand test
- Bump markdown from 3.3.4 to 3.3.7
- Bump boto3 from 1.21.27 to 1.23.0
- [OpenConceptLab/ocl_issues#1232](https://github.com/OpenConceptLab/ocl_issues/issues/1232) ValueSet Operations (validate-code and expand)
- [OpenConceptLab/ocl_issues#1224](https://github.com/OpenConceptLab/ocl_issues/issues/1224) | restricting concept cascade to source version
- [OpenConceptLab/ocl_issues#1292](https://github.com/OpenConceptLab/ocl_issues/issues/1292) | reference bulk delete
- [OpenConceptLab/ocl_issues#1292](https://github.com/OpenConceptLab/ocl_issues/issues/1292) | reference delete to reevaluate other references
- [OpenConceptLab/ocl_issues#1292](https://github.com/OpenConceptLab/ocl_issues/issues/1292) | new ref in expansion evaluates all exclusion refs also
- [OpenConceptLab/ocl_issues#1292](https://github.com/OpenConceptLab/ocl_issues/issues/1292) | added include/exclude in old style list parser
- [OpenConceptLab/ocl_issues#1292](https://github.com/OpenConceptLab/ocl_issues/issues/1292) | added include/exclude in reference clone
- [OpenConceptLab/ocl_issues#1278](https://github.com/OpenConceptLab/ocl_issues/issues/1278) | reference summary as part of verbose reference response
- [OpenConceptLab/ocl_issues#1292](https://github.com/OpenConceptLab/ocl_issues/issues/1292) | exclude reference
- [OpenConceptLab/ocl_issues#1232](https://github.com/OpenConceptLab/ocl_issues/issues/1232) | Valueset | cleaning/formatting serializer/tests
- [OpenConceptLab/ocl_issues#1232](https://github.com/OpenConceptLab/ocl_issues/issues/1232) | Valueset | using collection reference parser
- [OpenConceptLab/ocl_issues#1232](https://github.com/OpenConceptLab/ocl_issues/issues/1232) ValueSet Operations, filter
- bumping coverage to 92:
- Added test for collection version expansions APIs
- Added test for collection version expansion delete
- Added test for collection version expansion concept view
- Added test for collection version expansion concept's mappings list view
- Added test for collection version expansion mapping retrieve
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | fixing ES pagination
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | fixing ES max_clause_limit error when applying filters
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | logging async add references errors
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | fixing filter expression results
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | refactored expansion references add
- [OpenConceptLab/ocl_issues#1231](https://github.com/OpenConceptLab/ocl_issues/issues/1231) ValueSet CRUD
- [OpenConceptLab/ocl_issues#1166](https://github.com/OpenConceptLab/ocl_issues/issues/1166) Addressing issues after initial testing
- added missing test for collection version summary get
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | refactoring parsers | adding more tests
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | Valuset expression | resolving queryset correctly
- Errbit | fixing search result slicing when page is not defined
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | fixing collection references PUT response
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | Added more tests around new style syntax parser
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | reference filter can take exact_match as well
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | removed unused import
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | Setting expression for expanded structure
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | Collection Reference Delete API
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | Collection Reference response to have more info
- CodeSystem formatting tests
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | Expanded Reference Structure and refactorings
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | generic collection reference parser for old and new style
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | generic old style to new expanded reference structure parser
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | another assertion for a test
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | parser for old style all source resources reference expression to new expanded structure
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | parser for old style reference expression to new expanded syntax
##### 2.2.8 - Mon May 2 04:00:50 2022 +0000
- [OpenConceptLab/ocl_issues#1285](https://github.com/OpenConceptLab/ocl_issues/issues/1285) | Repo export behind permission
##### 2.2.7 - Sat Apr 30 03:52:38 2022 +0000
- [OpenConceptLab/ocl_issues#1285](https://github.com/OpenConceptLab/ocl_issues/issues/1285) | Repo export behind permission
- [OpenConceptLab/ocl_issues#1283](https://github.com/OpenConceptLab/ocl_issues/issues/1283) | Concept synonyms indexing | using lowecase normalizer
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | reference filter field schema validation
- [OpenConceptLab/ocl_issues#1277](https://github.com/OpenConceptLab/ocl_issues/issues/1277) | not using redis cache backend on CI
- [OpenConceptLab/ocl_issues#1277](https://github.com/OpenConceptLab/ocl_issues/issues/1277) | Using cached lookup API for all lookups
- [OpenConceptLab/ocl_issues#1277](https://github.com/OpenConceptLab/ocl_issues/issues/1277) | added django cache backend as redis
- [OpenConceptLab/ocl_issues#1283](https://github.com/OpenConceptLab/ocl_issues/issues/1283) | concept search criteria | added synonyms search criteria with wildcards
- Postgres-Dev | upgraded to 14.2-alpine
- Bump django from 4.0.3 to 4.0.4
- [OpenConceptLab/ocl_issues#1265](https://github.com/OpenConceptLab/ocl_issues/issues/1265) | Expansion Parameters | fixed order of parameters evaluation
- [OpenConceptLab/ocl_issues#1280](https://github.com/OpenConceptLab/ocl_issues/issues/1280) | added verbose references in collection's concept/mapping responses via query param
- [OpenConceptLab/ocl_issues#1265](https://github.com/OpenConceptLab/ocl_issues/issues/1265) | Source/Collection | making revision_date datetime field and setting on version release
- [OpenConceptLab/ocl_issues#1265](https://github.com/OpenConceptLab/ocl_issues/issues/1265) | expansion parameter | include/exclude system version considers valuesets as well
- [OpenConceptLab/ocl_issues#1265](https://github.com/OpenConceptLab/ocl_issues/issues/1265) | expansion parameter | applying include system before exclude
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | refactored resolve reference operation
- [OpenConceptLab/ocl_issues#1275](https://github.com/OpenConceptLab/ocl_issues/issues/1275) | CollectionReference | added attributes for structured reference
- [OpenConceptLab/ocl_issues#1262](https://github.com/OpenConceptLab/ocl_issues/issues/1262) | API for source head resources dedup deleted
##### 2.2.4 - Sat Apr 16 05:39:57 2022 +0000
- [OpenConceptLab/ocl_issues#1280](https://github.com/OpenConceptLab/ocl_issues/issues/1280) | added references in collection/expansion mapping version detail
- [OpenConceptLab/ocl_issues#1265](https://github.com/OpenConceptLab/ocl_issues/issues/1265) | expansion parameter to include/exclude system version
- [OpenConceptLab/ocl_issues#1262](https://github.com/OpenConceptLab/ocl_issues/issues/1262) | Making sure limit offset is applied in list queries
- Importers Errbit | fixing mapping failed index attempt issue
- [OpenConceptLab/ocl_issues#1265](https://github.com/OpenConceptLab/ocl_issues/issues/1265) | using API_BASE_URL in place of internal url
- [OpenConceptLab/ocl_issues#1265](https://github.com/OpenConceptLab/ocl_issues/issues/1265) | fixing empty queryset search
- [OpenConceptLab/ocl_issues#1265](https://github.com/OpenConceptLab/ocl_issues/issues/1265) | added expansion in facets filters
- [OpenConceptLab/ocl_issues#1265](https://github.com/OpenConceptLab/ocl_issues/issues/1265) | Expansions | added 'filter' parameter
- Concept Importer | handling integer ids
- Upgraded to 2.2.0
- [OpenConceptLab/ocl_issues#1274](https://github.com/OpenConceptLab/ocl_issues/issues/1274) | concept/mappings list | correcting global queryset
- [OpenConceptLab/ocl_issues#1274](https://github.com/OpenConceptLab/ocl_issues/issues/1274) | concept/mappings list | fixing parent resource set
- [OpenConceptLab/ocl_issues#1274](https://github.com/OpenConceptLab/ocl_issues/issues/1274) | concept/mappings list | removing joins for HEAD parent calls | added indexes
- Errbit | collection expansion concepts/mappings CSV list fix
- [OpenConceptLab/ocl_issues#1274](https://github.com/OpenConceptLab/ocl_issues/issues/1274) | concept list | not prefetching names
- [OpenConceptLab/ocl_issues#1274](https://github.com/OpenConceptLab/ocl_issues/issues/1274) | reusing count query
- [OpenConceptLab/ocl_issues#1274](https://github.com/OpenConceptLab/ocl_issues/issues/1274) | Concept/Mapping list view | added is_active clause
- [OpenConceptLab/ocl_issues#1274](https://github.com/OpenConceptLab/ocl_issues/issues/1274) | Concept/Mapping list view | removing a join from query
- [OpenConceptLab/ocl_issues#1272](https://github.com/OpenConceptLab/ocl_issues/issues/1272) | test for concept retired TRUE/FALSE CSV converter -> import
##### 2.1.3 - Thu Apr 7 09:24:30 2022 +0000
- [OpenConceptLab/ocl_issues#1262](https://github.com/OpenConceptLab/ocl_issues/issues/1262) | (attempting) fixing whitenoise static files issue
- [OpenConceptLab/ocl_issues#1206](https://github.com/OpenConceptLab/ocl_issues/issues/1206) | removed old collection concepts/mappings relations
- [OpenConceptLab/ocl_issues#1206](https://github.com/OpenConceptLab/ocl_issues/issues/1206) | removed code to migrate old style to new style collection
- [OpenConceptLab/ocl_issues#1155](https://github.com/OpenConceptLab/ocl_issues/issues/1155) | Merge pull request #209 from OpenConceptLab/django4
- [OpenConceptLab/ocl_issues#1262](https://github.com/OpenConceptLab/ocl_issues/issues/1262) | add response serializer
- [OpenConceptLab/ocl_issues#1262](https://github.com/OpenConceptLab/ocl_issues/issues/1262) | API route for head resources dedup
- Org data migration | adding creator and updator in members list
- [OpenConceptLab/ocl_issues#1247](https://github.com/OpenConceptLab/ocl_issues/issues/1247) | collection/source apis | brief response
- Refactoing | extracted common code
- Expansion test for getting mappings from a concept
- user search view test
- user org search view test
- user org collections/sources list test
- Concept Search | multi words wild card test
- Upgraded to Django4
##### 2.1.0 - Mon Apr 4 08:57:29 2022 +0530
- Upgraded to 2.1.0
- Expansion | do not re-evaluate references for auto expansion
- coverage at 91
- Collection get mapping expressions from concept in expansion test
- Collection Reference fetch concepts/mappings test
- Expansion delete expression tests
- Expansion clean test
- Expansion parameters test
- Mapping validation test
- Revert "reviving facets tests for CI"
- coverage at 90
- reviving facets tests for CI
- Source mappings/concepts indexes view test
- Source Hierarchy view test
- Source version summary API test
- Collection version expansion mappings/concepts API list view test
- test for collection version (default expansion) concept mappings api
- test for expansion concept mappings api
- test for source update validation schema task
- test for source concepts/mappings batch index tasks
- added retry on failure for source mappings index task
- tests for source/collection resources count tasks
- tasks | test for delete s3 objects
- mapping hard delete test
- concept summary test
- Mapping collection membership test
- Concept collection membership test
- Concept hard delete request test
- Concept parents/children test
- Mapping reactivate test
- Concept locale edit and reactivate tests
- [OpenConceptLab/ocl_issues#1267](https://github.com/OpenConceptLab/ocl_issues/issues/1267) | repo HEAD export should delete old cached exports from S3
- imports | update_comment in new concept/mapping
- imports | update_comment in new concept
- Reference Importer | one batch index index task each for concepts and mappings for all references
- batch_index_resources fixes
- indexing tasks | ignoring results and added retry
- [OpenConceptLab/ocl_issues#1166](https://github.com/OpenConceptLab/ocl_issues/issues/1166) | fixing get_serializer methods for swagger
- Repo Version export | logged upload status code
- [OpenConceptLab/ocl_issues#1262](https://github.com/OpenConceptLab/ocl_issues/issues/1262) | API to dedup source head resource versions associations
- [OpenConceptLab/ocl_issues#1262](https://github.com/OpenConceptLab/ocl_issues/issues/1262) | management task to have repo head only with resource latest versions
- [OpenConceptLab/ocl_issues#1262](https://github.com/OpenConceptLab/ocl_issues/issues/1262) | repo HEAD will not keep all resource versions but latest only
- Merge pull request #207 from OpenConceptLab/dependabot/pip/boto3-1.21.27
- Bump boto3 from 1.20.24 to 1.21.27
- Perform search when  is present
- [OpenConceptLab/ocl_issues#1166](https://github.com/OpenConceptLab/ocl_issues/issues/1166) | Add validate-code and lookup for CodeSystem
- [OpenConceptLab/ocl_issues#1244](https://github.com/OpenConceptLab/ocl_issues/issues/1244) Using 1 instead of 2 parallel workers for tests
- [OpenConceptLab/ocl_issues#1244](https://github.com/OpenConceptLab/ocl_issues/issues/1244) Using 2 instead of 4 parallel workers for tests
- [OpenConceptLab/ocl_issues#1244](https://github.com/OpenConceptLab/ocl_issues/issues/1244) | Wait for ES when running tests
- Cascade | added target source/owner name in mappings response
- Fixing search criteria for hyphens
- coverage at 88
- Bundle serializer | remove concepts/mappings count
- Fixing pylint
- Bundle serializer to close to Fhir Bundle response
- Coverage | minor refacotrings
- Coverage | Source index children test
- Coverage | utils | added missing test
- Coverage | import get task status | tests for flower service failed
- Coverage | tests for task delete
- Coverage | refactored client config serializers remove redundancy
- coverage to 87
- [OpenConceptLab/ocl_issues#1244](https://github.com/OpenConceptLab/ocl_issues/issues/1244) | skipping facets test on CI
- [OpenConceptLab/ocl_issues#1244](https://github.com/OpenConceptLab/ocl_issues/issues/1244) | attemping index fix for CI
- [OpenConceptLab/ocl_issues#1244](https://github.com/OpenConceptLab/ocl_issues/issues/1244) | fixing test for CI
- [OpenConceptLab/ocl_issues#1244](https://github.com/OpenConceptLab/ocl_issues/issues/1244) | fixing pylint
- [OpenConceptLab/ocl_issues#1244](https://github.com/OpenConceptLab/ocl_issues/issues/1244) | fail build if coverage is below 88
- [OpenConceptLab/ocl_issues#1244](https://github.com/OpenConceptLab/ocl_issues/issues/1244) | more search behaviours in integration tests
- [OpenConceptLab/ocl_issues#1244](https://github.com/OpenConceptLab/ocl_issues/issues/1244) | concept search integration test
- Revert "Revert "OpenConceptLab/ocl_issues#1244 | concept search integration test""
- Correcting celery signal processor
- Revert "OpenConceptLab/ocl_issues#1244 | concept search integration test"
- Concept/Mapping | added indexes for versioned_object_id
- [OpenConceptLab/ocl_issues#1244](https://github.com/OpenConceptLab/ocl_issues/issues/1244) | concept search integration test
- [OpenConceptLab/ocl_issues#1244](https://github.com/OpenConceptLab/ocl_issues/issues/1244) | Disable redis
- [OpenConceptLab/ocl_issues#1244](https://github.com/OpenConceptLab/ocl_issues/issues/1244) | Enable ES tests on Bamboo
- Parallel Imports | allowing update_comment field in concept/mapping imports
- Errbit | increased ES timeout to 60 seconds
- [OpenConceptLab/ocl_issues#1230](https://github.com/OpenConceptLab/ocl_issues/issues/1230) | references add | transform resource versions is acknowledged
- Imports | chunking indexing to multiple tasks
- User reactivate should reset the status of user
- [OpenConceptLab/ocl_issues#1246](https://github.com/OpenConceptLab/ocl_issues/issues/1246) | upgraded ES image to 7.17.1 | now supports mac m1
- [OpenConceptLab/ocl_issues#1166](https://github.com/OpenConceptLab/ocl_issues/issues/1166) | Bullet-proof identifier and extra logging
- [OpenConceptLab/ocl_issues#1241](https://github.com/OpenConceptLab/ocl_issues/issues/1241) | concept/mapping retrieve should work for source version request
- [OpenConceptLab/ocl_issues#1221](https://github.com/OpenConceptLab/ocl_issues/issues/1221) | integration test expansion concept
- [OpenConceptLab/ocl_issues#1221](https://github.com/OpenConceptLab/ocl_issues/issues/1221) | integration test expansion mappings
- [OpenConceptLab/ocl_issues#1221](https://github.com/OpenConceptLab/ocl_issues/issues/1221) | integration test for resolve operation
- APIs to get concept/mapping details from collection expansion
- [OpenConceptLab/ocl_issues#1221](https://github.com/OpenConceptLab/ocl_issues/issues/1221) | resolveReference | added requested info, resolution_url in response | can consider string expressions as well
- [OpenConceptLab/ocl_issues#1221](https://github.com/OpenConceptLab/ocl_issues/issues/1221) | resolveReference | considering namespace only in case of canonical (FQDN)
- [OpenConceptLab/ocl_issues#1166](https://github.com/OpenConceptLab/ocl_issues/issues/1166) | Making fields non-required
- [OpenConceptLab/ocl_issues#1221](https://github.com/OpenConceptLab/ocl_issues/issues/1221) | versionless resolve reference to resolve to latest or HEAD
- [OpenConceptLab/ocl_issues#1166](https://github.com/OpenConceptLab/ocl_issues/issues/1166) | Adding support for create and update for CodeSystems
- [OpenConceptLab/ocl_issues#1166](https://github.com/OpenConceptLab/ocl_issues/issues/1166) | Addressing review
- [OpenConceptLab/ocl_issues#1203](https://github.com/OpenConceptLab/ocl_issues/issues/1203) | Source/Collection Home | styling breadcrumbs to have fixed button widths | styling selected controls
- Errbit | fixing search results slicing when page is 0
- Exapnsions revaluate references always
- [OpenConceptLab/ocl_issues#1225](https://github.com/OpenConceptLab/ocl_issues/issues/1225) | Reference Import | fixing indexing
- [OpenConceptLab/ocl_issues#1225](https://github.com/OpenConceptLab/ocl_issues/issues/1225) | Collection Expansion processing to happen in sync in bulk import
- [OpenConceptLab/ocl_issues#1225](https://github.com/OpenConceptLab/ocl_issues/issues/1225) | Parallel Importer | making sure repo versions are processed in right order
- [OpenConceptLab/ocl_issues#1221](https://github.com/OpenConceptLab/ocl_issues/issues/1221) | resolve reference to consider Collections | using version list serializer response when resolved
- updated Readme
- [OpenConceptLab/ocl_issues#1221](https://github.com/OpenConceptLab/ocl_issues/issues/1221) | accepting relative url for source version
- [OpenConceptLab/ocl_issues#1221](https://github.com/OpenConceptLab/ocl_issues/issues/1221) | fixing pylint errors
- [OpenConceptLab/ocl_issues#1221](https://github.com/OpenConceptLab/ocl_issues/issues/1221) | reference expression resolve API
- [OpenConceptLab/ocl_issues#1219](https://github.com/OpenConceptLab/ocl_issues/issues/1219) | removed dead code
- [OpenConceptLab/ocl_issues#1219](https://github.com/OpenConceptLab/ocl_issues/issues/1219) | Concept index | added synonyms
- [OpenConceptLab/ocl_issues#1220](https://github.com/OpenConceptLab/ocl_issues/issues/1220) | facets size 20 (from default 10)
- [OpenConceptLab/ocl_issues#1206](https://github.com/OpenConceptLab/ocl_issues/issues/1206) | removed dead code
- Unused import removed
- Expansions | API to get concept mappings from collection version context
- Expansions | fixing concept mappings from collection/expansion  context
- [OpenConceptLab/ocl_issues#1203](https://github.com/OpenConceptLab/ocl_issues/issues/1203) | added uuid in references serializers
- concept/mapping version membership | removing duplicates
- Expansions | simplifying concept/mapping collection index
- Expansions | correcting concept/mapping collection_version membership api
- Expansions | correcting concept/mapping collection_version list property
- [OpenConceptLab/ocl_issues#1166](https://github.com/OpenConceptLab/ocl_issues/issues/1166) | fixing tests
- [OpenConceptLab/ocl_issues#1166](https://github.com/OpenConceptLab/ocl_issues/issues/1166) | Fixing formatting
- [OpenConceptLab/ocl_issues#1166](https://github.com/OpenConceptLab/ocl_issues/issues/1166) | Fixing formatting
- [OpenConceptLab/ocl_issues#1166](https://github.com/OpenConceptLab/ocl_issues/issues/1166) | Adding FHIR CodeSystem resource (read only)
- Making sure expansion mnemonic is used when provided
- [OpenConceptLab/ocl_issues#826](https://github.com/OpenConceptLab/ocl_issues/issues/826) | fixing tests
- [OpenConceptLab/ocl_issues#1209](https://github.com/OpenConceptLab/ocl_issues/issues/1209) | ordering children by mnemonic
- [OpenConceptLab/ocl_issues#826](https://github.com/OpenConceptLab/ocl_issues/issues/826) | parallel importer | batch index concepts/mappings
- [OpenConceptLab/ocl_issues#1209](https://github.com/OpenConceptLab/ocl_issues/issues/1209) | source parent less concepts API support
- [OpenConceptLab/ocl_issues#1209](https://github.com/OpenConceptLab/ocl_issues/issues/1209) | API to get source's parent less concepts
- [OpenConceptLab/ocl_issues#1209](https://github.com/OpenConceptLab/ocl_issues/issues/1209) | concept has children property
- [OpenConceptLab/ocl_issues#1205](https://github.com/OpenConceptLab/ocl_issues/issues/1205) | collection version export to wait until auto expansion is processing
- Migrations | fixing deleting dormant collection references
- Migrations | deleting dormant collection references
- Migrations | deleting dormant collection references
- fixing migration | creating postgres btree gin extension
- fixing migration | creating postgres btree gin extension
- fixing migration | creating psql extension
- Collection Reference -> Concept/Mapping association
- concept/version details/listing serializer | added versioned_object_id for term browser
- concept/mapping lists | added indexes and refactored queryset
- [OpenConceptLab/ocl_issues#1169](https://github.com/OpenConceptLab/ocl_issues/issues/1169) | concept cascade recursion | keeping it DRY
- [OpenConceptLab/ocl_issues#1196](https://github.com/OpenConceptLab/ocl_issues/issues/1196) | monthly usage report visualization
- [OpenConceptLab/ocl_issues#1197](https://github.com/OpenConceptLab/ocl_issues/issues/1197) | openmrs schema locales type from term browser fix
- [OpenConceptLab/ocl_issues#1191](https://github.com/OpenConceptLab/ocl_issues/issues/1191) | collection add reference | dynamic reference fix
- [OpenConceptLab/ocl_issues#1191](https://github.com/OpenConceptLab/ocl_issues/issues/1191) | Collection References | delete to use existing queryset
- [OpenConceptLab/ocl_issues#1191](https://github.com/OpenConceptLab/ocl_issues/issues/1191) | Collection References | not hard deleting, just disassociating
- [OpenConceptLab/ocl_issues#1191](https://github.com/OpenConceptLab/ocl_issues/issues/1191) | Collection References | fixing reference delete
- Collection/Version/Expansion | concepts/mappings facets class
- [OpenConceptLab/ocl_issues#1169](https://github.com/OpenConceptLab/ocl_issues/issues/1169) | correcting entries conditions
- [OpenConceptLab/ocl_issues#1169](https://github.com/OpenConceptLab/ocl_issues/issues/1169) |  as hierarchy terminal indicator
- deleted dead code
- Coverage at 85
- client-configs | using utils more
- Collection/Expansion | missing test | dead code
- Coverage increased to 86
- [OpenConceptLab/ocl_issues#1128](https://github.com/OpenConceptLab/ocl_issues/issues/1128) | source/collection async delete is default
- Fixing flaky test
- [OpenConceptLab/ocl_issues#997](https://github.com/OpenConceptLab/ocl_issues/issues/997) | fixing reference expression resolve
- [OpenConceptLab/ocl_issues#1136](https://github.com/OpenConceptLab/ocl_issues/issues/1136) | startup | migration from old style collection to new style
- [OpenConceptLab/ocl_issues#1169](https://github.com/OpenConceptLab/ocl_issues/issues/1169) |  levels to refelect cascade levels and not recursion levels
- [OpenConceptLab/ocl_issues#1136](https://github.com/OpenConceptLab/ocl_issues/issues/1136) | management command for collection migration from oldstyle to newstyle
- coverage back to 85
- Mapping/Concept version tests
- Mapping/Concept version hard delete
- Fixing Concept Version Hard Delete | fixing concept/mapping listing and active concepts/mappings count queries
- Concept Version Hard delete to not delete locales
- [OpenConceptLab/ocl_issues#1180](https://github.com/OpenConceptLab/ocl_issues/issues/1180) | moving away from celery autoscale
- Expansions | indexing async
- Mapping Collection Membership API
- Merge branch 'master' into collection_expansions
- [OpenConceptLab/ocl_issues#712](https://github.com/OpenConceptLab/ocl_issues/issues/712) | fixing pylint warnings
- [OpenConceptLab/ocl_issues#712](https://github.com/OpenConceptLab/ocl_issues/issues/712) | utils | method to get values from nested dict
- Merge branch 'master' into collection_expansions
- updated requests package
- Errbit fix | concepts mappings | return 404 if concept not found
- celery healthcheck | increased timeout
- celery healthcheck | increased timeout
- [OpenConceptLab/ocl_issues#1179](https://github.com/OpenConceptLab/ocl_issues/issues/1179) | fixing test
- [OpenConceptLab/ocl_issues#1176](https://github.com/OpenConceptLab/ocl_issues/issues/1176) | fixing test
- [OpenConceptLab/ocl_issues#1176](https://github.com/OpenConceptLab/ocl_issues/issues/1176) | collection/version concept/mapping/version GET request
- [OpenConceptLab/ocl_issues#1163](https://github.com/OpenConceptLab/ocl_issues/issues/1163) | importers | handling when no 'type' is provided
- [OpenConceptLab/ocl_issues#1163](https://github.com/OpenConceptLab/ocl_issues/issues/1163) | collection expansion mnemonic to have autoexpanded only if its autoexpanded
- Org overview column migration
- fixing migrations | migration merge
- coverage to 84 | wip
- Collection | autoexpand nullable boolean
- [OpenConceptLab/ocl_issues#1144](https://github.com/OpenConceptLab/ocl_issues/issues/1144) | Collection details | added expansion_url
- [OpenConceptLab/ocl_issues#1144](https://github.com/OpenConceptLab/ocl_issues/issues/1144) | auto expansions mnemonic updated
- [OpenConceptLab/ocl_issues#979](https://github.com/OpenConceptLab/ocl_issues/issues/979) | collection summary has expansions count also | fixing test
- [OpenConceptLab/ocl_issues#979](https://github.com/OpenConceptLab/ocl_issues/issues/979) | cannot delete default expansion | expansions count in version summary
- [OpenConceptLab/ocl_issues#923](https://github.com/OpenConceptLab/ocl_issues/issues/923) | errbit client setup
- [OpenConceptLab/ocl_issues#979](https://github.com/OpenConceptLab/ocl_issues/issues/979) | Expansion detail serializer
- Removed internal_reference_id from expansions
- [OpenConceptLab/ocl_issues#970](https://github.com/OpenConceptLab/ocl_issues/issues/970) | removed duplicate import
- [OpenConceptLab/ocl_issues#970](https://github.com/OpenConceptLab/ocl_issues/issues/970) | removed unused imports
- [OpenConceptLab/ocl_issues#970](https://github.com/OpenConceptLab/ocl_issues/issues/970) | reference expression can be collection based
- [OpenConceptLab/ocl_issues#818](https://github.com/OpenConceptLab/ocl_issues/issues/818) | Supporting dynamic references
- removed internal_reference_id | was used for v1 to v2 data migration
- [OpenConceptLab/ocl_issues#818](https://github.com/OpenConceptLab/ocl_issues/issues/818) | collection version serializer to have expansions_url
- [OpenConceptLab/ocl_issues#818](https://github.com/OpenConceptLab/ocl_issues/issues/818) | collection child last updated at on expansion
- [OpenConceptLab/ocl_issues#818](https://github.com/OpenConceptLab/ocl_issues/issues/818) | head autoexpand false behaviours
- [OpenConceptLab/ocl_issues#818](https://github.com/OpenConceptLab/ocl_issues/issues/818) | collection version to use expansion_uri to get concepts/mappings
- [OpenConceptLab/ocl_issues#818](https://github.com/OpenConceptLab/ocl_issues/issues/818) | collection version expansion_uri is an explicit field
- [OpenConceptLab/ocl_issues#818](https://github.com/OpenConceptLab/ocl_issues/issues/818) | expansions doesnt have references copy
- [OpenConceptLab/ocl_issues#818](https://github.com/OpenConceptLab/ocl_issues/issues/818) | collection version with expansions and parameters
- [OpenConceptLab/ocl_issues#818](https://github.com/OpenConceptLab/ocl_issues/issues/818) | collection version default expansion with default parameters on autoexpanded version creation
- [OpenConceptLab/ocl_issues#818](https://github.com/OpenConceptLab/ocl_issues/issues/818) | collection version autoexpand false to seed only references
- [OpenConceptLab/ocl_issues#818](https://github.com/OpenConceptLab/ocl_issues/issues/818) | collections | autoexpand head/version attrs | not resolving expressions for autoexpand_head false
##### 2.0.111 - Wed Jan 12 05:40:32 2022 +0000
- [OpenConceptLab/ocl_issues#1183](https://github.com/OpenConceptLab/ocl_issues/issues/1183) | added  parameters in swagger
- [OpenConceptLab/ocl_issues#1183](https://github.com/OpenConceptLab/ocl_issues/issues/1183) | concept  reverse
- [OpenConceptLab/ocl_issues#1183](https://github.com/OpenConceptLab/ocl_issues/issues/1183) | reverse mapping serializer
- [OpenConceptLab/ocl_issues#1169](https://github.com/OpenConceptLab/ocl_issues/issues/1169) | hierarchy concepts before mapping concepts
- [OpenConceptLab/ocl_issues#1169](https://github.com/OpenConceptLab/ocl_issues/issues/1169) | ordering by map type
- [OpenConceptLab/ocl_issues#1179](https://github.com/OpenConceptLab/ocl_issues/issues/1179) | source/collection | active concepts/mappings counts to be None when not set rather than 0
- [OpenConceptLab/ocl_issues#1175](https://github.com/OpenConceptLab/ocl_issues/issues/1175) | Source/Collection DELETE | fixing test
- [OpenConceptLab/ocl_issues#1175](https://github.com/OpenConceptLab/ocl_issues/issues/1175) | Source/Collection DELETE | fixing test
- [OpenConceptLab/ocl_issues#1175](https://github.com/OpenConceptLab/ocl_issues/issues/1175) | Source/Collection DELETE | making s3 exports delete async task
- [OpenConceptLab/ocl_issues#1169](https://github.com/OpenConceptLab/ocl_issues/issues/1169) | mapping serializer | added to_concept_code/to_concept_url
- Fixing flaky test for openmrs concept schema
- Fixing test
- Added missing tests
- [OpenConceptLab/ocl_issues#1163](https://github.com/OpenConceptLab/ocl_issues/issues/1163) | importers | handling when no 'type' is provided
- coverage.sh | fail if tests fail
##### 2.0.107 - Wed Dec 29 07:07:52 2021 +0000
- [OpenConceptLab/ocl_issues#712](https://github.com/OpenConceptLab/ocl_issues/issues/712) | Admin can hard delete users (except self)
- [OpenConceptLab/ocl_issues#1169](https://github.com/OpenConceptLab/ocl_issues/issues/1169) | added display_name in  hierarchy response
- [OpenConceptLab/ocl_issues#1168](https://github.com/OpenConceptLab/ocl_issues/issues/1168) | bundle hierarchy and flat responses
- [OpenConceptLab/ocl_issues#1168](https://github.com/OpenConceptLab/ocl_issues/issues/1168) | concept  as hierarchy method
- [OpenConceptLab/ocl_issues#1169](https://github.com/OpenConceptLab/ocl_issues/issues/1169) | concept summary to have child/parent concepts count | summary an be added in concept obj response
- [OpenConceptLab/ocl_issues#1169](https://github.com/OpenConceptLab/ocl_issues/issues/1169) | concept properties for child parent counts
- [OpenConceptLab/ocl_issues#1167](https://github.com/OpenConceptLab/ocl_issues/issues/1167) | inactive->verify->activate user feature
- [OpenConceptLab/ocl_issues#1163](https://github.com/OpenConceptLab/ocl_issues/issues/1163) | errbit | fixing concept clone
- Errbit fix | API with page number empty 500 fix
- [OpenConceptLab/ocl_issues#1167](https://github.com/OpenConceptLab/ocl_issues/issues/1167) | inactive user login | inactive user search and list
- [OpenConceptLab/ocl_issues#45](https://github.com/OpenConceptLab/ocl_issues/issues/45) | OpenMRS collection concepts validation to consider same name types
- Bump pylint from 2.11.1 to 2.12.2
- Bump coverage from 6.1.1 to 6.2
- Bump psycopg2 from 2.9.1 to 2.9.2
- Bump moto from 2.2.13 to 2.2.19
- Bump django-cors-headers from 3.10.0 to 3.10.1
- Source/Collection | removed last (missed) calculation of concepts
- Source/Collection Versions | saving unwanted summary calculation
- Errbit | concept children/parent APIs fix
- Errbit fix | user org collections/sources API fix when there are no user orgs
- Making sure that search doesn't compute DB query also
- [OpenConceptLab/ocl_issues#1133](https://github.com/OpenConceptLab/ocl_issues/issues/1133) | org importer | org creator is the member
- [OpenConceptLab/ocl_issues#1161](https://github.com/OpenConceptLab/ocl_issues/issues/1161) | mapping importer | retired is allowed field
- Errbit fix | importers | concepts/mappings returns failed if parent doesnt exist
- [OpenConceptLab/ocl_issues#1161](https://github.com/OpenConceptLab/ocl_issues/issues/1161) | mapping importer | encoding to/from_concept_code correctly
- [OpenConceptLab/ocl_issues#1156](https://github.com/OpenConceptLab/ocl_issues/issues/1156) | org overview settings
- [OpenConceptLab/ocl_issues#1157](https://github.com/OpenConceptLab/ocl_issues/issues/1157) | making sure new collection/source version copies all attributes from HEAD
- Bump django from 3.2.8 to 4.0
- [OpenConceptLab/ocl_issues#1151](https://github.com/OpenConceptLab/ocl_issues/issues/1151) | mapping importer | fixing exist check criteria
- Bump boto3 from 1.19.12 to 1.20.24
- API for user summary
- [OpenConceptLab/ocl_issues#1154](https://github.com/OpenConceptLab/ocl_issues/issues/1154) | Admin user can make another user admin or remove it (except self)
- Admin API to toggle user's staff permission
- Exposing DB for development
- Concept Debug API | no need for response
- [OpenConceptLab/ocl_issues#1151](https://github.com/OpenConceptLab/ocl_issues/issues/1151) | fixing self mapping importer
##### 2.0.93 - Wed Dec 8 04:42:29 2021 +0000
- Concept debug api to connect parent as source version
- Concept parent concept urls errbit fix | refactoring
- Concept debug api to log if versioned object exists
- fixing pylint warning | typo
- fixing pylint warning
- Admin API to mark latest version as versioned object
- Bundle resource_type -> type
- POST pins to have correct created by id
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | added concepts/mappings count
- [OpenConceptLab/ocl_issues#1132](https://github.com/OpenConceptLab/ocl_issues/issues/1132) | concept/mapping | added collection url in ES for facets
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | reducing iterations
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | reducing max results to 500
- [OpenConceptLab/ocl_issues#1127](https://github.com/OpenConceptLab/ocl_issues/issues/1127) | sorting coverage by cover percentage (asc)
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | integration test for all cascade levels
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | concept cascade performance | not loading concept parent
- [OpenConceptLab/ocl_issues#1131](https://github.com/OpenConceptLab/ocl_issues/issues/1131) | concept cascade performance | simplifying concept mappings queryset
- concept mappings removed distinct and order by clauses
- [OpenConceptLab/ocl_issues#1122](https://github.com/OpenConceptLab/ocl_issues/issues/1122) | concept cascade | refactoring and cleaning up
- [OpenConceptLab/ocl_issues#1122](https://github.com/OpenConceptLab/ocl_issues/issues/1122) | concept cascade | recrusion for nth level | limiting results and swagger parameters
- [OpenConceptLab/ocl_issues#1126](https://github.com/OpenConceptLab/ocl_issues/issues/1126) | fixing source/collection summary save tasks
- [OpenConceptLab/ocl_issues#1127](https://github.com/OpenConceptLab/ocl_issues/issues/1127) | coverage | fail under 85
- [OpenConceptLab/ocl_issues#1122](https://github.com/OpenConceptLab/ocl_issues/issues/1122) | concept cascade | can cascade hierarchy (default true) and cascade mappings option (default true)
##### 2.0.88 - Tue Nov 23 06:08:40 2021 +0000
- [OpenConceptLab/ocl_issues#1122](https://github.com/OpenConceptLab/ocl_issues/issues/1122) | concept cascade | swagger query parameters
- [OpenConceptLab/ocl_issues#1122](https://github.com/OpenConceptLab/ocl_issues/issues/1122) | concept cascade | excludeMapTypes filter
- [OpenConceptLab/ocl_issues#1126](https://github.com/OpenConceptLab/ocl_issues/issues/1126) | children concept urls queryset fix
- [OpenConceptLab/ocl_issues#1126](https://github.com/OpenConceptLab/ocl_issues/issues/1126) | fixing authored report when payload is none
- [OpenConceptLab/ocl_issues#1126](https://github.com/OpenConceptLab/ocl_issues/issues/1126) | parenless children queryset fix
- [OpenConceptLab/ocl_issues#1127](https://github.com/OpenConceptLab/ocl_issues/issues/1127) | coverage | setting coverage directory
- [OpenConceptLab/ocl_issues#1088](https://github.com/OpenConceptLab/ocl_issues/issues/1088) | bundle | added timestamp and concept/mapping type in response
- Adding API_IMAGE variable to docker-compose.ci
- Fixing Dockerfile to use cache
- [OpenConceptLab/ocl_issues#1088](https://github.com/OpenConceptLab/ocl_issues/issues/1088) | bundle | removed bundle type
- [OpenConceptLab/ocl_issues#1088](https://github.com/OpenConceptLab/ocl_issues/issues/1088) | bundle | not returning id and timestamp
##### 2.0.85 - Fri Nov 19 10:17:43 2021 +0000
- [OpenConceptLab/ocl_issues#1088](https://github.com/OpenConceptLab/ocl_issues/issues/1088) | bundle | default response is brief
- exact match facets to encode special characters in search string
- [OpenConceptLab/ocl_issues#1088](https://github.com/OpenConceptLab/ocl_issues/issues/1088) | concept  operation with bundle and ocl response
- Source/Collection counts update | making sure save is called when counts change
##### 2.0.82 - Sun Nov 14 13:30:38 2021 +0000
- ES | search request timeout
- Errbit Client | xml escaping url string
- [OpenConceptLab/ocl_issues#661](https://github.com/OpenConceptLab/ocl_issues/issues/661) | Reverting OpenConceptLab/ocl_issues#103
- [OpenConceptLab/ocl_issues#1008](https://github.com/OpenConceptLab/ocl_issues/issues/1008) | Collection ref add/delete will update child count
- [OpenConceptLab/ocl_issues#1008](https://github.com/OpenConceptLab/ocl_issues/issues/1008) | Source/Collection/Version retrieve will only update concepts or mappings count when required
- [OpenConceptLab/ocl_issues#1008](https://github.com/OpenConceptLab/ocl_issues/issues/1008) | concept/mapping on retire/delete updating parent counts
- [OpenConceptLab/ocl_issues#1008](https://github.com/OpenConceptLab/ocl_issues/issues/1008) | concept/mapping parent active counts are bulk updated after content import
- Response headers to have requesting user
- Not logging in dev mode
- Errbit errors to have request URL
- [OpenConceptLab/ocl_issues#1008](https://github.com/OpenConceptLab/ocl_issues/issues/1008) | concept/mapping counts async tasks
- [OpenConceptLab/ocl_issues#1082](https://github.com/OpenConceptLab/ocl_issues/issues/1082) Add GIN, GIST, TRGM extensions and concepts uri index
- [OpenConceptLab/ocl_issues#1082](https://github.com/OpenConceptLab/ocl_issues/issues/1082) | comments explaining hierarchy async tasks
- ES | increased timeout for facets query to 20s (default 10s) | Errbit
##### 2.0.80 - Mon Nov 8 11:36:04 2021 +0000
- Reverting to flower 0.9.5
- Merge pull request #28 from OpenConceptLab/dependabot/pip/flower-1.0.0
- Merge pull request #102 from OpenConceptLab/dependabot/pip/factory-boy-3.2.1
- Merge pull request #103 from OpenConceptLab/dependabot/pip/coverage-6.1.1
- Bump factory-boy from 3.2.0 to 3.2.1
- Bump coverage from 6.0.2 to 6.1.1
- Merge pull request #99 from OpenConceptLab/dependabot/pip/boto3-1.19.12
- Bump boto3 from 1.19.0 to 1.19.12
- Merge pull request #101 from OpenConceptLab/dependabot/pip/moto-2.2.13
- [OpenConceptLab/ocl_issues#1008](https://github.com/OpenConceptLab/ocl_issues/issues/1008) | hierarchy asyn processing on concurrent queue
- Bump moto from 2.2.9 to 2.2.13
- [OpenConceptLab/ocl_issues#1008](https://github.com/OpenConceptLab/ocl_issues/issues/1008) | not eager loading concepts/mappings owners
- [OpenConceptLab/ocl_issues#1008](https://github.com/OpenConceptLab/ocl_issues/issues/1008) | fixing pylints | unused arguments
- [OpenConceptLab/ocl_issues#1008](https://github.com/OpenConceptLab/ocl_issues/issues/1008) | saving concepts/mappings count on Source/Collection
- Not logging verbose on CI
- [OpenConceptLab/ocl_issues#1082](https://github.com/OpenConceptLab/ocl_issues/issues/1082) | fixing hierarchy query not use LIKE
- Concept List View | fixing queryset
- [OpenConceptLab/ocl_issues#941](https://github.com/OpenConceptLab/ocl_issues/issues/941) | not logging in dev/test
- [OpenConceptLab/ocl_issues#941](https://github.com/OpenConceptLab/ocl_issues/issues/941) | Added request/response headers and correlation id in logs
- [OpenConceptLab/ocl_issues#941](https://github.com/OpenConceptLab/ocl_issues/issues/941) | removed custom Logger middleware
- Collection version references | raise 404 if version not found
- Concept/Mapping | eager loading relations
- Concept/Mapping | added index with public_access for count queries (without order by)
- [OpenConceptLab/ocl_issues#1059](https://github.com/OpenConceptLab/ocl_issues/issues/1059) | including user as creator pins only if other user is not defined
- [OpenConceptLab/ocl_issues#1059](https://github.com/OpenConceptLab/ocl_issues/issues/1059) | Pin to have created by | user's pins can include user's created by pins
- [OpenConceptLab/ocl_issues#993](https://github.com/OpenConceptLab/ocl_issues/issues/993) | bulk import | collection/source delete and version creation only allowed for members
- [OpenConceptLab/ocl_issues#1070](https://github.com/OpenConceptLab/ocl_issues/issues/1070) | OpenMRS concept validator | preferred name uniquness clause to only consider existing preferred names
- pylint fixes
- Source concepts/mappings indexes views | added dummy serializer
- [OpenConceptLab/ocl_issues#1057](https://github.com/OpenConceptLab/ocl_issues/issues/1057) | Collection References | cascade source to concepts option
- Bump flower from 0.9.5 to 1.0.0
##### 2.0.75 - Wed Oct 27 08:40:29 2021 +0000
- [OpenConceptLab/ocl_issues#993](https://github.com/OpenConceptLab/ocl_issues/issues/993) | Parallel Bulk Import | user permission checks on resources
- [OpenConceptLab/ocl_issues#37](https://github.com/OpenConceptLab/ocl_issues/issues/37) | facets names are camel cased
- [OpenConceptLab/ocl_issues#37](https://github.com/OpenConceptLab/ocl_issues/issues/37) | fixing pylint
- Merge pull request #50 from PatrickCmd/filter_concepts_by_name_and_desctription_types
- Merge pull request #82 from OpenConceptLab/dependabot/pip/pyyaml-6.0
- [OpenConceptLab/ocl_issues#37](https://github.com/OpenConceptLab/ocl_issues/issues/37) | filter concepts by name type and description type
- Bump pyyaml from 5.4.1 to 6.0
##### 2.0.75 - Wed Oct 27 08:40:29 2021 +0000
- [OpenConceptLab/ocl_issues#993](https://github.com/OpenConceptLab/ocl_issues/issues/993) | Parallel Bulk Import | user permission checks on resources
- [OpenConceptLab/ocl_issues#37](https://github.com/OpenConceptLab/ocl_issues/issues/37) | facets names are camel cased
- [OpenConceptLab/ocl_issues#37](https://github.com/OpenConceptLab/ocl_issues/issues/37) | fixing pylint
- Merge pull request #50 from PatrickCmd/filter_concepts_by_name_and_desctription_types
- Merge pull request #82 from OpenConceptLab/dependabot/pip/pyyaml-6.0
- [OpenConceptLab/ocl_issues#37](https://github.com/OpenConceptLab/ocl_issues/issues/37) | filter concepts by name type and description type
- Bump pyyaml from 5.4.1 to 6.0
##### 2.0.74 - Fri Oct 22 02:13:56 2021 +0000
- Merge pull request #81 from OpenConceptLab/dependabot/pip/django-elasticsearch-dsl-7.2.1
- Bump django-elasticsearch-dsl from 7.2.0 to 7.2.1
- Merge pull request #80 from OpenConceptLab/dependabot/pip/coverage-6.0.2
- Merge pull request #79 from OpenConceptLab/dependabot/pip/django-cors-headers-3.10.0
- Bump coverage from 6.0 to 6.0.2
- Merge pull request #78 from OpenConceptLab/dependabot/pip/boto3-1.19.0
- Bump django-cors-headers from 3.9.0 to 3.10.0
- Merge pull request #70 from OpenConceptLab/dependabot/pip/django-3.2.8
- Bump boto3 from 1.18.49 to 1.19.0
- [OpenConceptLab/ocl_issues#1036](https://github.com/OpenConceptLab/ocl_issues/issues/1036) | POST Admin API to resolve duplicate latest versions using ids rather than created_at
- [OpenConceptLab/ocl_issues#1036](https://github.com/OpenConceptLab/ocl_issues/issues/1036) | keeping code DRY
- [OpenConceptLab/ocl_issues#1036](https://github.com/OpenConceptLab/ocl_issues/issues/1036) | POST Admin API to resolve duplicate latest versions for specific concepts
- [OpenConceptLab/ocl_issues#1036](https://github.com/OpenConceptLab/ocl_issues/issues/1036) | resolve duplicate latest version order by mnemonic
- [OpenConceptLab/ocl_issues#1036](https://github.com/OpenConceptLab/ocl_issues/issues/1036) | resolve duplicate latest version order by parent id desc
- [OpenConceptLab/ocl_issues#1036](https://github.com/OpenConceptLab/ocl_issues/issues/1036) | resolve duplicate latest version | added logs
- [OpenConceptLab/ocl_issues#1036](https://github.com/OpenConceptLab/ocl_issues/issues/1036) | resolve duplicate latest version with better query
- [OpenConceptLab/ocl_issues#1036](https://github.com/OpenConceptLab/ocl_issues/issues/1036) | after latest version resolve indexing
- [OpenConceptLab/ocl_issues#1036](https://github.com/OpenConceptLab/ocl_issues/issues/1036) | PUT Admin API to resolve duplicate latest versions
- [OpenConceptLab/ocl_issues#1036](https://github.com/OpenConceptLab/ocl_issues/issues/1036) | Admin API to get duplicate latest concept versions
- [OpenConceptLab/ocl_issues#923](https://github.com/OpenConceptLab/ocl_issues/issues/923) | errbit client setup
- Adding User-Agent to import file url request for parallel imports
- Adding User-Agent to import file url request
- [OpenConceptLab/ocl_issues#1035](https://github.com/OpenConceptLab/ocl_issues/issues/1035) Slow concepts and mappings select (partial index)
- Revert "OpenConceptLab/ocl_issues#1035 Slow concepts and mappings select (partial index)"
- [OpenConceptLab/ocl_issues#1035](https://github.com/OpenConceptLab/ocl_issues/issues/1035) Slow concepts and mappings select (partial index)
- [OpenConceptLab/ocl_issues#1035](https://github.com/OpenConceptLab/ocl_issues/issues/1035) Slow concepts and mappings select
- [OpenConceptLab/ocl_issues#1035](https://github.com/OpenConceptLab/ocl_issues/issues/1035) Slow concepts select
- Removing tmp after export file is uploaded
- Bump django from 3.2.7 to 3.2.8
- Merge pull request #69 from OpenConceptLab/dependabot/pip/moto-2.2.9
- Bump moto from 2.2.8 to 2.2.9
- Merge pull request #63 from OpenConceptLab/dependabot/pip/pydash-5.1.0
- pylint | removed unused import
- Concept children/parents | pagination and headers
- Bump pydash from 5.0.2 to 5.1.0
##### 2.0.66 - Thu Oct 7 05:38:06 2021 +0000
- [OpenConceptLab/ocl_issues#1018](https://github.com/OpenConceptLab/ocl_issues/issues/1018) | user org collections/sources search result scope fix
- [OpenConceptLab/ocl_issues#991](https://github.com/OpenConceptLab/ocl_issues/issues/991) | concept children/parents nested children/parents can be asked separately
- Merge pull request #61 from OpenConceptLab/dependabot/pip/coverage-6.0
- Bump coverage from 5.5 to 6.0
- [OpenConceptLab/ocl_issues#991](https://github.com/OpenConceptLab/ocl_issues/issues/991) | API get parents of a concept
- concept detail | not including empty hierarchy path by default
##### 2.0.63 - Mon Oct 4 04:57:25 2021 +0000
- [OpenConceptLab/ocl_issues#1018](https://github.com/OpenConceptLab/ocl_issues/issues/1018) | bulk references add from a source/version to not go through API
- Merge pull request #57 from OpenConceptLab/dependabot/pip/django-cors-headers-3.9.0
- Bump django-cors-headers from 3.8.0 to 3.9.0
##### 2.0.61 - Wed Sep 29 11:40:41 2021 +0000
- [OpenConceptLab/ocl_issues#992](https://github.com/OpenConceptLab/ocl_issues/issues/992) | concept collection membership restricted to user/org scope
- [OpenConceptLab/ocl_issues#1000](https://github.com/OpenConceptLab/ocl_issues/issues/1000) | deleted v1 to v2 data migration code
- Merge pull request #56 from OpenConceptLab/dependabot/pip/boto3-1.18.49
- Bump boto3 from 1.18.42 to 1.18.49
- Merge pull request #54 from OpenConceptLab/dependabot/pip/moto-2.2.8
- List APIs to use ES for non empty search str only
- [OpenConceptLab/ocl_issues#992](https://github.com/OpenConceptLab/ocl_issues/issues/992) | API to get collection memberships for a concept
- [OpenConceptLab/ocl_issues#963](https://github.com/OpenConceptLab/ocl_issues/issues/963) | OpenMRS validator external_id for concept/locales/mapping validations
- Bump moto from 2.2.7 to 2.2.8
##### 2.0.58 - Sat Sep 25 12:16:24 2021 +0000
- populate hierarchy task to log more
- [OpenConceptLab/ocl_issues#988](https://github.com/OpenConceptLab/ocl_issues/issues/988) | source/collection/concept/mapping list api to apply user permissions
##### 2.0.56 - Thu Sep 23 02:20:53 2021 +0000
- Fixing or criteria for searching mnemonic exact
- API to batch index source's concepts and mappings
- POST Concept/Mapping | 404 if parent not found
##### 2.0.54 - Mon Sep 20 07:22:22 2021 +0000
- [OpenConceptLab/ocl_issues#966](https://github.com/OpenConceptLab/ocl_issues/issues/966) | django logging for non-dev env (gunicorn based)
- fixing pylint
- Collection | add all references (*) bug fix
- Pylint | implemented consider-using-f-string fixes
- Pylint | implemented consider-using-f-string
- Merge pull request #47 from OpenConceptLab/dependabot/pip/moto-2.2.7
- Merge pull request #48 from OpenConceptLab/dependabot/pip/pylint-2.11.1
- Bump pylint from 2.10.2 to 2.11.1
- Bump moto from 2.2.6 to 2.2.7
- Merge pull request #46 from OpenConceptLab/dependabot/pip/boto3-1.18.42
- Bump boto3 from 1.18.40 to 1.18.42
- Revert "Revert "OpenConceptLab/ocl_issues#971 | making sure the non REST URLs are not supported""
- Mapping version creation | fixing queries to get and mark prev latest version not latest
- removed internal_reference_id | was used for v1 to v2 data migration
- Adding keep-alive to match ALB
- Adjust gunicorn timeout to 60s
- [OpenConceptLab/ocl_issues#972](https://github.com/OpenConceptLab/ocl_issues/issues/972) | data migration | not adding ocladmin as member to orgs with no members
- Revert "OpenConceptLab/ocl_issues#971 | making sure the non REST URLs are not supported"
- reverting file read encoding
- Increase number of gunicorn workers
- S3 | fixing upload of export file
- Batch delete | chunk size 1000
- [OpenConceptLab/ocl_issues#971](https://github.com/OpenConceptLab/ocl_issues/issues/971) | making sure the non REST URLs are not supported
- [OpenConceptLab/ocl_issues#972](https://github.com/OpenConceptLab/ocl_issues/issues/972) | data migration | making sure related_name is not used
- [OpenConceptLab/ocl_issues#972](https://github.com/OpenConceptLab/ocl_issues/issues/972) | data migration | making sure related_name is not used as string
- [OpenConceptLab/ocl_issues#972](https://github.com/OpenConceptLab/ocl_issues/issues/972) | data migration to add creator and updater as org member in orgs without any members
- Delete source can be an async call
##### 2.0.52 - Mon Sep 13 11:57:24 2021 +0000
- Adding capture output for gunicorn
- Merge pull request #44 from OpenConceptLab/dependabot/pip/boto3-1.18.40
- Bump boto3 from 1.18.39 to 1.18.40
- data/file upload max memory size can be upto 200mb
- [OpenConceptLab/ocl_issues#965](https://github.com/OpenConceptLab/ocl_issues/issues/965) Using Gunicorn for Swagger and disabling DEBUG mode
- Merge pull request #42 from OpenConceptLab/dependabot/pip/boto3-1.18.39
- Bump boto3 from 1.18.37 to 1.18.39
- [OpenConceptLab/ocl_issues#957](https://github.com/OpenConceptLab/ocl_issues/issues/957) | parallel importers | using deque to manage parts list
- CSV sample with special characters
- [OpenConceptLab/ocl_issues#960](https://github.com/OpenConceptLab/ocl_issues/issues/960) | using Python 3 style super() without arguments
- [OpenConceptLab/ocl_issues#960](https://github.com/OpenConceptLab/ocl_issues/issues/960) | fixed pylint warnings
- Merge pull request #32 from OpenConceptLab/dependabot/pip/pylint-2.10.2
- Merge pull request #40 from OpenConceptLab/dependabot/pip/boto3-1.18.37
- Bump boto3 from 1.18.36 to 1.18.37
- Bump pylint from 2.5.3 to 2.10.2
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
