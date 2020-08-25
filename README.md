# oclapi2
The new and improved OCL terminology service v2


#### Dev Setup
1. `sysctl -w vm.max_map_count=262144` #required by Elasticsearch
2. `docker-compose up -d`
3. Go to http://localhost:8000/swagger/ to benefit.

#### Run Checks
1. Pylint (pep8) --- `docker exec -it oclapi2 pylint -j2 core`
1. Tests --- `docker exec -it oclapi2  python manage.py test --keepdb -v3`
