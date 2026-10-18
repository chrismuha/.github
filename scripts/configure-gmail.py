#!/usr/bin/env python3
"""Run in a terminal. Password is hidden, sent via stdin, and never saved locally."""
import getpass
import argparse
import json
import subprocess

ORG = 'chrismuha'


def gh(*args, value=None):
    result = subprocess.run(['gh', *args], input=value, text=True,
                            capture_output=True, timeout=60)
    if result.returncode:
        raise SystemExit(result.stderr)
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--address', required=True, help='Gmail sender and notification recipient')
    address = parser.parse_args().address
    if '@' not in address or any(c in address for c in '\r\n'):
        raise SystemExit('Invalid email address')
    repos = json.loads(gh('repo', 'list', ORG, '--limit', '1000', '--json',
                          'name,isArchived,isFork'))
    password = getpass.getpass('Gmail app password (hidden): ').replace(' ', '')
    if len(password) != 16 or not password.isalpha():
        raise SystemExit('Expected a 16-letter Gmail app password; no settings changed.')
    for repo in repos:
        if repo['isArchived'] or repo['isFork']:
            continue
        full = ORG + '/' + repo['name']
        # Stop on any error. Enable only after all values are set for this repo.
        gh('secret', 'set', 'GMAIL_APP_PASSWORD', '--repo', full, value=password)
        gh('variable', 'set', 'COMMIT_EMAIL_FROM', '--repo', full, '--body', address)
        gh('variable', 'set', 'COMMIT_EMAIL_TO', '--repo', full, '--body', address)
        gh('variable', 'set', 'COMMIT_EMAIL_ENABLED', '--repo', full, '--body', 'true')
        print(full + ': configured and enabled', flush=True)
    print('Ready. Dispatch Commit email in one repository to verify delivery.')


if __name__ == '__main__':
    main()
