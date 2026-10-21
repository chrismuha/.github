"""Send a plain-text push summary without checking out or executing repository code."""
import json
import os
import smtplib
import ssl
import time
from urllib.request import Request, urlopen
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path


def load_commit_files(commit_id, env):
    """GitHub Actions omits file arrays, so read the commit API without checkout."""
    files = []
    for page in range(1, 31):
        url = (env.get('GITHUB_API_URL', 'https://api.github.com')
               + '/repos/' + quote(env['GITHUB_REPOSITORY'], safe='/')
               + '/commits/' + quote(commit_id, safe='')
               + f'?per_page=100&page={page}')
        request = Request(url, headers={
            'Authorization': 'Bearer ' + env['GH_TOKEN'],
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        })
        with urlopen(request, timeout=20) as response:
            batch = json.load(response).get('files', [])
        files.extend(batch)
        if len(batch) < 100:
            break
    return files


def add_file_details(event, env):
    for commit in event.get('commits', [])[:100]:
        try:
            commit['file_details'] = load_commit_files(commit['id'], env)
            if len(commit['file_details']) >= 3000:
                commit['file_note'] = 'GitHub limits this list to 3,000 files; see the commit link for more.'
        except (HTTPError, URLError, TimeoutError, KeyError, ValueError, OSError):
            commit['file_note'] = 'File list unavailable from GitHub; use the commit link to view changes.'


def build_message(event, env):
    sender = env['MAIL_FROM']
    recipients = [address for _, address in getaddresses([env['MAIL_TO']])]
    if not recipients or any('\n' in x or '\r' in x for x in [sender, env['MAIL_TO']]):
        raise ValueError('Invalid notification addresses')
    repo = env['GITHUB_REPOSITORY']
    ref = event.get('ref', env.get('GITHUB_REF', 'manual test'))
    actor = event.get('sender', {}).get('login', env.get('GITHUB_ACTOR', 'unknown'))
    commits = event.get('commits', [])
    subject = f'[{repo}] {len(commits)} commit(s) pushed to {ref}'
    if env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch':
        subject = f'[{repo}] Commit email test'
    message = EmailMessage()
    message['From'] = sender
    message['To'] = ', '.join(recipients)
    message['Subject'] = ' '.join(subject.splitlines())
    lines = [f'Repository: {repo}', f'Ref: {ref}', f'Pushed by: {actor}', '']
    for commit in commits[:100]:
        author = commit.get('author', {}).get('name', 'unknown')
        lines.extend([f"{commit.get('id', '')[:12]} - {author}",
                      commit.get('message', ''), commit.get('url', ''), ''])
        groups = {'Added / New files': [], 'Modified files': [], 'Deleted files': []}
        for file in commit.get('file_details', []):
            status = file.get('status')
            name = file['filename']
            if status == 'renamed':
                name = file.get('previous_filename', name) + ' -> ' + name + ' (renamed)'
            group = ('Added / New files' if status in ('added', 'copied') else
                     'Deleted files' if status == 'removed' else 'Modified files')
            groups[group].append(name)
        for title, names in groups.items():
            if names:
                lines.append(title + ':')
                lines.extend('  - ' + name for name in names)
                lines.append('')
        if commit.get('file_note'):
            lines.extend([commit['file_note'], ''])
    if len(commits) > 100:
        lines.append('Showing the first 100 commits; see the comparison for the full push.')
    if event.get('compare'):
        lines.append('Compare: ' + event['compare'])
    lines.append(f"Run: {env.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{env['GITHUB_RUN_ID']}")
    message.set_content('\n'.join(lines))
    return message


def send_message(message, env):
    """Retry only connection/authentication disconnects, never message submission."""
    for attempt in range(3):
        smtp = None
        try:
            if attempt < 2:
                smtp = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=30,
                                        context=ssl.create_default_context())
            else:
                smtp = smtplib.SMTP('smtp.gmail.com', 587, timeout=30)
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            # Use challenge/response after a dropped initial-response login.
            smtp.login(env['MAIL_FROM'], env['MAIL_PASSWORD'],
                       initial_response_ok=(attempt == 0))
        except (smtplib.SMTPServerDisconnected, TimeoutError, ConnectionError):
            if smtp is not None:
                smtp.close()
            if attempt == 2:
                raise RuntimeError('Gmail disconnected before message submission after 3 attempts; '
                                   'check Google account security and SMTP access. No message was submitted.') from None
            print(f'Gmail disconnected before submission; reconnecting (attempt {attempt + 2}/3).')
            time.sleep(15 * (attempt + 1))
            continue
        except BaseException:
            if smtp is not None:
                smtp.close()
            raise
        try:
            # A disconnect here has uncertain delivery: retrying could duplicate mail.
            smtp.send_message(message)
        finally:
            smtp.close()
        return


def main():
    env = os.environ
    for name in ['MAIL_FROM', 'MAIL_TO', 'MAIL_PASSWORD']:
        if not env.get(name):
            raise RuntimeError(f'Missing required secret: {name}')
    event = json.loads(Path(env['GITHUB_EVENT_PATH']).read_text())
    if env.get('GITHUB_EVENT_NAME') == 'push':
        add_file_details(event, env)
    message = build_message(event, env)
    send_message(message, env)
    print('Commit notification accepted by Gmail SMTP.')


if __name__ == '__main__':
    main()
