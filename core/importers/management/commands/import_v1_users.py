import json
from pprint import pprint

from django.core.management import BaseCommand
from pydash import get

from core.common.tasks import populate_indexes
from core.orgs.models import Organization
from core.users.models import UserProfile


class Command(BaseCommand):
    help = 'import v1 users'

    total = 0
    processed = 0
    created = []
    existed = []
    updated = []
    failed = []

    @staticmethod
    def log(msg):
        print("*******{}*******".format(msg))

    def handle(self, *args, **options):
        FILE_PATH = '/code/core/importers/v1_dump/data/exported_users.json'
        lines = open(FILE_PATH, 'r').readlines()

        self.log('STARTING USERS IMPORT')
        self.total = len(lines)
        self.log('TOTAL: {}'.format(self.total))

        for line in lines:
            data = json.loads(line)
            original_data = data.copy()
            self.processed += 1
            data.pop('_id')
            _id = data.pop('user_id')
            # created_at = data.pop('created_at')
            # updated_at = data.pop('updated_at')
            last_login = data.pop('last_login')
            date_joined = data.pop('date_joined')
            full_name = data.pop('full_name') or ''
            name_parts = list(set(full_name.split(' ')))
            first_name = data.pop('first_name', '') or ' '.join(name_parts[:-1])
            last_name = data.pop('last_name', '') or name_parts[-1]
            orgs = data.pop('organizations', [])
            password = data.pop('password')
            hashed_password = data.pop('hashed_password')
            password = password or hashed_password
            data['verified'] = data.pop('verified_email', True)

            data['last_name'] = last_name
            data['internal_reference_id'] = get(_id, '$oid')
            # data['created_at'] = get(created_at, '$date')
            # data['updated_at'] = get(updated_at, '$date')
            data['date_joined'] = get(date_joined, '$date')
            data['last_login'] = get(last_login, '$date')
            data['first_name'] = first_name
            username = data.get('username')
            self.log("Processing: {} ({}/{})".format(username, self.processed, self.total))
            queryset = UserProfile.objects.filter(username=username)
            if queryset.exists():
                self.updated.append(original_data)
                user = queryset.first()
                user.organizations.set(Organization.objects.filter(internal_reference_id__in=orgs))
            else:
                try:
                    user = UserProfile.objects.create(**data)
                    if user:
                        self.created.append(original_data)
                        user.password = password
                        user.save()
                        user.organizations.set(Organization.objects.filter(internal_reference_id__in=orgs))
                    else:
                        self.failed.append(original_data)
                except Exception as ex:
                    self.failed.append({**original_data, 'errors': ex.args})
        populate_indexes.delay(['users', 'orgs'])
        self.log(
            "Result: Created: {} | Updated: {} | Failed: {}".format(
                len(self.created), len(self.updated), len(self.failed),
            )
        )
        if self.existed:
            self.log("Updated")
            pprint(self.updated)

        if self.failed:
            self.log("Failed")
            pprint(self.failed)
