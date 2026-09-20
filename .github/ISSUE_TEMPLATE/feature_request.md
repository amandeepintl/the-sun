---
name: Feature request
about: Suggest an improvement to The Sun
title: ""
labels: enhancement
assignees: ""
---

**Problem to solve**

What can you not do today? Describe the situation, not the solution.

**Proposed behaviour**

What should happen instead?

**Where it belongs**

The project keeps strict layering: cogs call services, services call
repositories/cache/providers, and nothing dynamic is hardcoded. Say which layer
you think owns this and whether it needs:

- a new environment variable in `.env.example` / `config/`
- a schema migration
- a new command or context menu

**Alternatives considered**

**Willingness to contribute**

Are you able to open a pull request for this?
