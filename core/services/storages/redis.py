import json

from django_redis import get_redis_connection


class RedisService:  # pragma: no cover
    @staticmethod
    def get_client():
        return get_redis_connection('default')

    def set(self, key, val, **kwargs):
        return self.get_client().set(key, val, **kwargs)

    def set_json(self, key, val):
        return self.get_client().set(key, json.dumps(val))

    def get_formatted(self, key):
        val = self.get(key)
        if isinstance(val, bytes):
            val = val.decode()

        try:
            val = json.loads(val)
        except:  # pylint: disable=bare-except
            pass

        return val

    def exists(self, key):
        return self.get_client().exists(key)

    def get(self, key):
        return self.get_client().get(key)

    def keys(self, pattern):
        return self.get_client().keys(pattern)

    def get_int(self, key):
        return int(self.get_client().get(key).decode('utf-8'))

    def get_pending_tasks(self, queue, include_task_names, exclude_task_names=None):
        # queue = 'bulk_import_root'
        # task_name = 'core.common.tasks.bulk_import_parallel_inline'
        values = self.get_client().lrange(queue, 0, -1)
        tasks = []
        exclude_task_names = exclude_task_names or []
        if values:
            for value in values:
                val = json.loads(value.decode('utf-8'))
                headers = val.get('headers')
                task_name = headers.get('task')
                if headers.get('id') and task_name in include_task_names and task_name not in exclude_task_names:
                    tasks.append(
                        {'task_id': headers['id'], 'task_name': headers['task'], 'state': 'PENDING', 'queue': queue}
                    )
        return tasks
