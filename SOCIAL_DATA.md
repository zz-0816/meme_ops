# Social data connections and collection

## What is implemented

`meme_ops` resolves each provider with two complementary paths:

1. Cache first: use a fresh stored snapshot immediately for analysis.
2. Refresh and write back: when a provider snapshot is missing or stale, the
   scheduled collector or the user's analysis request refreshes that provider,
   saves a new snapshot, and updates Social RAG. Stale data is served while a
   background refresh runs; a completely missing snapshot waits only for the
   configured inline timeout.

The shared ranked universe contains at least 100 meme assets. X uses the
official X API. Telegram can use either an explicitly bound Bot community or
the optional official MTProto Client API collector for registered public
community handles.

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
X_RECENT_SEARCH_MAX_RESULTS=10
SOCIAL_X_CACHE_TTL_SECONDS=900
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

### Optional public-community MTProto collector

Register an app at `https://my.telegram.org`, use a dedicated collector
account, and configure:

```env
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_MTPROTO_SESSION=
TELEGRAM_MTPROTO_MAX_MESSAGES=200
TELEGRAM_MTPROTO_ALLOWED_CHATS=authorized_channel_one,authorized_group_two
SOCIAL_TELEGRAM_CACHE_TTL_SECONDS=1800
```

Generate the StringSession once on a trusted local machine:

```text
python scripts/create_telegram_session.py
```

Paste it directly into a sealed Railway variable. Never place it in Git,
screenshots, chat messages, logs, or a committed `.env`. The collector only
uses handles registered in `social_assets.telegram_chat` that also appear in
`TELEGRAM_MTPROTO_ALLOWED_CHATS`. Add a handle only after its administrators
have explicitly authorized this specific, revocable analytics use. The app
stores aggregate metrics and top terms and does not persist raw messages.
CoinGecko community links fill a bounded number of missing source handles per
scheduler run, but discovery alone never authorizes MTProto collection.

Telegram's current Content Licensing terms prohibit broad Telegram data
scraping/aggregation for AI products without the required specific consent.
Therefore this project intentionally does not auto-scrape arbitrary Top-100
communities. For broader coverage, use a provider that contractually licenses
the data for this use case.

## Scheduler modes

## One-time synthetic demo snapshot

For a product demo without X credits, the service can seed deterministic,
clearly-labelled X and Telegram fixtures for the currently ranked Top 10:

```env
DEMO_SOCIAL_DATA_ENABLED=true
DEMO_SOCIAL_DATA_LIMIT=10
SOCIAL_SCHEDULER_ENABLED=false
```

Redeploy once. The startup hook inserts `demo-synthetic-v1` snapshots only
when the asset/provider has no existing snapshot. It never replaces real data
and never refreshes an existing demo row on restart. Reports display
`Synthetic Demo Data - Not Live` and treat the values as low-confidence
proxies. After the seed succeeds, set `DEMO_SOCIAL_DATA_ENABLED=false` and
redeploy; the persisted database rows remain available for the demo.

The equivalent trusted local/console command is:

```text
python scripts/seed_demo_social.py
```

Do not market, export, or describe these fixtures as X/Telegram observations.

### Local or one Railway web replica

Run the collector inside the API process:

```env
SOCIAL_SCHEDULER_ENABLED=true
SOCIAL_SCHEDULER_INTERVAL_SECONDS=900
SOCIAL_ASSET_UNIVERSE_SIZE=100
SOCIAL_COLLECTOR_CONCURRENCY=4
SOCIAL_SOURCE_DISCOVERY_BATCH=5
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
