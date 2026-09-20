# The Sun

A Discord AI assistant built as a real application: real Discord interactions, a
real AI provider, real PostgreSQL and Redis. Every value it shows a user comes from
an interaction, the database, the cache, the environment or an external API.
Nothing about a specific server, user, channel, model or conversation is written
into the source code - no seeded data, no canned answers, no placeholder IDs.

The bot works in any server it is installed in. Each guild is provisioned its own
settings row the first time it is used and configures itself with `/settings`;
nobody edits the deployment for a new server.

---

## What it does

| Command | What it does |
| --- | --- |
| `/ask prompt [visibility]` | Answers with this channel's conversation history attached |
| `/new` | Closes the active conversation so the next question starts fresh |
| `/history` | Pages through the turns stored for you, with buttons |
| `/summarize count [channel] [focus]` | Summarises consecutive real Discord messages |
| `/explain subject [language]` | Explains code or a concept |
| `/translate text target_language [source_language]` | Translates text you supply |
| `/memory save key value [scope]` | Asks the bot to remember a fact about you |
| `/memory list`, `/memory delete key`, `/memory clear` | Shows, forgets or clears the above |
| `/settings show` | Current server configuration and effective limits |
| `/settings model`, `prompt`, `history`, `memory`, `ephemeral`, `channels`, `admins`, `ratelimit`, `quota`, `retention`, `reset` | Per-server configuration (Manage Server required) |
| `/stats [scope] [window]` | Invocations, tokens, latency and providers, aggregated from real events |
| `/privacy show`, `/privacy export`, `/privacy forget` | See, download or delete everything stored about you |
| `/status` | Live database, cache and provider circuit checks (Manage Server required) |
| `/ping` | Gateway heartbeat and command round-trip latency |
| `/help`, `/invite` | The commands actually published, and this bot's install URL |

Message context menus (right-click any message):

| Menu | What it does |
| --- | --- |
| **Ask The Sun** | Opens a box for an optional instruction and answers about that message |
| **Explain this message** | Explains the selected message |
| **Translate this message** | Translates the selected message |
| **Summarize from here** | Summarises the conversation ending at the selected message |
| **Save to my memories** | Saves text from the selected message as a memory |

---

## Architecture

```
src/the_sun/
  config/          settings, provider parsing, validation   (only reader of os.environ)
  logging_setup.py JSON logs, correlation ids, secret redaction
  errors.py        typed error taxonomy with retry/permission semantics
  db/              declarative models, engine and session lifecycle
  repositories/    one contract per aggregate + SQLAlchemy implementations,
                   unit of work, cursor pagination (only place with SQL)
  cache/           CacheBackend protocol, Redis implementation, key namespacing,
                   Lua scripts, cache-aside helpers (only place importing redis)
  ai/              AIProvider protocol, OpenAI-compatible adapter, registry,
                   token budget, retry policy, Redis circuit breaker
  services/        orchestration: settings, conversations, memory, rate limits,
                   prompt assembly, AI gateway, usage statistics, retention, health
  cogs/            Discord presentation: one module per command group, dynamically
                   discovered, plus message context menus (only place with discord.py)
  observability/   metrics registry (Prometheus text format) and health endpoints
  integrations/    one read-only Discord REST probe used by `doctor`
  bot.py           gateway client, error handling, command sync
```

### Rules the code is held to

1. **Strict dependency direction.** Commands call services; services call
   repositories, cache and providers. Services never import `discord`, and no layer
   reaches upward (`import-linter`, six contracts).
2. **No bypass paths.** Only repositories contain SQL, only the cache package
   imports `redis`, only `config` reads the environment, only `cogs/` and `bot.py`
   import `discord`, and every AI call goes through the `AIProvider` protocol and
   then the gateway (which owns retries, failover and circuit breaking).
3. **Nothing dynamic is hardcoded.** No Discord IDs, user IDs, guild IDs,
   credentials, model names, prices or canned AI text in `src/`. Model catalogues
   are fetched live from the provider's `/models` endpoint; prompts are templates
   parameterised by real input, and the standing instructions are configuration
   (`AI_SYSTEM_PROMPT`, overridable per server with `/settings prompt`).
4. **Real integrations only.** There is no dry-run mode and no fake provider,
   database or cache in the running application. Test doubles live exclusively in
   `tests/`.
5. **Secrets stay in the environment.** `.env` is gitignored, provider configs
   reference keys by variable name, and the logging layer redacts credentials.

