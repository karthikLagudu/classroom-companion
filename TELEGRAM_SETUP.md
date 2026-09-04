# Telegram integration setup and acceptance guide

Classroom Companion owns every student identity. Telegram is only a private messaging channel linked to an existing `User` through a short-lived, one-time token. Names, emails, internal IDs, school IDs, passwords, and Telegram usernames are never placed in a connection URL.

## Before you start

You need Python 3.12+, a Telegram bot token, its bot username, and a public HTTPS URL that forwards to this FastAPI application. Telegram cannot call `localhost` or `127.0.0.1` on your computer. For local testing, run any reputable HTTPS tunnel that forwards its public URL to `http://127.0.0.1:8000`; the application does not depend on a particular tunnel provider.

Never paste the real bot token into source code, tests, screenshots, documentation, frontend JavaScript, or Git. Store it only in the untracked `.env` file or your deployment secret manager.

## BotFather setup

1. Open Telegram and chat with the verified `@BotFather` account.
2. Create or select your bot and obtain its bot token.
3. Copy the bot username from BotFather or the bot profile. Configure only the username text; the application removes an accidental leading `@`.
4. If a token is ever exposed in chat, a screenshot, source control, or logs, revoke/rotate it with BotFather and replace the value in `.env`.

BotFather creates the Telegram bot identity. It does not create Classroom Companion students; teachers must create Rahul and Priya in the application database first.

## Exact local setup

1. Clone or open the repository and enter it.
2. Create and activate a virtual environment.

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install the application and development dependencies.

   ```powershell
   python -m pip install -e ".[dev]"
   ```

4. Create the local environment file.

   ```powershell
   Copy-Item .env.example .env
   ```

5. Put these values in `.env`. Use your real values only in that file.

   ```dotenv
   TELEGRAM_MODE=real
   TELEGRAM_BOT_TOKEN=<MY_BOT_TOKEN>
   TELEGRAM_BOT_USERNAME=<MY_BOT_USERNAME_WITHOUT_@>
   TELEGRAM_WEBHOOK_SECRET=<RANDOM_LONG_SECRET>
   TELEGRAM_LINK_TOKEN_MINUTES=30
   BASE_URL=<PUBLIC_HTTPS_URL>
   ```

   Generate the webhook secret locally, for example:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

6. Initialize demo data if needed and start the application.

   ```powershell
   python -m scripts.seed
   python -m uvicorn app.main:app --reload
   ```

7. Start your HTTPS tunnel to local port 8000. Set `BASE_URL` to its public HTTPS origin, without a trailing application path. Restart FastAPI after changing `.env`.
8. Register and verify the webhook.

   ```powershell
   python -m scripts.set_telegram_webhook
   ```

   A successful run prints `Telegram webhook configured and verified successfully.` It never prints the bot token or webhook secret. The script requires real mode, a bot username, a strong non-default webhook secret, and an HTTPS base URL. It configures Telegram's `secret_token` header as an additional request check while retaining the secret path used by the existing application.

9. Open the web application, sign in as a teacher/coordinator, open a class, and add students before linking Telegram.
10. Use **Generate Telegram link** for a student. Copy it before leaving the page; only its SHA-256 digest is stored, so the raw link cannot be recovered later.
11. Open the link with the intended Telegram account and press **START**. The student row should change to **Connected** after reloading.
12. Create an assignment from the class page and select its recipients. Delivery evidence appears on the assignment page and teacher dashboard.
13. Run due reminders with `python -m scripts.run_reminders` or the authorized **Run reminders** web action.

## Where each Telegram setting comes from

| Setting | Source |
|---|---|
| `TELEGRAM_MODE` | Use `log` for offline development and `real` for the live bot. |
| `TELEGRAM_BOT_TOKEN` | BotFather. Treat this as a password and keep it only in `.env`. |
| `TELEGRAM_BOT_USERNAME` | BotFather or the bot profile. Enter it without `@`; an accidental leading `@` is normalized. |
| `TELEGRAM_WEBHOOK_SECRET` | Generate it locally with the Python command above. It is unrelated to the BotFather token. |
| `TELEGRAM_LINK_TOKEN_MINUTES` | Application policy; `30` is the default and accepts 5–1440 minutes. |
| `BASE_URL` | Your public deployment origin or the temporary HTTPS tunnel URL. |

