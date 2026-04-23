# Gmail Outreach Auto-Follow-Up

An always-on cloud service that watches [Outreach](https://www.outreach.io)
mailings, and the moment a prospect has opened one of Manet's emails more
than **2** times, it replies on the original Gmail thread with a meeting
ask for the upcoming Friday at 10am CST. Every Friday morning it also
emails Manet a recap of everyone who got auto-nudged that week.

```
Outreach API (openCount > 2) ──▶ Gmail reply on original thread
                                  │
                                  ▼
                         SQLite dedupe store ──▶ Friday 08:00 CST recap
```

## How it works

1. Every 5 minutes the worker asks the Outreach API for mailings with
   `openCount > 2` updated since the last cursor.
2. For each hot mailing, it searches the Gmail Sent folder for the thread
   that goes to that recipient with that subject.
3. If a thread is found and it hasn't been nudged before, it sends a
   reply with a dynamically-dated Friday ask:
   > "hey love to connect before the end of the week. usually fridays
   > are more free — hows before lunch, maybe Friday May 2 at 10am CST
   > work? Cheers, Manet"
4. The thread id is written to SQLite so each thread gets nudged exactly
   once.
5. Every Friday at 08:00 `America/Chicago`, a recap email is sent to
   Manet listing that week's auto-nudges.

## Files

| File              | Purpose                                                        |
|-------------------|----------------------------------------------------------------|
| `main.py`         | APScheduler entry point; also supports `--once`, `--dry-run`, `--recap-now` |
| `nudger.py`       | Poll-cycle logic: fetch → filter → reply → record              |
| `outreach.py`     | Outreach REST client (OAuth2 refresh, `list_hot_mailings`)     |
| `gmail_client.py` | Gmail API client (`find_thread`, `reply`, `send_self`)         |
| `recap.py`        | Weekly Friday recap email                                      |
| `db.py`           | SQLite schema + queries                                        |
| `config.py`       | Env-driven configuration                                       |

## One-time setup

### 1. Gmail OAuth credentials

1. In [Google Cloud Console](https://console.cloud.google.com/), create a
   project and enable the **Gmail API**.
2. Configure an **OAuth consent screen** (external, testing mode is fine
   for a single user).
3. Under **Credentials**, create an **OAuth client ID** of type
   *Desktop app* and download the JSON as `credentials.json`.
4. On your laptop run `python main.py --once --dry-run` once; it opens a
   browser, you consent to `gmail.send` + `gmail.readonly`, and a
   `token.json` is written next to the code. Copy both
   `credentials.json` and `token.json` to the deployment host (see
   `DB_PATH` / `GMAIL_TOKEN_PATH` in Docker).

Required scopes: `gmail.send`, `gmail.readonly`.

### 2. Outreach OAuth credentials

1. In Outreach admin, go to **Settings → Integrations → Outreach API**
   and create an OAuth application.
2. Set the redirect URI to something you control (e.g.
   `http://localhost:8080/callback`) and request scopes
   `mailings.read` and `prospects.read`.
3. Walk through the OAuth authorization-code flow once to obtain a
   long-lived refresh token. Put the client id, secret, and refresh
   token into `.env` as `OUTREACH_CLIENT_ID`, `OUTREACH_CLIENT_SECRET`,
   `OUTREACH_REFRESH_TOKEN`.

Outreach refresh tokens may rotate on use — the client logs a warning
if a new one is issued so you can update the secret store.

### 3. Configure

```bash
cp .env.example .env
# edit .env and fill in the values
```

## Running

### Local (development)

```bash
pip install .
python main.py --dry-run --once    # prints what it *would* send
python main.py --once              # one real poll cycle
python main.py --recap-now         # force the recap email now
python main.py                     # long-running scheduler
```

### Docker (production)

```bash
docker build -t gmail-outreach .
docker run -d --name gmail-outreach \
    --env-file .env \
    -v "$(pwd)/data:/app/data" \
    gmail-outreach
```

Mount `./data` so `nudges.db` + `token.json` survive container restarts.
Drop `credentials.json` and a pre-minted `token.json` into `./data`
before the first start.

## Verification

1. `python main.py --once --dry-run` — confirms both APIs are reachable
   and prints the exact reply text without sending.
2. Send yourself an Outreach mailing, open it ≥3 times, wait one poll
   cycle, confirm a reply lands on the thread.
3. Open the test email a 4th time, confirm no second reply and
   `SELECT count(*) FROM nudges` is 1.
4. `python main.py --recap-now` — confirms the recap formatting.
5. On a Friday, confirm the reply body proposes *next* Friday (not
   today).
