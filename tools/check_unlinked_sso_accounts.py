"""
Checks active accounts with no sso_id against Keycloak (OpenConceptLab/ocl_online#573). Such an account now links on
sign-in only when the Keycloak user with its username has the account's email, verified. Each account is reported as:

    ok                  Keycloak has the username with the same, verified email: it links on its next sign-in
    email_unverified    same email, but not verified in Keycloak: refused until the owner verifies it
    email_differs       Keycloak has the username with another email: refused, link by hand once ownership is confirmed
    no_keycloak_user    nobody has the username in Keycloak
    keycloak_user_taken the Keycloak user with the username is already linked to another account

--link-all links every email_differs and email_unverified account, the ones the check would lock out. --link links
only the usernames passed. Either writes only with --apply; --link-all --apply also needs --expect with the count from
the dry run, so nothing links that wasn't in the reviewed report.

Usage (inside the api container):
    python tools/check_unlinked_sso_accounts.py [--csv report.csv]
    python tools/check_unlinked_sso_accounts.py --link-all
    python tools/check_unlinked_sso_accounts.py --link-all --apply --expect 20
    python tools/check_unlinked_sso_accounts.py --link alice Bob [--apply]
"""
import argparse
import csv
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

import django  # noqa: E402
django.setup()

from core.services.auth.openid import OpenIDAuthService  # noqa: E402
from core.users.models import UserProfile  # noqa: E402

LOCKED_OUT = ('email_differs', 'email_unverified')
FIELDS = ['status', 'username', 'id', 'email', 'last_login', 'date_joined', 'keycloak_id', 'keycloak_username',
          'keycloak_email', 'keycloak_email_verified']


def normalize(email):
    return (email or '').strip().lower()


def keycloak_user_for(user, keycloak_users):
    return keycloak_users.get(user.username) or keycloak_users.get(user.username.lower())


def check(user, kc_user, linked):
    if not kc_user:
        return 'no_keycloak_user'
    if kc_user['id'] in linked:
        return 'keycloak_user_taken'
    if not normalize(user.email) or normalize(user.email) != normalize(kc_user.get('email')):
        return 'email_differs'
    if kc_user.get('emailVerified') is not True:
        return 'email_unverified'
    return 'ok'


def row_for(user, kc_user, status):
    kc_user = kc_user or {}
    return {
        'status': status, 'username': user.username, 'id': user.id, 'email': user.email,
        'last_login': user.last_login, 'date_joined': user.date_joined, 'keycloak_id': kc_user.get('id'),
        'keycloak_username': kc_user.get('username'), 'keycloak_email': kc_user.get('email'),
        'keycloak_email_verified': kc_user.get('emailVerified'),
    }


def report(users, keycloak_users, linked, csv_path):
    rows = []
    for user in users:
        kc_user = keycloak_user_for(user, keycloak_users)
        rows.append(row_for(user, kc_user, check(user, kc_user, linked)))
    rows.sort(key=lambda row: (row['status'], row['username']))
    for row in rows:
        print(' | '.join(str(row[field]) for field in FIELDS))
    counts = Counter(row['status'] for row in rows)
    print(f'{len(rows)} unlinked active account(s): ' + ', '.join(f'{key} {counts[key]}' for key in sorted(counts)))
    if csv_path:
        with open(csv_path, 'w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(f'wrote {csv_path}')


def link(usernames, users, keycloak_users, linked, apply):
    by_username = {user.username: user for user in users}
    failed = False
    for username in usernames:
        user = by_username.get(username)
        kc_user = keycloak_user_for(user, keycloak_users) if user else None
        if not user:
            print(f'skipped {username}: not an active account without an sso_id')
        elif not kc_user:
            print(f'skipped {username}: no Keycloak user has this username')
        elif kc_user['id'] in linked:
            print(f"skipped {username}: Keycloak user {kc_user['id']} is linked to another account")
        else:
            if apply:
                updated = UserProfile.objects.filter(id=user.id, sso_id__isnull=True).update(sso_id=kc_user['id'])
                print(f"{'linked' if updated else 'skipped, already linked:'} {username} -> {kc_user['id']} "
                      f"({kc_user.get('email')})")
            else:
                print(f"would link {username} -> {kc_user['id']} ({kc_user.get('email')})")
            linked.add(kc_user['id'])
            continue
        failed = True
    if not apply:
        print('dry run: pass --apply to link')
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--csv', help='also write the report to this CSV file')
    parser.add_argument('--link', nargs='+', metavar='USERNAME', help='link these accounts to their Keycloak user')
    parser.add_argument('--link-all', action='store_true', help='link every email_differs and email_unverified account')
    parser.add_argument('--apply', action='store_true', help='with --link or --link-all, write the links')
    parser.add_argument('--expect', type=int, help='with --link-all --apply, the number of accounts to link')
    args = parser.parse_args()
    if args.link and args.link_all:
        parser.error('pass --link or --link-all, not both')
    if args.apply and not (args.link or args.link_all):
        parser.error('--apply needs --link or --link-all')
    if args.link_all and args.apply and args.expect is None:
        parser.error('--link-all --apply needs --expect')

    keycloak_users = {kc_user['username']: kc_user for kc_user in OpenIDAuthService.get_users()}
    linked = set(UserProfile.objects.filter(sso_id__isnull=False).values_list('sso_id', flat=True))
    users = list(UserProfile.objects.filter(is_active=True, sso_id__isnull=True).order_by('username'))

    usernames = args.link
    if args.link_all:
        usernames = [
            user.username for user in users
            if check(user, keycloak_user_for(user, keycloak_users), linked) in LOCKED_OUT]
        print(f'{len(usernames)} account(s) to link')
        if args.apply and len(usernames) != args.expect:
            sys.exit(f'expected {args.expect}, found {len(usernames)}: nothing linked, run the dry run again')
    if usernames is not None:
        sys.exit(1 if link(usernames, users, keycloak_users, linked, args.apply) else 0)
    report(users, keycloak_users, linked, args.csv)


if __name__ == '__main__':
    main()
