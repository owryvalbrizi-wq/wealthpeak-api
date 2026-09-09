# Telegram Bot — Stable Setup Guide

## Required environment variables (Render Dashboard → Environment)

| Variable              | Example                                      | Required |
|-----------------------|----------------------------------------------|----------|
| `TELEGRAM_BOT_TOKEN`  | `7123456789:AAH...` (from @BotFather)        | Yes      |
| `TELEGRAM_CHAT_ID`    | `123456789` (your personal or group chat id) | Yes      |
| `ADMIN_SECRET`        | any strong string                            | Recommended |
| `PUBLIC_API_URL`      | `https://wealthpeak-api-2.onrender.com`      | Yes      |

## How to get the values

1. **Bot token**
   - Open Telegram → talk to `@BotFather`
   - `/newbot` or use existing bot → copy the token

2. **Chat ID**
   - Start a chat with your bot (send `/start`)
   - Or add the bot to a group
   - Visit: `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Look for `"chat":{"id": 123456789}`

## After deploying / changing env vars

Call the setup endpoint once:

```bash
curl -X POST "https://wealthpeak-api-2.onrender.com/api/telegram/setup-webhook"
```

Or with explicit URL:

```bash
curl -X POST "https://wealthpeak-api-2.onrender.com/api/telegram/setup-webhook" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://wealthpeak-api-2.onrender.com"}'
```

## Verify everything works

```bash
# 1. Status
curl https://wealthpeak-api-2.onrender.com/api/telegram/status

# 2. Send test message (needs ADMIN_SECRET)
curl -X POST https://wealthpeak-api-2.onrender.com/api/telegram/test \
  -H "Content-Type: application/json" \
  -d '{"secret":"your-admin-secret"}'
```

You should receive a message in Telegram and see `"bot_ok": true`.

## What was fixed for stability

- Retries + exponential backoff on every Telegram API call
- Photo send fails gracefully → falls back to text + buttons
- Webhook always returns `{"ok":true}` quickly (Telegram requires this)
- Env vars re-read on every request (no stale values after restart)
- Photo size limited to avoid timeouts on free Render tier
- Explicit `setup-webhook` endpoint so you can re-bind after redeploy
- Clear status endpoint for debugging
- Approve / Reject buttons work both via callback and text commands

## Manual approve/reject (if buttons fail)

In the chat with the bot simply type:

```
APPROVE 42
REJECT 42
```

(replace 42 with the receipt id)

## Important notes for free Render tier

- The free instance sleeps after ~15 min of inactivity.
- First request after sleep can take 30–50 seconds.
- Telegram may retry the webhook; our handler is idempotent.
- Keep the server awake by having the frontend call `/api/plans` on load (already implemented).
