import os
import io
import json
import tempfile
import smtplib
import unittest
from unittest.mock import patch, MagicMock
from send_email import build_message, main, add_file_details, load_commit_files, send_message

ENV = {'MAIL_FROM': 'sender@example.com', 'MAIL_TO': 'recipient@example.com',
       'MAIL_PASSWORD': 'test-only', 'GITHUB_REPOSITORY': 'example/repo',
       'GITHUB_RUN_ID': '123', 'GITHUB_EVENT_NAME': 'push'}


class EmailTests(unittest.TestCase):
    def test_reusable_workflow_embeds_tested_sender_exactly(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        workflow_path = root / '.github/workflows/commit-email-reusable.yml'
        sender_path = root / 'scripts/send_email.py'
        if not workflow_path.exists():  # Unversioned suite mirror.
            workflow_path = Path(__file__).with_name('commit-email-reusable.yml')
            sender_path = Path(__file__).with_name('send_email.py')
        workflow = workflow_path.read_text()
        embedded = workflow.split('        run: |\n', 1)[1]
        self.assertEqual(embedded, ''.join(('          ' + line).rstrip() + '\n' for line in
                         sender_path.read_text().splitlines()))

    def test_summary_keeps_untrusted_commit_text_as_plain_text(self):
        event = {'ref': 'refs/heads/main', 'sender': {'login': 'human'},
                 'commits': [{'id': 'abcdef0123456789', 'author': {'name': 'A'},
                              'message': 'Fix $HOME `command`\n<script>text</script>'}],
                 'compare': 'https://github.com/example/repo/compare/a...b'}
        message = build_message(event, ENV)
        self.assertIn('abcdef012345 - A', message.get_body(preferencelist=('plain',)).get_content())
        self.assertIn('Fix $HOME `command`', message.get_body(preferencelist=('plain',)).get_content())
        self.assertEqual(message.get_content_type(), 'multipart/alternative')
        self.assertIn('human', message.get_body(preferencelist=('plain',)).get_content())

    def test_accessible_html_escapes_commit_text_and_bolds_filenames(self):
        event = {'ref': 'refs/heads/<main>', 'commits': [{
            'id': 'abcdef012345', 'author': {'name': 'A & B'},
            'message': 'Fix <script>alert(1)</script>\nSecond line',
            'url': 'https://github.com/example/repo/commit/abcdef',
            'file_details': [{'filename': 'src/<unsafe>&.py', 'status': 'modified'}],
        }]}
        message = build_message(event, ENV)
        html = message.get_body(preferencelist=('html',)).get_content()
        self.assertIn('font-size:22px', html)
        self.assertIn('font-size:34px', html)
        self.assertIn('<h2 ', html)
        self.assertIn('Modified files</h3>', html)
        self.assertIn('<strong>src/&lt;unsafe&gt;&amp;.py</strong>', html)
        self.assertIn('A &amp; B', html)
        self.assertNotIn('<script>', html)
        self.assertIn('View this commit on GitHub', html)
        self.assertIn('overflow-wrap:anywhere', html)
        self.assertIn('Fix <script>', message.get_body(preferencelist=('plain',)).get_content())
        self.assertEqual(len(list(message.iter_parts())), 2)

    def test_unsafe_urls_are_not_clickable_in_html(self):
        message = build_message({'commits': [{'url': 'javascript:alert(1)'}],
                                 'compare': 'data:text/html,unsafe'}, ENV)
        html = message.get_body(preferencelist=('html',)).get_content()
        self.assertNotIn('javascript:', html)
        self.assertNotIn('data:text/html', html)
        self.assertIn('View this commit on GitHub', html)

    def test_file_groups_and_renames(self):
        event = {'commits': [{'message': 'Update app', 'file_details': [
            {'filename': 'new.txt', 'status': 'added'},
            {'filename': 'app.py', 'status': 'modified'},
            {'filename': 'old.txt', 'status': 'removed'},
            {'filename': 'renamed.txt', 'previous_filename': 'before.txt', 'status': 'renamed'},
        ]}]}
        body = build_message(event, ENV).get_body(preferencelist=('plain',)).get_content()
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
        body = build_message(event, ENV).get_body(preferencelist=('plain',)).get_content()
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
            smtp.login.assert_called_once_with('sender@example.com', 'test-only', initial_response_ok=True)
            message = smtp.send_message.call_args.args[0]
            self.assertEqual(message['To'], 'recipient@example.com')

    def test_login_disconnect_reconnects_before_single_submission(self):
        first, second = MagicMock(), MagicMock()
        first.login.side_effect = smtplib.SMTPServerDisconnected('closed')
        with patch('send_email.smtplib.SMTP_SSL', side_effect=[first, second]), patch('send_email.time.sleep'):
            send_message(build_message({}, ENV), ENV)
        first.send_message.assert_not_called()
        first.close.assert_called_once()
        second.login.assert_called_once_with(ENV['MAIL_FROM'], ENV['MAIL_PASSWORD'], initial_response_ok=False)
        second.send_message.assert_called_once()

    def test_persistent_disconnect_is_bounded(self):
        with patch('send_email.smtplib.SMTP_SSL', side_effect=smtplib.SMTPServerDisconnected('closed')) as connect, patch('send_email.smtplib.SMTP', side_effect=smtplib.SMTPServerDisconnected('closed')) as starttls_connect, patch('send_email.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'No message was submitted'):
                send_message(build_message({}, ENV), ENV)
        self.assertEqual(connect.call_count, 2)
        starttls_connect.assert_called_once()

    def test_final_attempt_upgrades_to_tls_before_authentication(self):
        smtp = MagicMock()
        calls = []
        smtp.starttls.side_effect = lambda **kwargs: calls.append('tls')
        smtp.login.side_effect = lambda *args, **kwargs: calls.append('login')
        with patch('send_email.smtplib.SMTP_SSL', side_effect=smtplib.SMTPServerDisconnected('closed')), patch('send_email.smtplib.SMTP', return_value=smtp) as connect, patch('send_email.time.sleep'):
            send_message(build_message({}, ENV), ENV)
        connect.assert_called_once_with('smtp.gmail.com', 587, timeout=30)
        self.assertEqual(calls, ['tls', 'login'])
        smtp.send_message.assert_called_once()

    def test_tls_failure_does_not_authenticate_or_send(self):
        import ssl
        smtp = MagicMock()
        smtp.starttls.side_effect = ssl.SSLCertVerificationError('untrusted')
        with patch('send_email.smtplib.SMTP_SSL', side_effect=smtplib.SMTPServerDisconnected('closed')), patch('send_email.smtplib.SMTP', return_value=smtp), patch('send_email.time.sleep'):
            with self.assertRaises(ssl.SSLCertVerificationError):
                send_message(build_message({}, ENV), ENV)
        smtp.login.assert_not_called()
        smtp.send_message.assert_not_called()
        smtp.close.assert_called_once()

    def test_rejected_credentials_are_not_retried(self):
        smtp = MagicMock()
        smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b'rejected')
        with patch('send_email.smtplib.SMTP_SSL', return_value=smtp) as connect:
            with self.assertRaises(smtplib.SMTPAuthenticationError):
                send_message(build_message({}, ENV), ENV)
        connect.assert_called_once()
        smtp.send_message.assert_not_called()
        smtp.close.assert_called_once()

    def test_submission_disconnect_is_never_retried(self):
        smtp = MagicMock()
        smtp.send_message.side_effect = smtplib.SMTPServerDisconnected('delivery uncertain')
        with patch('send_email.smtplib.SMTP_SSL', return_value=smtp) as connect:
            with self.assertRaises(smtplib.SMTPServerDisconnected):
                send_message(build_message({}, ENV), ENV)
        connect.assert_called_once()
        smtp.send_message.assert_called_once()
        smtp.close.assert_called_once()

    def test_missing_credentials_does_not_connect(self):
        with patch.dict(os.environ, {}, clear=True), patch('send_email.smtplib.SMTP_SSL') as connect:
            with self.assertRaisesRegex(RuntimeError, 'Missing required secret'):
                main()
            connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
