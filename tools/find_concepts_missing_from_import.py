"""
Read-only: list the HEAD concepts that a bulk-import file does not mention (OpenConceptLab/ocl_online#297 follow-up,
"anything not in the latest import is retired").

    tools/run_via_bastion.sh tools/find_concepts_missing_from_import.py --head-id 32 \
        --import-file loinc-2.82/concepts.json --csv missing.csv

For every versioned object in HEAD whose code (mnemonic) is not in the file it reports whether the latest version is
already retired, when that version was last written, and whether it belongs to a twin pair. Nothing is written.
"""
import argparse
import csv
import json
import os
import sys
from collections import Counter

import psycopg2
import psycopg2.extensions
import psycopg2.extras

# lets Ctrl-C interrupt a query that is still running on the server
psycopg2.extensions.set_wait_callback(psycopg2.extras.wait_select)

SQL = """
SELECT v.id AS vo, v.mnemonic, v.concept_class, l.id AS latest_id, l.retired, l.created_at AS latest_created
FROM concepts v
LEFT JOIN concepts l ON l.versioned_object_id = v.id AND l.id <> v.id AND l.is_latest_version
WHERE v.parent_id = %(head_id)s AND v.id = v.versioned_object_id
"""


def pg_connect():
    return psycopg2.connect(
        dbname=os.environ.get('DB', 'postgres'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'Postgres123'),
        host=os.environ.get('DB_HOST', 'localhost'),
        port=os.environ.get('DB_PORT', 5432),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--head-id', type=int, required=True)
    parser.add_argument('--import-file', required=True, help='the concepts.json lines file (one JSON object per line)')
    parser.add_argument('--csv', help='write the missing concepts to this file')
    args = parser.parse_args()

    in_file = set()
    with open(args.import_file, encoding='utf-8') as f:
        for line in f:
            if line.strip():
                in_file.add(json.loads(line)['id'])
    print(f'codes in the import file: {len(in_file)}')

    conn = pg_connect()
    with conn.cursor() as cur:
        cur.execute('SELECT version FROM sources WHERE id = %s', (args.head_id,))
        found = cur.fetchone()
        if not found or found[0] != 'HEAD':
            sys.exit(f'source {args.head_id} is not a HEAD')
        cur.execute(SQL, {'head_id': args.head_id})
        cols = [d[0] for d in cur.description]
        vos = [dict(zip(cols, r)) for r in cur.fetchall()]

    per_code = Counter(v['mnemonic'] for v in vos)
    missing = [v for v in vos if v['mnemonic'] not in in_file]
    print(f'HEAD versioned objects: {len(vos)}, distinct codes: {len(per_code)}')
    print(f'in HEAD but not in the file: {len(missing)} VOs, {len({v["mnemonic"] for v in missing})} codes')
    print(f'  already retired: {sum(1 for v in missing if v["retired"])}')
    print(f'  not retired (would need retiring): {sum(1 for v in missing if not v["retired"])}')
    print(f'  in the file but not in HEAD: {len(in_file - set(per_code))}')
    by_class = Counter(v['concept_class'] for v in missing if not v['retired'])
    print('  not retired, by class:', by_class.most_common(8))
    by_year = Counter(str(v['latest_created'])[:4] for v in missing if not v['retired'])
    print('  not retired, latest version written in:', sorted(by_year.items()))
    if args.csv and missing:
        with open(args.csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=list(missing[0]))
            writer.writeheader()
            writer.writerows(missing)
        print(f'written to {args.csv}')


if __name__ == '__main__':
    main()
