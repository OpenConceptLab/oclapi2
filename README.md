# oclapi2
The new and improved OCL terminology service v2


#### Dev Setup
1. `docker-compose up -d`
2. Go to localhost:7000 to benefit.

#### Run Checks
1. Pylint (pep8) --- `docker exec -it oclapi2 pylint -j2 core`
1. Tests --- `docker exec -it oclapi2  python manage.py test --keepdb -v3`
