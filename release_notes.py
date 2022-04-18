import os
import re
import subprocess
import sys

SYSTEM_COMMIT_PATTERNS = [
    'Increase maintenance version', 'updated packages', '\[skip ci\]'
]


def show_usage(exit_code=0):
    print('Usage:')
    print("python release_notes.py <from_version> <to_version> <verbose>")
    exit(exit_code)


def throw_error():
    print("Bad args...")
    show_usage(os.EX_USAGE)


def run_shell_cmd(cmd):
    return subprocess.check_output(cmd, shell=True).decode('utf-8')


def system_commit_patterns_grep_statement():
    statement = "--invert-grep"
    for pattern in SYSTEM_COMMIT_PATTERNS:
        statement += " --grep='{}'".format(pattern)
    return statement


def commits_with_issue_number_grep_statement():
    return "--grep='OpenConceptLab/ocl_issues'"


def get_commit_sha_from_message(message):
    return run_shell_cmd("git log --oneline --grep={} --format=format:%H".format(message)).split('\n')[0]


def get_release_date(message):
    return run_shell_cmd("git log --oneline --grep={} --format=format:%ad".format(message))


def get_commits(from_sha, to_sha, verbose=True, remove_system_commits=True):
    commits_list_cmd = "git log {}..{} --pretty=format:'%s'".format(from_sha, to_sha)
    if verbose:
        if remove_system_commits:
            commits_list_cmd += " " + system_commit_patterns_grep_statement()
    else:
        commits_list_cmd += " " + commits_with_issue_number_grep_statement()

    return format_commits(run_shell_cmd(commits_list_cmd).split('\n'))


def get_issue_url(issue_number):
    return "https://github.com/OpenConceptLab/ocl_issues/issues/{}".format(issue_number)


def format_commits(commits):
    issue_number_regex = re.compile('\#\d+')
    result = []
    for commit in commits:
        if commit.startswith('OpenConceptLab/ocl_issues'):
            issue_number = issue_number_regex.search(commit).group()
            if issue_number:
                prefix = 'OpenConceptLab/ocl_issues' + issue_number
                suffix = commit.split(prefix)[1]
                issue_number = issue_number.replace('#', '')
                result.append("[{}]({}){}".format(prefix, get_issue_url(issue_number), suffix))
            else:
                result.append(commit)

        else:
            result.append(commit)
    return result


def format_md(value, heading_level=None):
    if isinstance(value, list):
        value = [v for v in value if v]
        return '- ' + '\n- '.join(value) if value else 'No changelog'

    if heading_level and isinstance(heading_level, int) and 6 >= heading_level >= 1:
        return "#" * heading_level + ' ' + value

    return value


def run():
    try:
        if 'help' in sys.argv:
            show_usage()
            return

        from_message = sys.argv[1]
        to_message = sys.argv[2]
        is_verbose = len(sys.argv) > 3 and sys.argv[3] in [True, 'true', 'True']

        if not from_message or not to_message:
            throw_error()

        commits = get_commits(
            get_commit_sha_from_message(from_message), get_commit_sha_from_message(to_message), is_verbose)
        release_date = get_release_date(to_message)

        print(format_md(value="{} - {}".format(to_message, release_date), heading_level=5))
        print(format_md(value=commits))
    except Exception:
        throw_error()


run()