Rules 2 and 3 are enforced mechanically: `tests/unit/test_architecture.py` scans the
source tree and `import-linter` contracts in `pyproject.toml` fail the build on a
forbidden import.

### How one request travels

```
Discord interaction
  -> cog (defer, resolve guild settings, apply channel allow-list)
  -> AIService        rate limit (Redis, atomic Lua) -> daily token allowance
                      -> conversation + history, saved memories, system prompt
                      -> PromptBuilder -> token budget
  -> AIGateway        circuit check -> retries with backoff -> fallback providers
  -> AIProvider       OpenAI-compatible HTTP adapter (streaming supported)
  -> AIService        stores both turns, writes a usage event
  -> cog              splits long answers, attaches a provenance footer
```

Every failure between those steps is a typed error that maps to a safe,
actionable message (with a correlation id) and a recorded usage event. No
user-visible text on a failure path is model output.

---

## Requirements

- Python 3.12 or newer (developed and verified on 3.14)
- PostgreSQL 14+ (managed instances are fine; TLS is configured through the URL)
- Redis 7+ (managed instances are fine; `rediss://` is supported)
- A Discord application and bot token
- An AI provider reachable over the OpenAI-compatible `/chat/completions` API - a
  hosted API or a local server such as Ollama or LM Studio

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate        # Windows (Git Bash); .venv/bin/activate on Unix
pip install -e ".[dev]"              # or: pip install -r requirements-dev.txt

cp .env.example .env                 # then fill in real values; .env is gitignored
python -m the_sun check-config        # validates the environment without network access
python -m the_sun migrate             # applies the schema
python -m the_sun doctor              # real checks: Postgres, Redis, providers, Discord
python -m the_sun invite              # prints the install URL for your application
python -m the_sun run                 # starts the bot
```

Enable the **Message Content** intent in the Discord Developer Portal if you want
`/summarize` to read other members' messages; set
`DISCORD_MESSAGE_CONTENT_INTENT=true` to match. Slash commands and the message
context menus work without it, and `/summarize` refuses with an explanation when
the intent is off rather than summarising empty text.

Invite the bot with `python -m the_sun invite`. The requested permissions are the
minimum the commands need (view channel, send messages, embed links, attach files,
read message history, use application commands) - nothing grants moderation.

## Deployment

**[DEPLOY.md](DEPLOY.md) has a step-by-step guide for hosting the bot for free**
(Oracle Cloud Always Free, or Koyeb + Neon + Upstash without a VM).

### Docker Compose (PostgreSQL, Redis and the bot)

```bash
cp .env.example .env      # fill in DISCORD_TOKEN, AI_PROVIDERS, DEFAULT_PROVIDER
docker compose up --build -d
docker compose logs -f bot
docker compose run --rm bot doctor
```

For internet-facing hosts, use `docker-compose.prod.yml` instead: it publishes
no database or cache ports, requires strong service passwords, binds the health
endpoint to loopback, and adds log rotation and memory limits.

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Compose supplies the container-internal `DATABASE_URL` and `REDIS_URL` from its own
Postgres and Redis services; override them with `CONTAINER_DATABASE_URL` and
`CONTAINER_REDIS_URL` to use a managed database or hosted Redis instead. The bot
applies migrations at startup (`RUN_MIGRATIONS=false` to disable) and validates its
configuration first, so a bad deployment fails immediately and visibly.

### Any other container platform

```bash
docker build -t the-sun .
docker run --rm --env-file .env the-sun check-config
docker run --rm --env-file .env the-sun migrate
docker run -d --name the-sun --env-file .env -p 8080:8080 \
  -e METRICS_ENABLED=true -e HEALTH_HOST=0.0.0.0 the-sun run
