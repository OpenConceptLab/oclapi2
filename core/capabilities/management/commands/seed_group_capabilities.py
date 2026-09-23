import json
import os

import yaml
from django.core.management import BaseCommand, CommandError
from django.db import transaction

GROUPS_CONFIG_ENV = 'OCL_GROUPS_CONFIG'

# Plan groups are deployment config: {"groups": {"<name>": {"permissions": [...], "capabilities": {...}}}}
# Seed-only - creates missing groups/limits and adds missing permissions; never overwrites or removes.


def seed_groups(config, stdout=None):
    def warn(msg):
        if stdout:
            stdout.write(f'WARNING: {msg}')

    for group_name, group_config in validate_config(config).items():
        with transaction.atomic():
            seed_group(group_name, group_config or {}, warn)


def seed_group(group_name, group_config, warn):
    from django.contrib.auth.models import Group, Permission

    from core.capabilities.models import Capability, GroupCapability

    group, _ = Group.objects.get_or_create(name=group_name)
    # add-only: listed permissions are (re)granted, ones already on the group are never removed
    codenames = group_config.get('permissions') or []
    permissions = list(Permission.objects.filter(codename__in=codenames, content_type__app_label='users'))
    missing = set(codenames) - {permission.codename for permission in permissions}
    if missing:
        warn(f'{group_name}: unknown permissions skipped: {sorted(missing)}')
    group.permissions.add(*permissions)

    for capability_name, limit in (group_config.get('capabilities') or {}).items():
        capability = Capability.objects.filter(name=capability_name).first()
        if not capability:
            warn(f'{group_name}: unknown capability skipped: {capability_name}')
            continue
        GroupCapability.objects.get_or_create(group=group, capability=capability, defaults={'limit': limit})


def validate_config(config):
    if not isinstance(config, dict) or not isinstance(config.get('groups'), dict):
        raise CommandError('groups config must be an object with a "groups" mapping')
    for group_name, group_config in config['groups'].items():
        if group_config is None:
            continue
        if not isinstance(group_config, dict):
            raise CommandError(f'{group_name}: config must be a mapping')
        permissions = group_config.get('permissions')
        if permissions is not None and not isinstance(permissions, list):
            raise CommandError(f'{group_name}: permissions must be a list')
        capabilities = group_config.get('capabilities')
        if capabilities is not None and (
                not isinstance(capabilities, dict) or
                not all(isinstance(limit, int) and not isinstance(limit, bool) for limit in capabilities.values())
        ):
            raise CommandError(f'{group_name}: capabilities must map names to integer limits')
    return config['groups']


class Command(BaseCommand):
    help = f'seed groups, their permissions and capability limits from {GROUPS_CONFIG_ENV} or --file, ' \
           'without overwriting any that already exist'

    def add_arguments(self, parser):
        parser.add_argument('--file', help='path to a yaml/json groups config (instead of the env var)')

    def handle(self, *args, **options):
        config = self.load_config(options.get('file'))
        if config is None:
            self.stdout.write(f'No groups config ({GROUPS_CONFIG_ENV} not set), nothing to seed')
            return
        seed_groups(config, self.stdout)
        self.stdout.write(f'Seeded groups: {", ".join(config["groups"].keys())}')

    @staticmethod
    def load_config(file_path):
        if file_path:
            try:
                with open(file_path, encoding='utf-8') as file:
                    return yaml.safe_load(file)  # yaml is a superset of json
            except (OSError, yaml.YAMLError) as ex:
                raise CommandError(f'Could not read groups config file {file_path}: {ex}') from ex

        raw = os.environ.get(GROUPS_CONFIG_ENV)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as ex:
            raise CommandError(f'{GROUPS_CONFIG_ENV} is not valid JSON: {ex}') from ex
