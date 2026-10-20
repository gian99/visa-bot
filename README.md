# visa-bot

A small bot that watches the US visa appointment calendar for the **Lima, Peru** consulate
on [ais.usvisa-info.com](https://ais.usvisa-info.com) and posts to **Slack** as soon as a date
**earlier than your current appointment** opens up.

- **Notify only.** It never reschedules, books or changes anything in your account.
- **Runs for free on GitHub Actions.** You don't need to keep a computer on.
- **Gentle on the site.** It checks every ~10 minutes, keeps one session open for hours, and backs off when it looks rate-limited.
- **Knows your current date.** It reads your current appointment from the portal at every login, so if you manage to move it earlier, the bot adjusts on its own.

---

## Table of contents

- [How it works](#how-it-works)
- [Slack messages](#slack-messages)
- [Setup](#setup)
  - [1. Create a Slack webhook](#1-create-a-slack-webhook)
  - [2. Add secrets and variables to GitHub](#2-add-secrets-and-variables-to-github)
  - [3. Test it](#3-test-it)
- [Running and controlling the bot](#running-and-controlling-the-bot)
- [Configuration reference](#configuration-reference)
- [Running locally](#running-locally)
- [Rate limiting and safety](#rate-limiting-and-safety)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Security notes](#security-notes)
- [Project structure](#project-structure)
- [Disclaimer](#disclaimer)

---

## How it works

```
GitHub Actions (cron, every 6h)
        │
        ▼
   bot.py starts a headless Chromium (Playwright)
        │
        ├─ 1. Logs in to ais.usvisa-info.com
        ├─ 2. Reads your current appointment from the "groups" page
        ├─ 3. Opens the reschedule page and gets the consulate id (Lima = 115)
        │
        └─ loop (~5h35m) ──────────────────────────────────────────┐
             ├─ GET /schedule/<id>/appointment/days/<facility>.json │
             ├─ any date earlier than your appointment?             │
             │     yes → Slack alert ✅🚨                           │
             └─ sleep ~10 min (randomized 8–14 min) ────────────────┘
```

- The available dates come from the same JSON endpoint the portal's date picker uses,
  so the bot sees exactly what you would see in the "Fecha de la cita" calendar.
- Each GitHub Actions run keeps **one logged-in session** for its whole duration
  instead of logging in on every check. Frequent logins are what most often triggers blocks.
- If the session expires or a request fails, the bot logs in again with a clean session.
- You get an alert for an earlier date once. If an even earlier date shows up later in
  the same run, you get a new alert for that one too.

### Schedule

| Run starts (UTC) | Lima time | Notes |
|---|---|---|
| 00:00 | 19:00 | |
| 06:00 | 01:00 | |
| 12:00 | 07:00 | Also sends the **daily summary** to Slack |
| 18:00 | 13:00 | |

Each run checks for about **5h35m**, then exits so the next scheduled run can start.
GitHub may start scheduled runs a few minutes late.

---

## Slack messages

| When | Message |
|---|---|
| An earlier date is found | ✅🚨 **Appointment found! Earlier date available in Lima**, with the dates, your current appointment and a **Reschedule now** link. If you're logged out, the portal asks you to log in first (dismiss the "You need to sign in" popup) and then opens the reschedule page directly |
| Every day at ~07:00 Lima | 🔴 **No dates before your appointment**, with the earliest available date (shows the bot is alive) |
| Manual `once` run | 🔴 / ✅ with the result of that check |
| Consulate shows no dates at all | 🟡 **Visa bot running**, but the consulate shows no open dates right now |
| 3 failed checks in a row, or a failed manual run | ⚠️ with the error |
| Site looks rate-limited (3 empty responses in a row) | 🟡 **Pausing 3 hours** |
| The run stops itself (see [Automatic stops](#automatic-stops)) | 🛑 **Visa bot stopped**, with the reason and a link to the run |
| Manual `test` run | ✅ **Visa bot connected** |

> **Tip:** In Slack, open the channel → click its name → **Notifications** → **All new messages**,
> so alerts reach your phone. By default Slack only notifies you on mentions.

---

## Setup

### 1. Create a Slack webhook

1. Go to <https://api.slack.com/apps> → **Create New App** → **Blank app**
   (older UIs call it *From scratch*).
2. Name it `Visa Bot` and pick your workspace.
3. If asked for **scopes**, only add `incoming-webhook`. If asked for **events**, leave them empty.
4. In the left menu open **Incoming Webhooks** and turn it **On**.
5. Click **Add New Webhook**, choose a channel (for example a private `#visa-alerts`) and click **Allow**.
6. Copy the URL. It looks like `https://hooks.slack.com/services/T.../B.../...`.

You only need the **webhook URL**. The bot doesn't use the App ID, Client ID, Client Secret,
bot token or app token, so don't store them anywhere.

### 2. Add secrets and variables to GitHub

In the repository go to **Settings → Secrets and variables → Actions**.

**Secrets** tab (encrypted, never shown in logs):

| Name | Value |
|---|---|
| `VISA_EMAIL` | Your ais.usvisa-info.com login email |
| `VISA_PASSWORD` | Your ais.usvisa-info.com password |
| `SLACK_WEBHOOK_URL` | The webhook URL from step 1 |

**Variables** tab:

| Name | Example | Required | Description |
|---|---|---|---|
| `SCHEDULE_ID` | `12345678` | yes | The number in your reschedule URL: `.../niv/schedule/<SCHEDULE_ID>/appointment` |
| `COUNTRY_CODE` | `es-pe` | no | Portal locale/country prefix. Default `es-pe` (Peru, Spanish) |
| `CHECK_INTERVAL_MINUTES` | `10` | no | Minutes between checks (randomized ×0.8–1.4). Default `10` |
| `CURRENT_APPOINTMENT_DATE` | `2027-04-13` | no | Fallback only, used if the bot can't read your appointment from the portal |

### 3. Test it

Go to **Actions → visa-bot → Run workflow** and pick a mode:

1. `test` sends a "connected" message to Slack. This checks the webhook only.
2. `once` does one real check (login → read appointment → fetch dates) and posts the result to Slack.

If both work, you're done. Scheduled runs start automatically.

---

## Running and controlling the bot

Workflow page: **Actions → visa-bot** (`.github/workflows/visa-bot.yml`).

| I want to… | How |
|---|---|
| Start watching now | **Run workflow** → `loop` |
| Do a single check | **Run workflow** → `once` |
| Test Slack | **Run workflow** → `test` |
| Stop the current run | Open the running job → **Cancel workflow** |
| Pause everything, including scheduled runs | Workflow page → **⋯** → **Disable workflow** |
| Resume | Workflow page → **Enable workflow** |
| See what it found | Open a run → job **check** → step **Run bot** |

All of this works from the **GitHub Mobile** app (iOS/Android): repository → **Actions**.

Only one run executes at a time (`concurrency: visa-bot`). If a scheduled run fires while
another is still running, it waits in the queue.

---

## Configuration reference

All settings are environment variables. On GitHub they come from secrets and variables.
Locally they come from a `.env` file.

| Variable | Required | Default | Description |
|---|---|---|---|
| `VISA_EMAIL` | yes | – | Portal login email |
| `VISA_PASSWORD` | yes | – | Portal password |
| `SCHEDULE_ID` | yes | – | Your schedule id (see above) |
| `SLACK_WEBHOOK_URL` | recommended | – | Slack Incoming Webhook. Without it, results only go to the logs |
| `COUNTRY_CODE` | no | `es-pe` | Portal country/locale prefix |
| `CHECK_INTERVAL_MINUTES` | no | `10` | Base interval between checks |
| `CURRENT_APPOINTMENT_DATE` | no | – | Fallback `YYYY-MM-DD` if reading it from the portal fails |
| `MAX_RUNTIME_MINUTES` | no | `0` (no limit) | Stop the loop after this many minutes. The workflow sets `335` |

### Command-line options

```
python bot.py            # loop forever (or until MAX_RUNTIME_MINUTES)
python bot.py --once     # single check; always posts the result to Slack
python bot.py --report   # loop, and post the status of the first check (daily summary)
python bot.py --test     # only send a test message to Slack
python bot.py --show     # show the browser window (local debugging)
```

---

## Running locally

Requires Python 3.10+.

```bash
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env        # Windows: copy .env.example .env
# edit .env with your values
python bot.py --once
```

`.env` is in `.gitignore` and is never committed.

---

## Rate limiting and safety

The portal blocks accounts or IPs that query it too often, usually for a few hours.
The bot is built to stay well below that:

- **~6 checks per hour** by default (every 8–14 minutes, randomized).
- **One login per ~5h35m run**, not one per check.
- **Back-off:** 3 empty responses in a row usually means a temporary block, so the bot pauses for **3 hours**.
- **Human-like pacing:** it waits a few seconds after loading the reschedule page before querying dates.
  The server drops requests made instantly after page load.
- It uses a regular Chrome user agent. The site returns `403` to the default `HeadlessChrome` one.

### Automatic stops

The current run ends itself, and you get a 🛑 message in Slack, when:

| Situation | Why it stops |
|---|---|
| **Login fails** (wrong email/password) | Retrying could lock your portal account. Update `VISA_PASSWORD`, or disable the workflow until you do. Otherwise each scheduled run makes one login attempt |
| **Still rate-limited after the 3-hour pause** | Continuing would extend the block. The next scheduled run tries again |
| **5 failed checks in a row** (other errors) | Something is broken (site down, page changed). The next scheduled run tries again |

To stop everything, including future scheduled runs, disable the workflow (see
[Running and controlling the bot](#running-and-controlling-the-bot)).

**Choosing an interval:** start with `10`. If a week goes by without ⚠️ or 🟡 messages,
you can try `7`. Going below `5` is not recommended.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `KeyError: 'SCHEDULE_ID'` (or another variable) | Missing or misspelled secret/variable. `SCHEDULE_ID` must be under **Variables**; email, password and webhook under **Secrets** |
| `Login failed: check email/password` (🛑 in Slack) | Wrong email or password, or the password changed. Update `VISA_PASSWORD`. The bot doesn't retry within the run |
| `Timeout ... waiting for locator("#user_email")` | The sign-in page didn't load, possibly a `403` or the site being down. Try again later |
| `socket hang up` / `Failed to fetch` | Transient network drop. The bot retries automatically |
| Repeated 🟡 / empty results | Probably rate-limited. The bot backs off on its own; consider raising `CHECK_INTERVAL_MINUTES` |
| No Slack message from `test` | Check the `SLACK_WEBHOOK_URL` secret. If the Slack app was reinstalled, create a new webhook |
| Scheduled runs stopped | GitHub disables cron workflows after 60 days without repo activity. Re-enable the workflow |

Logs are in **Actions → (run) → check → Run bot**.

---

## Known limitations

- **Gaps between runs:** there are ~25 minutes without checks every 6 hours while the next run starts,
  plus whatever delay GitHub adds to scheduled runs.
- **60-day inactivity rule:** on public repositories, GitHub disables scheduled workflows after
  60 days without commits. GitHub emails you first; re-enable from the Actions tab.
- **Portal changes:** if the website changes its HTML or endpoints, the bot may need updating.
- **Single consulate:** it monitors the consulate selected on your reschedule page (Lima).
- **Notify only:** you still have to reschedule by hand, and fast. Freed-up slots often disappear within minutes.

---

## Security notes

- Credentials live only in **GitHub Secrets** (encrypted) or your local `.env`. Neither is in the repo.
- The Slack webhook URL can post to your channel. Treat it as a secret.
- If any credential is ever exposed (pasted in a chat, committed by mistake, etc.), rotate it:
  change the portal password, regenerate the Slack webhook, and update the secrets.

---

## Project structure

```
.
├── bot.py                         # The bot: login, read appointment, poll dates, notify Slack
├── requirements.txt               # Python dependencies (Playwright, requests, python-dotenv)
├── .env.example                   # Template for local configuration
├── .gitignore                     # Keeps .env and logs out of git
└── .github/workflows/visa-bot.yml # GitHub Actions schedule and manual run modes
```

---

## Disclaimer

This is a personal tool and is not affiliated with the U.S. Department of State or the
appointment service provider. It only reads information that is visible to you when you are
logged in to your own account. Use it responsibly, keep the check interval reasonable, and
follow the website's terms of use.
