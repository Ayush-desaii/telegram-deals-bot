# Telegram to Instagram, on your Windows PC

Share an Instagram Reel link to your Telegram bot. While `start.cmd` is running,
it downloads the MP4, uploads it to Instagram, publishes it, and replies with the
post link. The PC must stay awake and connected to the internet.

## Connect your accounts

1. In Telegram, open the official **@BotFather**, send `/newbot`, and choose a name
   and username. Keep the resulting token private. Use a new bot so it does not
   conflict with another service.
2. Open [Meta for Developers](https://developers.facebook.com/), create an app,
   and configure **Instagram API with Instagram Login**. Add your Creator account
   as an Instagram tester/account and accept the invitation when requested.
   Authorize `instagram_business_basic` and `instagram_business_content_publish`.
   Obtain the Instagram user ID and access token through this setup, and note the
   API version shown by Meta. These are not your Instagram username or password.
   Your own account must have the appropriate app role in development mode;
   publishing for other users can require app review. Dashboard labels can vary.
3. Double-click **setup.cmd**. It creates an isolated Python environment and
   installs yt-dlp. Python 3.11+ or the available Codex bundled Python is required.
   Follow the prompts, send `/start` to the Telegram bot, then enter your chat ID
   from the displayed list. Enter the Instagram credentials when prompted.
4. Double-click **check.cmd** for a read-only connection check.
5. Double-click **start.cmd**. Sharing a link now triggers automatic publication.
   Start with one of your own Reels to verify the complete workflow.

Tokens are stored in `config.json`, excluded from Git. Enter them locally; do not
paste them into a chat. Token expiration/revocation requires updating that file
and restarting the bot. This initial version does not refresh tokens automatically.

For a channel, add the bot as an administrator of a private channel and configure
its numeric channel ID. Every new Reel link in that channel can trigger posting,
so only give trusted people permission to post. Direct bot chat is the simplest
option. The bot does not read Saved Messages.

## Behavior

- Only the configured Telegram chat is accepted.
- Supports canonical `instagram.com/reel/...` and `/reels/...` links, including
  sharing query parameters. Redirect/share URLs and non-Reel posts are unsupported.
- Uses the configured fixed caption (blank by default). It does not copy the
  original caption, attribution, licensed music metadata, or collaboration tags.
- Downloads a single MP4 with audio, up to 300 MB. No transcoding is included;
  Instagram may reject an incompatible video. Restricted downloads can fail.
- `/status` lists recent jobs. `/retry SHORTCODE` retries confirmed failures that
  occurred before publication. Download files are removed after each attempt.
- SQLite history prevents repeat posts, including after a restart. Do not delete
  `data/posts.db` if you want to retain duplicate protection.
- If the publication request loses its response, the job stays `uncertain` and
  cannot be automatically retried. Check Instagram before manually resolving it.
- Only one worker may run at a time. Close it or press Ctrl+C to stop.
- Telegram retains pending updates for a limited time (normally up to 24 hours).
  On startup, retained links are processed, including links sent while the PC was off.

Use videos you own or have permission to republish. An audio track present in the
download is part of the uploaded file; this does not recreate Instagram music tags.

## Troubleshooting and verification

Double-click **test-download.cmd** and paste a Reel URL to test downloading alone.
It does not require account credentials, publish anything, or modify posting history.
The temporary video is removed after the test. Errors distinguish login/rate limits,
format selection, missing dependencies, and network problems. MP4s with unknown
codec metadata are accepted by the format selector.

If downloading breaks, close the bot and run `python.cmd -m pip install --upgrade
yt-dlp` in this directory, then restart. This version does not extract browser
cookies or sign in to download restricted Reels.

HTTP authorization errors: check token expiration, account ID, API version, and
publishing permissions. A successful `check.cmd` verifies identity access, not the
full upload/publish flow. No real post has been tested without your credentials.

The local upload implementation uses a resumable media container followed by a
binary upload to Meta. This path still needs verification against your configured
Meta app; account/API errors must be resolved before treating it as operational.

Run offline tests with `python.cmd -m unittest discover -s tests -v`.

References: [Meta Instagram API](https://www.postman.com/meta/workspace/instagram/documentation/23987686-9386f468-7714-490f-9bfc-9442db5c8f00),
[content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/),
[Telegram Bot API](https://core.telegram.org/bots/api),
[yt-dlp](https://github.com/yt-dlp/yt-dlp).
