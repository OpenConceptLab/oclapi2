from datetime import datetime

from django.core.management import BaseCommand, CommandError
from django.utils import timezone
from elasticsearch_dsl.connections import connections

# manage.py es_split_index concepts --shards 6 --dry-run    preflight checks and the plan, no changes
# manage.py es_split_index concepts --shards 6 [--yes]      split, then swap the alias (asks first unless --yes)
# Hard-links segments into <index>-<timestamp> (writes blocked for minutes), then atomically swaps in an alias <index>.

REQUEST_TIMEOUT = 120


class Command(BaseCommand):
    client = None
    help = 'Split a single-shard ES index into N primaries behind an alias of the same name.'

    def add_arguments(self, parser):
        parser.add_argument('index', help='Concrete index to split, e.g. concepts')
        parser.add_argument('--shards', type=int, required=True, help='Primary shards of the new index')
        parser.add_argument('--dry-run', action='store_true', help='Run the preflight checks only')
        parser.add_argument('--yes', action='store_true', help="Don't ask before blocking writes")
        parser.add_argument('--timeout', type=int, default=3600, help='Seconds to wait for the new index to be green')
        parser.add_argument(
            '--max-disk-percent', type=int, default=85,
            help="Abort if the shard's node could pass this disk use while the new shards merge apart")

    def handle(self, *args, **options):
        index = options['index']
        shards = options['shards']
        self.client = client = connections.get_connection().options(request_timeout=REQUEST_TIMEOUT)

        store_bytes, node = self.preflight(index, shards, options['max_disk_percent'])
        target = f"{index}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        self.stdout.write(
            f"Plan: block writes on '{index}' ({store_bytes / 1024 ** 3:.1f} GB on {node}), split it into '{target}' "
            f"with {shards} primaries and 0 replicas pinned to {node}, check doc counts, then point alias '{index}' at "
            f"'{target}' and delete '{index}'.")
        if options['dry_run']:
            self.stdout.write('Dry run: nothing changed.')
            return
        if not options['yes'] and input(f"Block writes on '{index}' and go ahead? [y/N]: ").lower() != 'y':
            raise CommandError('Aborted: nothing changed.')

        blocked_at = timezone.now().isoformat()
        try:
            client.indices.add_block(index=index, block='write')  # unlike the setting, waits for in-flight writes
            self.stdout.write(f"Blocked writes on '{index}' at {blocked_at}.")
            self.split(index, target, shards, node, options['timeout'])
            self.check_counts(index, target)
            # No client retry: a resent swap fails once the first went through, and would look like a failed swap.
            client.options(max_retries=0).indices.update_aliases(actions=[
                {'add': {'index': target, 'alias': index}},
                {'remove_index': {'index': index}},
            ])
        except BaseException as ex:  # pylint: disable=broad-exception-caught
            swapped = self.is_swapped(index, target)
            if not swapped:
                self.rollback(index, target, ex, delete_target=swapped is False)
            self.stderr.write(f"The swap went through although it reported: {ex}")

        self.stdout.write(self.style.SUCCESS(f"Alias '{index}' now points at '{target}'; old index deleted."))
        self.stdout.write(
            f"If the indexing worker wasn't paused, re-index what changed while writes were blocked: POST "
            f"/indexes/resources/{index}/ with filter={{\"updated_at__gte\": \"{blocked_at}\"}}.\n"
            f"Next: POST {index}/_forcemerge?only_expunge_deletes=true and wait for merges to finish, then "
            f"PUT {index}/_settings {{\"index.routing.allocation.require._name\": null}} to let shards rebalance, "
            f"then PUT {index}/_settings {{\"index.number_of_replicas\": 1}}.")

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
        # Hard-linked source files are only freed once every new shard has merged away from them.
        peak_percent = 100 * (int(allocation['disk.used']) + store_bytes) / int(allocation['disk.total'])
        if peak_percent > max_disk_percent:
            raise CommandError(
                f"{node} could reach {peak_percent:.0f}% disk while the new shards merge apart (used + "
                f"{store_bytes / 1024 ** 3:.1f} GB), above {max_disk_percent}%.")
        return store_bytes, node

    def split(self, index, target, shards, node, timeout):  # pylint: disable=too-many-arguments
        client = self.client
        client.indices.flush(index=index)
        client.options(request_timeout=timeout).indices.split(
            index=index, target=target, settings={
                'index.number_of_shards': shards,
                'index.number_of_replicas': 0,
                'index.blocks.write': None,
                # Each new shard holds the whole source via hard links until merged; moving it early copies all that.
                'index.routing.allocation.require._name': node,
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

    def is_swapped(self, index, target):
        """Whether the alias points at the target already; None if ES couldn't be asked."""
        try:
            return bool(self.client.indices.exists_alias(name=index, index=target))
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    def rollback(self, index, target, error, delete_target):
        """Deletes the target (only if it's known not to be serving) and clears the write block, then raises."""
        cleanup_errors = []
        if delete_target:
            try:
                self.client.indices.delete(index=target, ignore_unavailable=True)
            except Exception as ex:  # pylint: disable=broad-exception-caught
                cleanup_errors.append(f"deleting '{target}': {ex}")
        else:
            cleanup_errors.append(f"couldn't tell whether alias '{index}' points at '{target}', so kept it")
        try:
            self.client.indices.put_settings(index=index, settings={'index.blocks.write': None})
        except Exception as ex:  # pylint: disable=broad-exception-caught
            cleanup_errors.append(f"clearing the write block on '{index}': {ex}")

        if cleanup_errors:
            raise CommandError(
                f"Split failed: {error}. Rollback incomplete ({'; '.join(cleanup_errors)}): check _cat/aliases, "
                f"and the write block on '{index}'.") from error
        self.stderr.write(f"Rolled back: deleted '{target}' (if created) and cleared the write block on '{index}'.")
        if isinstance(error, CommandError):
            raise error
        raise CommandError(f'Split failed and was rolled back: {error}') from error
