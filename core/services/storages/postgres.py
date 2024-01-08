from django.db import connection


class PostgresQL:
    @staticmethod
    def create_seq(seq_name, owned_by, min_value=0, start=1):
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE SEQUENCE IF NOT EXISTS {seq_name} MINVALUE {min_value} START {start} OWNED BY {owned_by};")

    @staticmethod
    def update_seq(seq_name, start):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT setval('{seq_name}', {start}, true);")

    @staticmethod
    def drop_seq(seq_name):
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SEQUENCE IF EXISTS {seq_name};")

    @staticmethod
    def next_value(seq_name):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT nextval('{seq_name}');")
            return cursor.fetchone()[0]

    @staticmethod
    def last_value(seq_name):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT last_value from {seq_name};")
            return cursor.fetchone()[0]
