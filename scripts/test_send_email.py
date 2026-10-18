import os
import io
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from send_email import build_message, main, add_file_details, load_commit_files

ENV = {'MAIL_FROM': 'sender@example.com', 'MAIL_TO': 'recipient@example.com',
       'MAIL_PASSWORD': 'test-only', 'GITHUB_REPOSITORY': 'example/repo',
       'GITHUB_RUN_ID': '123', 'GITHUB_EVENT_NAME': 'push'}


class EmailTests(unittest.TestCase):
    def test_summary_keeps_untrusted_commit_text_as_plain_text(self):
        event = {'ref': 'refs/heads/main', 'sender': {'login': 'human'},
                 'commits': [{'id': 'abcdef0123456789', 'author': {'name': 'A'},
                              'message': 'Fix $HOME `command`\n<script>text</script>'}],
                 'compare': 'https://github.com/example/repo/compare/a...b'}
        message = build_message(event, ENV)
        self.assertIn('abcdef012345 - A', message.get_content())
        self.assertIn('Fix $HOME `command`', message.get_content())
        self.assertEqual(message.get_content_type(), 'text/plain')
        self.assertIn('human', message.get_content())

    def test_file_groups_and_renames(self):
        event = {'commits': [{'message': 'Update app', 'file_details': [
            {'filename': 'new.txt', 'status': 'added'},
            {'filename': 'app.py', 'status': 'modified'},
            {'filename': 'old.txt', 'status': 'removed'},
            {'filename': 'renamed.txt', 'previous_filename': 'before.txt', 'status': 'renamed'},
        ]}]}
        body = build_message(event, ENV).get_content()
        self.assertIn('Added / New files:\n  - new.txt', body)
        self.assertIn('Modified files:\n  - app.py', body)
        self.assertIn('Deleted files:\n  - old.txt', body)
        self.assertIn('before.txt -> renamed.txt (renamed)', body)

    def test_api_pagination_and_read_token(self):
        first = [{'filename': str(i), 'status': 'modified'} for i in range(100)]
        replies = [io.StringIO(json.dumps({'files': first})),
                   io.StringIO(json.dumps({'files': [{'filename': 'last', 'status': 'added'}]}))]
        with patch('send_email.urlopen', side_effect=replies) as request:
            files = load_commit_files('abcdef', dict(ENV, GH_TOKEN='test-token'))
        self.assertEqual(len(files), 101)
        self.assertIn('page=2', request.call_args.args[0].full_url)
        self.assertEqual(request.call_args.args[0].get_header('Authorization'), 'Bearer test-token')

    def test_installation_tokens_are_forwarded_without_length_or_format_assumptions(self):
        tokens = ['ghs_' + 'a' * 36,
                  'ghs_12345_' + 'a' * 170 + '.' + 'b' * 170 + '.' + 'c' * 168,
                  'ghs_' + 'x' * 1020]
        self.assertEqual(len(tokens[1]), 520)
        for token in tokens:
            with self.subTest(length=len(token)):
                with patch('send_email.urlopen', return_value=io.StringIO('{"files": []}')) as request:
                    load_commit_files('abcdef', dict(ENV, GH_TOKEN=token))
                self.assertEqual(request.call_args.args[0].get_header('Authorization'), 'Bearer ' + token)

    def test_api_failure_keeps_commit_message_and_explains_missing_files(self):
        from urllib.error import URLError
        event = {'commits': [{'id': 'abc', 'message': 'Keep this message'}]}
        with patch('send_email.load_commit_files', side_effect=URLError('unavailable')):
            add_file_details(event, ENV)
        body = build_message(event, ENV).get_content()
        self.assertIn('Keep this message', body)
        self.assertIn('File list unavailable', body)

    def test_header_injection_rejected(self):
        with self.assertRaises(ValueError):
            build_message({}, dict(ENV, MAIL_TO='a@example.com\nBcc: other@example.com'))

    def test_manual_test(self):
        self.assertIn('email test', build_message({}, dict(ENV, GITHUB_EVENT_NAME='workflow_dispatch'))['Subject'])

    def test_send_uses_tls_and_configured_recipient(self):
        with tempfile.NamedTemporaryFile(mode='w') as event:
            event.write('{}'); event.flush()
            smtp = MagicMock()
            with patch.dict(os.environ, dict(ENV, GITHUB_EVENT_PATH=event.name)), patch('send_email.smtplib.SMTP_SSL', return_value=smtp) as connect:
                main()
            self.assertEqual(connect.call_args.args, ('smtp.gmail.com', 465))
            smtp.__enter__.return_value.login.assert_called_once_with('sender@example.com', 'test-only')
            message = smtp.__enter__.return_value.send_message.call_args.args[0]
            self.assertEqual(message['To'], 'recipient@example.com')

    def test_missing_credentials_does_not_connect(self):
        with patch.dict(os.environ, {}, clear=True), patch('send_email.smtplib.SMTP_SSL') as connect:
            with self.assertRaisesRegex(RuntimeError, 'Missing required secret'):
                main()
            connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
