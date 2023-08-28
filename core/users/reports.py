from pydash import get

from core.reports.models import AbstractReport
from core.users.models import UserProfile


class UserReport(AbstractReport):
    queryset = UserProfile.objects.filter()
    name = 'Users'
    verbose_fields = ['username', 'email', 'name', 'date_joined', 'status']
    VERBOSE_HEADERS = ["Username", "Email", "Name", "Joined At", "Status"]

    @classmethod
    def get_authoring_report(cls, usernames):
        users = UserProfile.objects.filter(username__in=usernames)
        result = {}
        for user in users:
            user_result = {}
            for app, model in [
                    ('concepts', 'concept'), ('mappings', 'mapping'), ('sources', 'source'),
                    ('collections', 'collection'), ('users', 'userprofile'), ('orgs', 'organization')
            ]:
                created = get(user, f"{app}_{model}_related_created_by").count()
                updated = get(user, f"{app}_{model}_related_updated_by").count()
                user_result[app] = {'created': created, 'updated': updated}

            result[user.username] = user_result

        return result