Real mode refuses to start without `TELEGRAM_BOT_TOKEN`. The webhook setup command additionally checks the bot username, non-placeholder webhook secret, and HTTPS base URL.

## Windows real-bot setup in three terminals

Download the current Windows MSI or executable from [Cloudflare's official downloads page](https://developers.cloudflare.com/tunnel/downloads/). Install the MSI, or place `cloudflared.exe` in a directory on your `PATH`, open a new PowerShell window, and confirm:

```powershell
cloudflared --version
```

Cloudflare is only a convenient beginner tunnel for local testing. The application does not depend on it and any trustworthy public HTTPS reverse tunnel or deployment works.

### Terminal 1 — FastAPI

From the repository with the virtual environment activated:

```powershell
python -m uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000> and confirm the application loads.

### Terminal 2 — public HTTPS tunnel

Keep FastAPI running and open a second PowerShell window:

```powershell
cloudflared tunnel --url http://localhost:8000
```

Cloudflare prints a temporary address similar to `https://random-name.trycloudflare.com`. Copy the actual URL from your terminal and place it in `.env`:

```dotenv
BASE_URL=https://random-name.trycloudflare.com
```

The shown hostname is only an example; never hardcode it. Keep the Cloudflare terminal open. A Quick Tunnel URL can change after restart. Whenever it changes, update `BASE_URL`, restart FastAPI, and register the webhook again.

Also confirm the remaining `.env` values:

```dotenv
TELEGRAM_MODE=real
TELEGRAM_BOT_TOKEN=<MY_BOT_TOKEN>
TELEGRAM_BOT_USERNAME=<MY_BOT_USERNAME_WITHOUT_@>
TELEGRAM_WEBHOOK_SECRET=<MY_GENERATED_SECRET>
TELEGRAM_LINK_TOKEN_MINUTES=30
```

After editing `.env`, stop Terminal 1 with `Ctrl+C` and run the FastAPI command again. Although `--reload` watches source code, explicitly restarting ensures the new settings are loaded.

### Terminal 3 — register and verify Telegram

Open a third PowerShell window, enter the repository, and activate its environment:

```powershell
.\.venv\Scripts\Activate.ps1
python -m scripts.set_telegram_webhook
python -m scripts.check_telegram_webhook
```

The setup command should report success and a URL ending in `/telegram/webhook/***`. The check command safely displays the redacted URL, pending update count, last Telegram error, and allowed update types. Neither command prints the bot token or full webhook secret.

## Connection behavior

- `/start TOKEN` is the primary onboarding route. A valid token links the exact internal user named by the token to the numeric Telegram user/chat IDs and immediately consumes the token.
- Plain `/start` explains how to obtain a secure link and never guesses an identity.
- `/join CODE EMAIL` remains available as the manual fallback.
- Creating a new link revokes any prior unused link for that student.
- Expired, revoked, used, and unknown tokens are rejected.
- A Telegram account already connected to another user is rejected.
- An already-connected student cannot be silently moved to a different Telegram account. A scoped teacher/coordinator must disconnect first.
- Disconnecting clears only `telegram_user_id` and `telegram_chat_id` and revokes active link tokens. It does not remove the user, memberships, assignments, state, submissions, or feedback.

## Exact two-student acceptance test

Use two different Telegram accounts. Telegram account A belongs to Rahul; account B belongs to Priya.

1. Sign in as the teacher and add `Rahul` / `rahul.test@example.com` and `Priya` / `priya.test@example.com` to the same class. Enter a temporary password of at least 10 characters for each. Confirm both show **Not connected**.
2. Generate Rahul's link, open it with account A, and press **START**. Reload the class: Rahul is connected and Priya is not.
3. Generate Priya's link, open it with account B, and press **START**. Reload: both are connected.
4. Create an assignment with only Rahul checked. Account A receives the message; account B receives nothing. The assignment has only Rahul's target/state/reminder rows.
5. Create an assignment with only Priya checked. Account B receives it; account A receives nothing.
6. Create an assignment with both checked. Both accounts receive exactly one assignment message.
7. Disconnect Priya and create another assignment for both. Rahul receives it. Priya receives nothing, but her durable `NotificationDelivery` is `skipped` with `User has not linked Telegram`; assignment creation still succeeds.
8. Create a near-due assignment, then run `python -m scripts.run_reminders`. Only connected, targeted, eligible students receive reminders. Completed/submitted students and cancelled assignments are suppressed, and quiet hours defer sending.

The assignment message includes the title, instructions, school-local due time, class, and the existing acknowledge, blocked, and submit/help buttons.

## Practical reminder test without waiting 24 hours

1. Outside the configured school quiet hours, create an assignment due about one hour from now. The existing scheduler places its 24-hour and 2-hour jobs at the current time because both reminder windows are already open.
2. Keep the student targeted, connected, and in a non-final state such as assigned or acknowledged.
3. Run:

   ```powershell
   python -m scripts.run_reminders
   ```

4. Confirm the eligible student receives a message and inspect the assignment delivery/reminder rows in the teacher interface.

If you run this during quiet hours, the correct result is `deferred` until the configured school-local morning. Do not change production reminder rules merely to force an immediate test.

## Troubleshooting

### Bot does not respond or `/start TOKEN` is not processed

Confirm FastAPI is running, `TELEGRAM_MODE=real`, the token and username are correct, the webhook script succeeded, and the link was opened in a private bot chat. Restart the app after `.env` changes. Generate a fresh link if the old token expired, was regenerated/revoked, or was already used.

`TELEGRAM_BOT_TOKEN is required` means real mode was enabled without a token in the `.env` file loaded by the current terminal. Add the BotFather token and restart FastAPI. `BASE_URL must be a public HTTPS URL` means the tunnel/deployment URL is missing, still localhost, or uses HTTP.

### Webhook receives no updates

Telegram cannot reach localhost. Confirm the tunnel is running, `BASE_URL` exactly matches its current HTTPS origin, its certificate is valid, and it forwards to port 8000. Re-run the webhook setup script whenever the public URL changes. A webhook-secret mismatch is returned as a generic 404; ensure the same `TELEGRAM_WEBHOOK_SECRET` is loaded by both the running app and setup script.

If the Cloudflare terminal stopped, restart it, copy the new URL, update `.env`, restart FastAPI, and rerun both webhook commands. The status checker reveals whether Telegram still points to an old tunnel URL and shows Telegram's latest delivery error safely.

### Wrong token or invalid bot token

Copy the token from BotFather into `.env` without quotes or whitespace, restart the app, and run the setup script again. Errors are sanitized so the token is not written into delivery records or application logs.

### Student is not connected or notification is skipped

Open the class roster and confirm **Connected**. A skipped delivery with `User has not linked Telegram` means the assignment is valid but that student has no saved chat ID. Generate a fresh link and complete `/start TOKEN` from the intended account.

### Telegram account is already linked

One numeric Telegram account can belong to only one Classroom Companion user. Disconnect it from the currently linked student before reconnecting. Usernames and display names do not affect this rule.

If the student record is already linked to a different Telegram account, use **Disconnect Telegram** from the authorized class page, generate a new link, and open it with the intended account. The application never silently replaces a connection.

### Bot was blocked, Telegram timed out, or Telegram returned HTTP error

Ask the student to unblock/start the bot. The application records a sanitized failed delivery and schedules bounded retries. Assignment creation remains valid. Inspect `attempt_count`, `last_attempt_at`, `next_attempt_at`, and `error` on the delivery evidence.

### Assignment exists but no Telegram message arrived

Confirm the student was checked as a recipient, appears in `AssignmentTarget`, is connected, and the delivery status is `sent` (real mode), not `logged`, `skipped`, or `failed`. `logged` means the app is still in demo/log mode. No broadcast occurs: unrelated students intentionally receive nothing.

### Reminder did not fire

Confirm a reminder is due, the assignment schedule version matches the job, and the student is targeted and not submitted/completed/cancelled. Quiet hours defer jobs until the configured school-local morning. Deadline edits cancel stale jobs and create versioned replacements.

## Development and production boundary

The take-home uses SQLite, `create_all`, an in-process reminder poller, and local files. Adding the link-token model creates its new table automatically, but production schema changes should use Alembic. A production evolution should use PostgreSQL, a transactional outbox, durable background workers, Redis or another queue where appropriate, managed secrets, scanned object storage, centralized monitoring, rate limiting, and a dead-letter workflow. Those infrastructure changes are intentionally documented rather than added to this focused integration.
