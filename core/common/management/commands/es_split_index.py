from datetime import datetime

from django.core.management import BaseCommand, CommandError
from elasticsearch_dsl.connections import connections

# manage.py es_split_index concepts --shards 6 --dry-run    preflight checks and the plan, no changes
# manage.py es_split_index concepts --shards 6 [--yes]      split, then swap the alias (asks first unless --yes)
# Hard-links segments into <index>-<timestamp> (writes blocked for minutes), then atomically swaps in an alias <index>.


class Command(BaseCommand):
    client = None
    help = 'Split a single-shard ES index into N primaries behind an alias of the same name.'

    def add_arguments(self, parser):
        parser.add_argument('index', help='Concrete index to split, e.g. concepts')
        parser.add_argument('--shards', type=int, required=True, help='Primary shards of the new index')
        parser.add_argument('--dry-run', action='store_true', help='Run the preflight checks only')
        parser.add_argument('--yes', action='store_true', help="Don't ask before swapping the alias")
        parser.add_argument('--timeout', type=int, default=3600, help='Seconds to wait for the new index to be green')
        parser.add_argument(
            '--max-disk-percent', type=int, default=85, help='Abort if the shard\'s node disk use is above this')

    def handle(self, *args, **options):
        index = options['index']
        shards = options['shards']
        self.client = client = connections.get_connection()

        store_bytes, node = self.preflight(index, shards, options['max_disk_percent'])
        target = f"{index}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        self.stdout.write(
            f"Plan: block writes on '{index}' ({store_bytes / 1024 ** 3:.1f} GB on {node}), split it into '{target}' "
            f"with {shards} primaries and 0 replicas, check doc counts, then point alias '{index}' at '{target}' "
            f"and delete '{index}'.")
        if options['dry_run']:
            self.stdout.write('Dry run: nothing changed.')
            return

        client.indices.put_settings(index=index, settings={'index.blocks.write': True})
        self.stdout.write(f"Blocked writes on '{index}'.")
        try:
            self.split(index, target, shards, options['timeout'])
            self.check_counts(index, target)
            if not options['yes'] and input(
                    f"Point alias '{index}' at '{target}' and delete index '{index}'? [y/N]: ").lower() != 'y':
                raise CommandError('Aborted before the alias swap.')
            client.indices.update_aliases(actions=[
                {'add': {'index': target, 'alias': index}},
                {'remove_index': {'index': index}},
            ])
        except BaseException as ex:  # pylint: disable=broad-exception-caught
            self.rollback(index, target)
            if isinstance(ex, CommandError):
                raise
            raise CommandError(f'Split failed and was rolled back: {ex}') from ex

        self.stdout.write(self.style.SUCCESS(f"Alias '{index}' now points at '{target}'; old index deleted."))
        self.stdout.write(
            f"Next: let shards rebalance, then POST {index}/_forcemerge?only_expunge_deletes=true, then "
            f"PUT {index}/_settings {{\"index.number_of_replicas\": 1}}.")

    def preflight(self, index, shards, max_disk_percent):
        """Returns (primary store bytes, node) once every check passes; raises CommandError otherwise."""
        client = self.client
        if shards < 2:
            raise CommandError('--shards must be at least 2.')
        if client.indices.exists_alias(name=index):
            raise CommandError(f"'{index}' is already an alias, nothing to split.")
        if not client.indices.exists(index=index):
            raise CommandError(f"Index '{index}' doesn't exist.")

        index_settings = client.indices.get_settings(index=index)[index]['settings']['index']
        if int(index_settings['number_of_shards']) != 1:
            raise CommandError(f"'{index}' has {index_settings['number_of_shards']} primaries; only 1 is supported.")
        if index_settings.get('blocks'):
            raise CommandError(f"'{index}' has blocks set ({index_settings['blocks']}); clear them first.")

        health = client.cluster.health(index=index)['status']
        if health != 'green':
            raise CommandError(f"'{index}' is {health}, not green.")

        primary = next(
            shard for shard in client.cat.shards(index=index, format='json', bytes='b') if shard['prirep'] == 'p')
        store_bytes, node = int(primary['store']), primary['node']
        allocation = next(
            row for row in client.cat.allocation(format='json', bytes='b') if row['node'] == node)
        if int(allocation['disk.percent']) > max_disk_percent:
            raise CommandError(f"{node} disk is {allocation['disk.percent']}% used, above {max_disk_percent}%.")
        if int(allocation['disk.avail']) < store_bytes:
            raise CommandError(
                f"{node} has {int(allocation['disk.avail']) / 1024 ** 3:.1f} GB free; the split can need up to "
                f"{store_bytes / 1024 ** 3:.1f} GB as the new shards merge apart.")
        return store_bytes, node

    def split(self, index, target, shards, timeout):
        client = self.client
        client.indices.flush(index=index)
        client.options(request_timeout=timeout).indices.split(
            index=index, target=target, settings={
                'index.number_of_shards': shards,
                'index.number_of_replicas': 0,
                'index.blocks.write': None,
            })
        self.stdout.write(f"Split '{index}' into '{target}', waiting for green...")
        health = client.options(request_timeout=timeout + 30).cluster.health(
            index=target, wait_for_status='green', timeout=f'{timeout}s')
        if health['timed_out'] or health['status'] != 'green':
            raise CommandError(f"'{target}' isn't green after {timeout}s ({health['status']}).")

    def check_counts(self, index, target):
        client = self.client
        client.indices.refresh(index=[index, target])
        source_count = client.count(index=index)['count']
        target_count = client.count(index=target)['count']
        if source_count != target_count:
            raise CommandError(f"Doc counts differ: '{index}' {source_count}, '{target}' {target_count}.")
        self.stdout.write(f'Doc counts match: {source_count}.')

    def rollback(self, index, target):
        client = self.client
        client.indices.delete(index=target, ignore_unavailable=True)
        client.indices.put_settings(index=index, settings={'index.blocks.write': None})
        self.stderr.write(f"Rolled back: deleted '{target}' (if created) and cleared the write block on '{index}'.")
