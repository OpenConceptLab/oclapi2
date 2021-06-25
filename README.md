# oclapi2
The new and improved OCL terminology service v2


#### Dev Setup
1. `sysctl -w vm.max_map_count=262144` #required by Elasticsearch
2. `docker-compose up -d`
3. Go to http://localhost:8000/swagger/ to benefit.

#### Run Checks
1. Pylint (pep8) --- `docker exec -it oclapi2_api_1 pylint -j2 core`
2. Coverage -- `docker exec -it oclapi2_api_1 bash coverage.sh`
2. Tests --- `docker exec -it oclapi2_api_1  python manage.py test --keepdb -v3`

### Release

Every build is a candidate for release.

In order to release please trigger the release build step in [our CI](https://ci.openmrs.org/browse/OCL-OCLAPI2/latest)

You also need to create a deployment release [here](https://ci.openmrs.org/deploy/createDeploymentVersion.action?deploymentProjectId=205619201).
Please set the release version to match the version defined in core/__init__.py.

Do remember to increase maintenance release version in package.json after a successful release (if releasing the latest build).

### Deployment

In order to deploy please trigger the deployment [here](https://ci.openmrs.org/deploy/viewDeploymentProjectEnvironments.action?id=205619201).
Please use an existing deployment release.