```

The image runs as an unprivileged user, contains no secrets, and exposes the
optional health endpoint. With `METRICS_ENABLED=true` the process serves:

| Path | Meaning |
| --- | --- |
| `/healthz`, `/livez` | Liveness, no dependency calls |
| `/readyz` | Readiness: a real query against PostgreSQL and a real Redis round trip |
| `/metrics` | Prometheus text format |

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | yes | Bot token from the Discord Developer Portal |
| `DATABASE_URL` | yes | PostgreSQL URL; `postgresql://` is normalised to `postgresql+asyncpg://` |
| `TEST_DATABASE_URL` | no | Separate database for integration tests; falls back to `DATABASE_URL` |
| `REDIS_URL` | yes | `redis://` or `rediss://` connection URL |
| `AI_PROVIDERS` | yes | JSON map of provider name to configuration |
| `DEFAULT_PROVIDER` | yes | Provider used when a guild has not chosen one |
| `AI_FALLBACK_PROVIDERS` | no | Comma-separated providers tried when the chosen one fails |
| `AI_SYSTEM_PROMPT` | no | Standing instructions for the model; overridable per server |
| `AI_MAX_CONTEXT_TOKENS` | no | Context window budget for assembled conversations |
| `AI_MAX_OUTPUT_TOKENS` | no | Cap for one answer |
| `AI_TEMPERATURE` | no | Sampling temperature; unset uses the provider's default |
| `AI_REQUEST_TIMEOUT_SECONDS` | no | Default per-request timeout; a provider's own `timeout_seconds` wins |
| `AI_MAX_RETRIES`, `AI_RETRY_BASE_SECONDS`, `AI_RETRY_MAX_SECONDS` | no | Retry policy (retryable failures only) |
| `AI_CIRCUIT_FAILURE_THRESHOLD`, `AI_CIRCUIT_RESET_SECONDS` | no | Circuit breaker per provider |
| `DEV_GUILD_ID` | no | Sync commands to one guild instantly while developing |
| `DISCORD_MESSAGE_CONTENT_INTENT` | no | Must match the intent setting in the portal |
| `DEFAULT_HISTORY_LENGTH` | no | History window for direct messages |
| `HISTORY_PAGE_SIZE` | no | Turns per page in `/history` |
| `SUMMARY_MAX_MESSAGES` | no | Upper bound for `/summarize` |
| `SUMMARY_MAX_CHARS_PER_MESSAGE` | no | How much of each message is quoted to the model |
| `RATE_LIMIT_ENABLED` | no | Master switch for rate limiting |
| `DEFAULT_RATE_LIMIT_PER_USER_PER_MINUTE`, `DEFAULT_RATE_LIMIT_PER_GUILD_PER_MINUTE` | no | Deployment defaults when a guild sets none |
| `MEMORY_MAX_ENTRIES`, `MEMORY_MAX_VALUE_CHARS` | no | Caps for explicitly saved memories |
| `DEFAULT_HISTORY_RETENTION_DAYS` | no | Purge stored turns after N days; unset means keep until deleted |
| `USAGE_RETENTION_DAYS`, `RETENTION_INTERVAL_HOURS` | no | Usage-event retention and purge cadence (`0` disables) |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_RECYCLE_SECONDS`, `DB_ECHO` | no | Connection pool tuning |
| `REDIS_KEY_PREFIX` | no | Namespace for every Redis key; use a distinct value per environment |
| `REDIS_MAX_CONNECTIONS` | no | Redis connection pool size |
| `METRICS_ENABLED`, `HEALTH_HOST`, `HEALTH_PORT` | no | Optional health and metrics endpoint |
| `LOG_LEVEL`, `LOG_JSON`, `APP_ENV` | no | Logging and environment labelling |

Compose-only variables: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`,
`POSTGRES_PORT`, `REDIS_PORT`, `CONTAINER_DATABASE_URL`, `CONTAINER_REDIS_URL`,
`RUN_MIGRATIONS`, `CHECK_CONFIG_ON_START`.

### Configuring providers

`AI_PROVIDERS` maps a name you choose to its configuration:

```json
{
  "primary": {
    "type": "openai_compatible",
    "base_url": "https://api.example.com/v1",
    "api_key_env": "EXAMPLE_API_KEY",
    "default_model": "your-model-id"
  },
  "local": {
    "type": "openai_compatible",
    "base_url": "http://127.0.0.1:11434/v1",
    "default_model": "your-local-model-id"
  }
}
```

`api_key_env` names another environment variable holding the secret, so the JSON
itself stays free of credentials. A provider without a key is valid - local
runtimes usually need none. Model identifiers are yours to choose; they are never
baked into the code, and `/settings model` autocompletes them from the provider's
live catalogue. Adding a vendor with a different API shape means adding one adapter
module and its type identifier; no service or command changes.

## Command line

| Command | What it does |
| --- | --- |
| `python -m the_sun check-config [--json]` | Validates configuration offline and lists problems |
| `python -m the_sun migrate [--revision head]` | Applies database migrations to the configured database |
| `python -m the_sun doctor [--json] [--skip-discord] [--metrics]` | Runs real dependency checks and reports results |
| `python -m the_sun invite [--application-id ID]` | Prints the install URL, resolved from the real application id |
| `python -m the_sun run` | Starts the bot |

