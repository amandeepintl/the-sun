# Security Policy

## Reporting a vulnerability

Do **not** open a public issue for a security problem.

Use GitHub's private vulnerability reporting on this repository
(**Security → Report a vulnerability**), or contact the maintainer directly.
Include a description, reproduction steps and any logs with secrets redacted.

You can expect an initial response within a few days. Please allow a reasonable
window for a fix before any public disclosure.

## What matters most here

This bot handles credentials (Discord token, provider API keys) and member
conversation data. The highest-value findings involve:

- Secret disclosure through logs, errors, metrics or the repository itself.
  The logging layer redacts credentials; `config/` is the only reader of
  `os.environ`; provider configs reference API keys by *variable name*.
- Any bypass of the permission model: `/settings` requires **Manage Server**,
  `/status` likewise, and memories/privacy commands must never leak across
  members or guilds.
- The privacy guarantees: `/privacy export` shows everything stored about a
  member, `/privacy forget` deletes all of it. A bug that leaves rows behind
  is a security issue.
- Prompt or command surfaces that let one guild read or write another's data.

## Scope

- Code under `src/`, `migrations/`, `docker/` and `tests/`.
- The Docker image and compose stack.

Out of scope: your own deployment's infrastructure, and vulnerabilities in
dependencies that have public fixes (please report those upstream and open a
normal issue to bump the pin).

## Supported versions

Security fixes apply to the latest commit on the default branch. Pin a commit
hash if you need a stable deployment.
