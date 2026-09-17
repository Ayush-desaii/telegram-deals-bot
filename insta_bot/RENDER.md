# Free Render deployment

This version receives Telegram webhooks and returns Reel MP4s. No Instagram API
credentials are needed. It handles one download at a time and never publishes.

## Deploy

1. Create a **private GitHub repository** with the files from this folder at its
   root. Include bot.py, downloader.py, render_app.py, requirements.txt,
   requirements-render.txt, render.yaml, and .gitignore. Never upload config.json,
   .venv, data, or tokens. The local .gitignore protects these when using Git;
   manual browser uploads require selecting only the listed files.
2. Sign into Render, choose **New > Blueprint**, and connect this repository.
   render.yaml selects the Free plan. If Render requests a payment method, stop:
   do not select a paid plan to continue this no-card setup.
3. Enter TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from your local config.json into
   Render's secret environment fields. The Blueprint generates the webhook secret.
4. Wait until Live. Open the service URL and confirm it shows
   `{"mode":"download-only","status":"ok"}` (field order may differ).
5. Close the PC bot window. Register the Telegram webhook using the local helper
   below. Do not run the polling PC version while the webhook is active.

## Connect Telegram

Run `python.cmd render_webhook.py` locally. Enter the HTTPS service URL and the
TELEGRAM_WEBHOOK_SECRET from Render's Environment page. The helper uses your local
Telegram token without printing it. It checks the server health before registering.
This redirects this bot's updates from PC polling to Render. Existing pending
messages are preserved. Then send one Reel to test cloud downloading and delivery.

To switch back: run `python.cmd render_webhook.py --disconnect`, then start.cmd.

## Free service limits

- Render sleeps after 15 minutes without inbound traffic; wake-up is about a
  minute, with possible extra delay for Telegram retries.
- Temporary files and the recent-update cache disappear when the service restarts
  or sleeps. Interrupted downloads may need resending; a retry across a restart
  can deliver a duplicate. This version does not promise durable delivery.
- Videos above 49 MB are rejected for Telegram delivery. Nothing is retained on
  Render for later retrieval. Temporary downloads are removed after each request.
- Bandwidth and compute limits apply. Heavy outbound traffic can lead to service
  suspension. No keep-alive pings are used to defeat sleeping.
- Instagram can block downloads from cloud IPs even when they work on a home PC.
  The deployed service needs an end-to-end test before relying on it.

References: https://render.com/docs/free and https://core.telegram.org/bots/api#setwebhook
