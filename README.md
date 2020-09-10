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
