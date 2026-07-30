# Social data connections and collection

## What is implemented

`meme_ops` has two complementary paths:

1. A shared ranked universe of at least 100 meme assets. A scheduled collector
   refreshes market priority and collects X signals through the official X API.
2. Wallet-bound fallback connections. A user connects X with OAuth 2.0 PKCE
   and Telegram with Telegram Login. Assets outside the shared universe can
   then be collected on demand with that wallet's authorization.

Raw numeric snapshots are stored in SQLite first. Short attributable hourly
summaries are stored in `social_rag_documents` and supplied to the report
Agent. A missing metric is reported as `not connected`, never as a verified
zero.

X HTML/session scraping is intentionally not implemented. X collection uses
the supported API because storing browser cookies or automating an X login is
unsafe and can violate platform rules. Telegram group metrics use the Bot API
after the user explicitly adds and binds the bot.

## Required encryption

Generate one durable Fernet key:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the result as:

```env
SOCIAL_TOKEN_ENCRYPTION_KEY=<generated-value>
```

Keep this value in Railway Variables or a local `.env`; never commit it. If it
is replaced, existing encrypted provider tokens cannot be read and users must
reconnect.

## X setup

Create an X developer application with OAuth 2.0 Authorization Code + PKCE.
Set its callback URI to:

```text
https://YOUR_DOMAIN/api/social/x/callback
```

Configure:

```env
APP_PUBLIC_URL=https://YOUR_DOMAIN
X_CLIENT_ID=
X_CLIENT_SECRET=
X_OAUTH_SCOPES=tweet.read users.read follows.read offline.access
```

For shared Top-100 collection, also configure an app-level read token:

```env
X_BEARER_TOKEN=
```

Without `X_BEARER_TOKEN`, shared asset ranking still updates, while a connected
user's OAuth token is used only for that wallet's on-demand asset collection.

## Telegram setup

Create a bot with BotFather, configure the production domain for Telegram
Login, and set:

```env
TELEGRAM_BOT_USERNAME=
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=<long-random-value>
TELEGRAM_AUTO_SET_WEBHOOK=true
APP_PUBLIC_URL=https://YOUR_DOMAIN
```

Automatic webhook setup requires HTTPS. For local HTTP development, keep
`TELEGRAM_AUTO_SET_WEBHOOK=false`; Telegram Login can be tested through an
HTTPS tunnel, and webhook updates can be forwarded to:

```text
POST /api/social/telegram/webhook
X-Telegram-Bot-Api-Secret-Token: <TELEGRAM_WEBHOOK_SECRET>
```

After Telegram identity login, the UI creates a 15-minute group link code.
Add the bot to a group/channel and send:

```text
/connect CODE
```

Only the Telegram user connected to the current wallet can consume that code,
and the Bot API must confirm that user is a group/channel administrator.

## Scheduler modes

### Local or one Railway web replica

Run the collector inside the API process:

```env
SOCIAL_SCHEDULER_ENABLED=true
SOCIAL_SCHEDULER_INTERVAL_SECONDS=900
SOCIAL_ASSET_UNIVERSE_SIZE=100
SOCIAL_COLLECTOR_CONCURRENCY=4
```

The default priority is:

- ranks 1-20: eligible every 15 minutes, including small recent-post samples;
- ranks 21-100: eligible every 60 minutes;
- lower-priority/on-demand assets: user-triggered or six-hour refresh.

### Railway Cron or a separate worker

Keep the web process scheduler disabled and run:

```text
python scripts/social_collector.py
```

from one Railway Cron service. Both services must use the same persistent
database. With SQLite, run one web replica and one non-overlapping collector
job. Before horizontal scaling, migrate the social tables to PostgreSQL and
add a distributed job lock.

## Local persistence without a cloud server

The same implementation works locally:

- structured metrics and encrypted connections: `data/meme_ops.db`;
- Social RAG summaries: `social_rag_documents` in the same SQLite file;
- update mechanism: embedded scheduler or `scripts/social_collector.py`.

It updates only while the local machine and backend are running. For 24/7
collection, run the collector on Railway/a VPS and mount persistent storage.

## Privacy boundary

- OAuth tokens are encrypted at rest and never returned to the frontend.
- Connections, Telegram communities, OAuth state, and private RAG retrieval are
  isolated by normalized wallet address.
- Shared app-token snapshots have no owner and may be reused by all reports.
- Wallet OAuth and Telegram-group snapshots carry `owner_address`; only that
  wallet's Agent can retrieve them.
- Same-symbol assets are keyed by `chain:contract`; they are never merged
  merely because their symbols match.