## Testing

```bash
pytest -m unit           # no infrastructure required
pytest -m integration    # requires real Postgres and Redis from your .env
pytest -m live           # requires real Discord and a real provider (opt in)
```

Unit tests need nothing but the source tree; they cover the configuration layer,
the provider adapter and error mapping, the cache key policy, the retry and circuit
logic, the gateway's failover decisions, permissions, prompt templates, the command
surface and the analytics/privacy rendering.

Integration tests run migrations against the configured database, then exercise
repositories, the unit of work, the Redis backend and Lua limiter, the cache-aside
settings path, usage aggregation, **and the full AI request path** - real Postgres,
real Redis, real gateway, real rate limiter, real prompt assembly, with only the
provider's HTTP transport replaced by a double so no external calls are made. If
the database or cache is unreachable the suite fails with an explanatory message
rather than quietly skipping. Test data is written inside a transaction that is
rolled back, so no residue is left behind; point `TEST_DATABASE_URL` at a scratch
database when possible.

Live tests (`SUN_LIVE_TESTS=1 pytest -m live`) make real calls: a real completion
from your configured provider with real token usage, the live model catalogue, a
persisted usage row, and a full health report including the Discord identity check.

`.github/workflows/ci.yml` runs lint, format, mypy, the six architecture contracts,
configuration validation, the rendered migration SQL, the unit suite, and the
integration suite against real PostgreSQL and Redis service containers.

## Privacy and data handling

- Message text is stored only for conversations you started, and only what you sent
  plus what the model answered. `/privacy show` lists exactly what exists and
  `/privacy export` downloads it as JSON.
- `/privacy forget` deletes your conversations, turns, memories, usage records and
  profile row.
- Memories are opt-in: they exist only because someone ran `/memory save`, and they
  are scoped to the server where they were saved or to the member globally.
- Retention is opt-in. A guild sets its own window with `/settings retention`;
  until it does, history is kept until it is deleted explicitly.
- `/summarize` reads channels through Discord's own history endpoint at the moment
  it runs and caches nothing, so edits and deletions are respected.

## Operations

- **Logging.** JSON by default, with a correlation id on every command failure; the
  reply includes that id so a user report can be traced to log lines. Credentials
  are redacted by the logging layer.
- **Metrics.** `sun_ai_requests_total`, `sun_ai_latency_ms`,
  `sun_health_checks_total`, `sun_retention_deleted_total`,
  `sun_rate_limit_degraded_total`.
- **Degradation.** If Redis is unavailable, rate limits fail open and are counted
  rather than blocking members; settings reads fall back to PostgreSQL. If a
  provider is unhealthy its circuit opens and traffic moves to the configured
  fallbacks; if all of them are down the user gets a typed failure, never a
  fabricated answer.
- **Scaling.** The bot is stateless apart from PostgreSQL and Redis, so several
  replicas can run behind a process manager. Rate limiting is atomic in Redis, so
  two replicas cannot both grant the last slot in a window.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `check-config` reports `AI_PROVIDERS is not valid JSON` | The JSON value is quoted or wrapped incorrectly in `.env` |
| `doctor` fails on `provider:*` with 401/403 | The API key variable named by `api_key_env` is missing or wrong |
| `doctor` fails on `database` with `revision not applied` | Run `python -m the_sun migrate` (the container entrypoint does this for you) |
| Connections fail behind a pooler (for example Supabase's transaction pooler) | Use the direct connection string the provider documents for migrations |
| `/summarize` says it cannot read message text | The Message Content intent is off in the portal or `DISCORD_MESSAGE_CONTENT_INTENT=false` |
| Commands do not appear | Global sync can take up to an hour; set `DEV_GUILD_ID` during development |
| `CacheUnavailableError` in logs while commands still work | The cache is unavailable; reads fall back to the database and writes are skipped |

## Project layout

```
alembic.ini, migrations/     schema and the initial migration
docker/entrypoint.sh         optional migrate-then-run container entrypoint
Dockerfile, docker-compose.yml
src/the_sun/                 the application (see Architecture)
tests/unit/                  fast tests, no infrastructure
tests/integration/           real PostgreSQL and Redis
tests/live/                  real Discord and a real provider (opt in)
tests/fakes/                 transport and cache doubles used only by tests
```
