import json

from django.core.exceptions import ValidationError
from django.core.management import BaseCommand, CommandError

from core.capacity.config import get_current, save_config, diff
from core.capacity.constants import SOURCE_COMMAND
from core.capacity.limiter import get_status

# manage.py capacity show                       the config in force
# manage.py capacity set mode=enforce tiers.preview=1 [--note ...] [--user <username>]
# manage.py capacity reset [--note ...]         back to the defaults
# manage.py capacity history [--limit N]        changes, newest first
# manage.py capacity status                     heavy calls in flight now, by lane


def parse_assignment(assignment):
    """'tiers.preview=1' -> {'tiers': {'preview': 1}}"""
    path, sep, raw = assignment.partition('=')
    if not sep or not path:
        raise CommandError(f'Expected <setting>=<value>, got "{assignment}".')
    value = int(raw) if raw.lstrip('-').isdigit() else raw
    for key in reversed(path.split('.')):
        value = {key: value}
    return value


class Command(BaseCommand):
    help = 'Show or change the capacity limit on heavy calls (semantic $match, $rerank).'

    def add_arguments(self, parser):
        parser.add_argument('action', choices=['show', 'set', 'reset', 'history', 'status'])
        parser.add_argument('assignments', nargs='*', help='For set: <setting>=<value>, e.g. tiers.preview=1')
        parser.add_argument('--note', default='', help='Why, recorded with the change')
        parser.add_argument('--user', default=None, help='Username to record the change under')
        parser.add_argument('--limit', type=int, default=20, help='For history: how many changes')

    def handle(self, *args, **options):
        action = options['action']
        if action == 'show':
            row, config = get_current()
            self.print({'version': row.id if row else None, 'config': config})
        elif action == 'status':
            self.print(get_status(get_current()[1]))
        elif action == 'history':
            from core.capacity.models import CapacityConfig
            for row in CapacityConfig.objects.select_related('created_by').order_by('-id')[:options['limit']]:
                self.print({
                    'version': row.id, 'created_at': row.created_at.isoformat(), 'source': row.source,
                    'created_by': row.created_by.username if row.created_by else None, 'note': row.note or None,
                    'changes': diff(row.previous_config, row.config),
                })
        else:
            self.save(action, options)

    def save(self, action, options):
        if action == 'set' and not options['assignments']:
            raise CommandError('Give at least one <setting>=<value>.')
        changes = {}
        for assignment in options['assignments'] if action == 'set' else []:
            changes = self.merge(changes, parse_assignment(assignment))
        user = None
        if options['user']:
            from core.users.models import UserProfile
            user = UserProfile.objects.filter(username=options['user']).first()
            if not user:
                raise CommandError(f'No user "{options["user"]}".')
        try:
            row, _, changed = save_config(
                changes, user=user, source=SOURCE_COMMAND, note=options['note'], replace=action == 'reset')
        except ValidationError as ex:
            raise CommandError(' '.join(ex.messages)) from ex
        self.print({'version': row.id if row else None, 'changes': changed})

    @classmethod
    def merge(cls, base, changes):
        for key, value in changes.items():
            base[key] = cls.merge(base.get(key, {}), value) if isinstance(value, dict) else value
        return base

    def print(self, data):
        self.stdout.write(json.dumps(data, indent=2, default=str))
