# Hosting The Sun for free

**Read this first: a Discord bot is not a website.** It is a program that stays
connected to Discord 24/7. "Free website" hosts — Vercel, Netlify, GitHub Pages,
Cloudflare Pages — only serve web pages and **cannot** run it. The bot needs a
host that keeps a process running, plus PostgreSQL and Redis (both included in
this repository's Docker stack, so you do not need to buy them separately).

Also useful to know: the bot only makes **outbound** connections (to Discord,
your AI provider, its database). It needs **no public port**, which makes free
hosting much easier.

The two realistic free paths are below. **Option A is the recommendation**: it is
genuinely free forever, never sleeps, and runs the exact stack this repository
ships.

---

## Option A (recommended): Oracle Cloud "Always Free" VM

Oracle's Always Free tier gives you a small VM (up to 4 ARM CPUs / 24 GB RAM)
that runs 24/7 at no cost — no trial expiry, no sleeping. You need a credit or
debit card once to verify your identity (Always Free is not charged).

### 1. Create the VM

1. Sign up at <https://cloud.oracle.com> (choose the "Always Free" resources;
   pick a home region close to you — it cannot be changed later).
2. In the console: **Compute → Instances → Create Instance**.
3. Image: **Ubuntu 24.04**. Shape: **Ampere A1 Flex** — set 2 OCPU and 12 GB RAM
   (or anything within the Always Free allowance).
4. Add your SSH key (or let Oracle generate one and download it).
5. Create. Note the VM's **public IP address**.

### 2. Install Docker on the VM

```bash
ssh ubuntu@<PUBLIC_IP>
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu      # then log out and back in
```

### 3. Get the code onto the VM

The repository is private, so cloning needs a GitHub token: on GitHub go to
**Settings → Developer settings → Fine-grained tokens**, create a token with
read-only access to `the-sun`, then:

```bash
git clone https://<YOUR_GITHUB_TOKEN>@github.com/amandeepintl/the-sun.git
cd the-sun
```

### 4. Configure secrets

```bash
nano .env
```

Fill in at least:

```dotenv
DISCORD_TOKEN=...                 # Discord Developer Portal -> your app -> Bot
AI_PROVIDERS={"openrouter":{"type":"openai_compatible","base_url":"https://openrouter.ai/api/v1","api_key_env":"OPENROUTER_API_KEY","default_model":"deepseek/deepseek-v4-flash-0731:free"}}
DEFAULT_PROVIDER=openrouter
OPENROUTER_API_KEY=...            # openrouter.ai -> Keys

# compose service credentials - invent STRONG new values here (letters/digits only)
POSTGRES_PASSWORD=<strong random password>
REDIS_PASSWORD=<another strong random password>
```

Password gotcha: use **only letters, digits, `-` and `_`** — the password is
embedded in a connection URL, so characters like `@ : / #` break it.

Generate good values with: `openssl rand -hex 16`

### 5. Start it

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f bot    # watch it start, Ctrl+C to stop watching
```

The first start builds the image, applies the database schema, validates the
configuration and connects to Discord. `check-config` runs before the bot
starts, so a mistake in `.env` fails immediately and visibly instead of
crash-looping.

### 6. Verify

```bash
# inside the VM - real checks against database, cache and config:
docker compose -f docker-compose.prod.yml run --rm bot doctor --skip-discord --skip-providers

# health endpoint (bound to 127.0.0.1, not public):
curl http://127.0.0.1:8080/readyz
```

Then in Discord: run `/ping` in any server the bot is in. If you have not
invited it yet, print the install URL from the VM:

```bash
docker compose -f docker-compose.prod.yml run --rm bot invite
```

Open that URL, add the bot to your server, and run `/ping`.

### 7. Updating the bot later

```bash
cd the-sun
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

### Why no ports are open

The bot dials out to Discord; nothing dials in. The database and Redis are not
published on any host port at all, and the health endpoint is bound to
`127.0.0.1` only. In Oracle's console you only need SSH (port 22) open — the
default.

---

## Option B (no server to manage): Koyeb + Neon + Upstash

If you would rather click through a UI than run a VM, split the stack across
three free services. The bot image is the same; only the URLs in `.env` change.

| Piece | Free service | What you take from it |
| --- | --- | --- |
| The bot | [Koyeb](https://www.koyeb.com) free instance | Deploy from this GitHub repo, Dockerfile detected automatically |
| PostgreSQL | [Neon](https://neon.tech) free tier | A `DATABASE_URL` connection string |
| Redis | [Upstash](https://upstash.io) free tier | A `REDIS_URL` (use the `rediss://` TLS URL) |

Setup sketch:

1. **Neon**: create a project → copy the connection string → use it as
   `DATABASE_URL` (append `?ssl=require` if not present).
2. **Upstash**: create a Redis database → copy the `rediss://` URL → use it as
   `REDIS_URL`.
3. **Koyeb**: create a service from this GitHub repository. The `Dockerfile` at
   the root is detected; the start command is `run`. Set the environment
   variables from your `.env` (DISCORD_TOKEN, DATABASE_URL, REDIS_URL,
   AI_PROVIDERS, DEFAULT_PROVIDER, the provider API key, METRICS_ENABLED=true,
   APP_ENV=production, LOG_JSON=true). Health check path: `/healthz`, port 8080.
4. Run the migration once before the first start (or set `RUN_MIGRATIONS=true`
   and let the entrypoint do it): use Koyeb's one-off job/console with
   `python -m the_sun migrate`.

Trade-offs versus Option A: three accounts and dashboards instead of one, free
tiers have small storage/connection limits, and platform free tiers change their
terms more often than Oracle's Always Free allowance. Fine for trying the bot
out; Option A is calmer for the long run.

---

## What the bot needs from you (either option)

| Value | Where you get it |
| --- | --- |
| `DISCORD_TOKEN` | Discord Developer Portal → your application → Bot → Reset Token |
| Message Content intent | Same page, enable only if you want `/summarize` to read other members' messages; then set `DISCORD_MESSAGE_CONTENT_INTENT=true` |
| Invite URL | `python -m the_sun invite` (or `docker compose -f docker-compose.prod.yml run --rm bot invite`) |
| AI provider key | e.g. openrouter.ai → Keys (free models are available there) |

## Security notes

- `.env` is gitignored — real secrets never go into git. The CI pipeline runs
  gitleaks on every push as a backstop.
- The production compose exposes nothing publicly: no database port, no cache
  port, health endpoint on loopback only.
- The container runs as an unprivileged user with no shell tools beyond what the
  entrypoint needs, and `no-new-privileges` is set.
- Rotate `DISCORD_TOKEN` in the portal if it ever leaks, then update `.env` and
  `docker compose -f docker-compose.prod.yml up -d` (no rebuild needed).
