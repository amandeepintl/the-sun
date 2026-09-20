---
name: Bug report
about: Report something broken in The Sun
title: ""
labels: bug
assignees: ""
---

**What happened?**

A clear description of the failure.

**Which command or flow?**

For example `/summarize`, `/settings ratelimit`, a context menu, or a CLI command
(`doctor`, `migrate`).

**What did you expect?**

**Logs**

Paste the relevant lines. JSON log lines include a correlation id - include it.
Redact tokens and keys; the logging layer should already have done it.

```
paste logs here
```

**Environment**

- Deployment: Docker Compose / container / bare process
- `APP_ENV`: 
- Python version (`python --version`):
- Any unusual provider or database setup (pooler, TLS, managed Redis):

**Additional context**

Anything else that helps: output of `python -m the_sun doctor --json`
(redacted), the setting values involved, what changed just before this started.
