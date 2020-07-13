python manage.py migrate

echo "Importing Base Entities"
python manage.py loaddata core/fixtures/*

echo "Importing Lookup Values"
python manage.py import_lookup_values

python manage.py runserver 0.0.0.0:8000
