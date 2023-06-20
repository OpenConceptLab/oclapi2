# oclapi2
The new and improved OCL terminology service v2


#### Dev Setup
1. `sysctl -w vm.max_map_count=262144` #required by Elasticsearch
2. `docker-compose up -d`
3. Go to http://localhost:8000/swagger/ to benefit.


#### Dev Setup with KeyCloak (SSO)
1. `sysctl -w vm.max_map_count=262144` #required by Elasticsearch
2. `docker-compose -f docker-compose.yml -f docker-compose.sso.yml up -d`
3. Go to http://localhost:8000/swagger/ to benefit.
4. Go to http://localhost:8080 for keyCloak.

#### Run Checks
(use the `docker exec` command in a service started with `docker-compose up -d`)
1. Pylint (pep8):
   
   `docker exec -it oclapi2_api_1 pylint -j2 core` 

    or

   `docker-compose -f docker-compose.yml -f docker-compose.ci.yml run --rm api pylint -j0 core`
2. Coverage

   `docker exec -it oclapi2_api_1 bash coverage.sh`

   or

   `docker-compose -f docker-compose.yml -f docker-compose.ci.yml run --rm api bash coverage.sh`
3. Tests

    `docker exec -it oclapi2_api_1  python manage.py test --keepdb -v3` 

    or

    `docker exec -it oclapi2_api_1  python manage.py test --keepdb -v3 -- core.sources.tests.tests.SourceTest` 

    or

    `docker-compose -f docker-compose.yml -f docker-compose.ci.yml run --rm api python manage.py test --keepdb -v3`

### DB migrations
After modifying model you need to create migration files. Run:

`docker-compose run --rm api python manage.py makemigrations`

Make sure to commit newly created migration files.

### Indexing in OpenSearch:
- `cd oclapi2/`
- `docker exec -it oclapi2-api-1  python manage.py opensearch index --force rebuild` -- for rebuild (delete and create) all indexes.
- `docker exec -it oclapi2-api-1  python manage.py opensearch index --force rebuild concepts mappings` -- for rebuilding specific indexes.
- `docker exec -it oclapi2-api-1  python manage.py opensearch document --force index --missing` -- for populating all missing indexes
[Read More](https://django-opensearch-dsl.readthedocs.io/en/latest/management/)


### Debugging

In order to debug tests or api you can use PDB. Set a breakpoint in code with:

`import pdb; pdb.set_trace()`

Run tests with:

`docker-compose run --rm api python manage.py test core.code_systems --keepdb -v3`

Run api with:

`docker-compose run --rm --service-ports api`

### Importing FHIR resources

In order to import FHIR resources run:

`docker-compose run --no-deps --rm -v $(pwd)/../oclfhir-tests/definitions.json:/fhir api python tools/fhir_import.py -d /fhir -p http://api:8000/orgs/test -t 891b4b17feab99f3ff7e5b5d04ccc5da7aa96da6 -r CodeSystem`

Please note that the 'test' org must exist. For help run:

`docker-compose run --no-deps --rm api python tools/fhir_import.py -h`

### Release

Every build is a candidate for release.

In order to release please trigger the release build step in [our CI](https://ci.openmrs.org/browse/OCL-OCLAPI2/latest). Please note
that the maintenance version will be automatically increased after a successful release. It is desired only, if you are releasing the latest build and
should be turned off by setting the increaseMaintenanceRelease variable to false on the Run stage "Release" popup in other cases.

A deployment release will be automatically created and pushed to the staging environment.

#### Major/minor version increase

In order to increase major/minor version you need to set the new version in [core/\_\_init\_\_.py](core/__init__.py). Alongside you need to login to our CI and update the next release version on a deployment plan [here](https://ci.openmrs.org/deploy/config/configureDeploymentProjectVersioning.action?id=205619201) with the same value.

### Deployment

In order to deploy please trigger the deployment [here](https://ci.openmrs.org/deploy/viewDeploymentProjectEnvironments.action?id=205619201).
Please use an existing deployment release.


## Contributing to OCLAPI2
We welcome contributions. Please see [CONTRIBUTING.md](CONTRIBUTING.md) to get started!
