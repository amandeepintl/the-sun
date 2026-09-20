# Hosting The Sun for free

**Read this first: a Discord bot is not a website.** It is a program that stays
connected to Discord 24/7. "Free website" hosts — Vercel, Netlify, GitHub Pages,
Cloudflare Pages — only serve web pages and **cannot** run it. The bot needs a
host that keeps a process running, plus PostgreSQL and Redis.

Good news: the bot only makes **outbound** connections (to Discord, your AI
provider, its database). It needs **no public inbound port**, which is what makes
small free tiers viable.

## Option A (no credit card needed): Koyeb + Neon + Upstash

All three have free tiers that **require no credit card and never expire**.
The bot runs on Koyeb; the database lives on Neon; the cache lives on Upstash.

| Piece | Free service | Free tier gives you |
| --- | --- | --- |
| The bot | [koyeb.com](https://www.koyeb.com) | One always-on instance: 512 MB RAM, deploys straight from this GitHub repo |
| PostgreSQL | [neon.com](https://neon.com) | 512 MB storage, no time limit, no card |
| Redis | [upstash.com](https://upstash.com) | ~256 MB, 500k commands/day, no card |

That is enough for a personal or small-server bot.

### 1. Neon — the database

1. Sign up (GitHub login works), create a **project**, choose a region near you.
2. Copy the pooled connection string. It looks like
   `postgresql://user:pass@ep-xxx-pooler.region.aws.neon.tech/dbname?sslmode=require`.
3. **One required edit:** replace `?sslmode=require` with `?ssl=require` — the
   bot's PostgreSQL driver understands the latter (verified; `sslmode` is not
   accepted). This becomes your `DATABASE_URL`.

### 2. Upstash — the cache

1. Sign up, create a **Redis database** (any region; pick the same area as Neon).
2. Copy the `rediss://` connection URL (the TLS one). This becomes your
   `REDIS_URL`.

### 3. Koyeb — the bot

1. Sign up (GitHub login works), connect your GitHub account, and grant Koyeb
   access to the private `the-sun` repository when asked.
2. Create a **Web Service** from the repository. Koyeb detects the root
   `Dockerfile` automatically; keep that.
3. Set the **port** to `8080` and the **health check path** to `/healthz`.
4. Add these **environment variables** (Koyeb → Service → Settings → Variables):

| Variable | Value |
| --- | --- |
| `DISCORD_TOKEN` | your bot token (Discord Developer Portal → your app → Bot) |
| `DATABASE_URL` | the Neon URL from step 1 (with `?ssl=require`) |
| `REDIS_URL` | the Upstash `rediss://` URL from step 2 |
| `AI_PROVIDERS` | `{"openrouter":{"type":"openai_compatible","base_url":"https://openrouter.ai/api/v1","api_key_env":"OPENROUTER_API_KEY","default_model":"deepseek/deepseek-v4-flash-0731:free"}}` |
| `DEFAULT_PROVIDER` | `openrouter` |
| `OPENROUTER_API_KEY` | your key from openrouter.ai → Keys |
| `METRICS_ENABLED` | `true` |
| `HEALTH_HOST` | `0.0.0.0` |
| `HEALTH_PORT` | `8080` |
| `APP_ENV` | `production` |
| `LOG_JSON` | `true` |

   The container entrypoint applies the database schema on first start
   (`RUN_MIGRATIONS` defaults to true) and validates the configuration before the
   bot connects, so a wrong variable fails the deploy immediately and visibly
   instead of crash-looping.

5. Deploy. Watch the build logs — you should see the entrypoint's
   "applying database migrations" and "validating configuration" lines, then the
   bot connect.

### 4. Invite and verify

```text
# after a successful deploy, run once from Koyeb's console for the service:
python -m the_sun invite
```

Open the printed URL, add the bot to your server, then run `/ping` in Discord.
If something is wrong, Koyeb's **Logs** tab shows the exact failure (and the
bot's own error messages are designed to be readable there).

### 5. Updating the bot

Push to `main` as usual, then in Koyeb click **Deploy** (or enable auto-deploy
on push). The schema migrations run automatically at startup.

### Honest limits of the free tiers

- **Neon** pauses an idle database after a few minutes; the first command after
  a quiet period wakes it (~1 second slower, then normal).
- **Upstash** free allows ~500k commands/day — far more than a small server
  generates.
- **Koyeb's** 512 MB instance comfortably fits this bot, but it is one shared
  CPU — fine for a Discord bot, not for batch jobs.
- Free-tier terms can change; if one service tightens, the other two stay put
  and only that URL needs replacing.

---

## Option B (needs a card for identity verification): Oracle Cloud Always Free

Oracle's Always Free tier gives a small VM (up to 4 ARM CPUs / 24 GB RAM) that
runs 24/7 forever at no cost. A card is required once to verify identity (never
charged), which is why it is not the default here. If you get access to a card,
the full walkthrough is in the git history of this file (`git log -- DEPLOY.md`)
— the short version: create an Ubuntu VM, `curl -fsSL https://get.docker.com | sh`,
clone the repo, fill `.env`, and
`docker compose -f docker-compose.prod.yml up -d --build` (Postgres and Redis
run alongside the bot on the VM, nothing else to sign up for).

---

## What the bot needs from you (either option)

| Value | Where you get it |
| --- | --- |
| `DISCORD_TOKEN` | Discord Developer Portal → your application → Bot → Reset Token |
| Message Content intent | Same page, enable only if you want `/summarize` to read other members' messages; then set `DISCORD_MESSAGE_CONTENT_INTENT=true` |
| Invite URL | `python -m the_sun invite` |
| AI provider key | e.g. openrouter.ai → Keys (free models are available there) |

## Security notes

- Secrets live only in each platform's environment variables — never in git.
  CI runs gitleaks on every push as a backstop.
- Rotate `DISCORD_TOKEN` in the portal if it ever leaks, then update the
  environment variable and redeploy.
- The container runs as an unprivileged user with no shell tools beyond what the
  entrypoint needs, and `no-new-privileges` is set.
