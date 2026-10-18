# Organization commit email

One shared reusable workflow lives in `chrismuha/.github`. Each active, non-fork
repository has a small `Commit email` trigger. It watches branch and tag pushes,
with no branch filter, and has a manual test button. It never checks out or
executes repository contents. Messages contain commit authors, messages, links,
ref, actor, and the comparison URL. Gmail uses verified TLS on port 465.

## Activation

GitHub Free does not expose organization secrets to private repositories.
Configure repository secrets and variables together using `configure-gmail.py`.
It asks once for a Gmail app password, sends it to GitHub over the authenticated
CLI using stdin, and does not save or print it. It never uses a normal Gmail
password. Existing Gmail app passwords with the same secret name are replaced.

1. Enable Google account two-step verification if needed.
2. Create an app password at https://myaccount.google.com/apppasswords.
3. In a local terminal run `python3 configure-gmail.py --address you@gmail.com` from this folder.
4. Manually dispatch **Commit email** in one repo and confirm the email arrives.

Triggers remain disabled until `COMMIT_EMAIL_ENABLED=true` is set in each repo.
The script sets `GMAIL_APP_PASSWORD`, `COMMIT_EMAIL_FROM`, `COMMIT_EMAIL_TO`, and
then enables each repo. New repos need the caller workflow and setup script run.
To pause one repo set its `COMMIT_EMAIL_ENABLED` variable to `false`.

## GitHub limitations

Pushes made using a workflow's built-in GITHUB_TOKEN do not trigger another push
workflow. Commits with GitHub's skip-CI directives also skip push workflows.
Dependabot-authored pushes are skipped because Actions mail credentials are not
available to those runs; its ordinary PR notifications remain separate. Normal
human pushes and merges into main are covered. Delivery starts after activation;
there is no backfill. SMTP acceptance does not prove inbox delivery. Don't retry
an accepted message automatically: that could send duplicate notifications.

## Validation

Run `python3 -m unittest discover -s . -p 'test_*.py'`.
Tests mock SMTP and do not send email. A live delivery test is still required
once credentials are configured.
