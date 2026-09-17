# Download-only mode

Run **start.cmd** in this folder and keep the window open. Your existing Telegram
token and chat ID in config.json are reused. No Meta authorization is checked and
no Instagram posts are created. You do not need to rerun setup.cmd.

Send a canonical Instagram /reel/ or /reels/ link. The bot replies with an MP4
attachment. Send the same link again to retry or download again; earlier posting
failures do not block downloads. /status explains the active mode.

Files above 49 MB (up to the downloader's 300 MB limit) stay in
data/downloads/<Telegram update ID>/reel.mp4; the bot replies with their PC path.
Completed files also remain there if Telegram delivery fails. Successfully sent
files are removed locally. Interrupted requests are not automatically resent;
resend the link manually if necessary. Pending messages retained by Telegram can
be processed when starting the bot.

check.cmd tests Telegram only. The previous publishing implementation remains in
bot.py for future work but start.cmd now runs downloader.py.
Shortened share URLs still need to be opened in a browser to copy the /reel/ URL.
