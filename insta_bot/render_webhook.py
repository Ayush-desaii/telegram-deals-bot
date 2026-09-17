"""Explicit local switch between Render webhooks and PC polling."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from bot import BotError, request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--disconnect', action='store_true')
    args = parser.parse_args()
    config = json.loads((Path(__file__).resolve().parent / 'config.json').read_text())
    telegram = 'https://api.telegram.org/bot' + config['telegram_token'] + '/'
    if args.disconnect:
        request(telegram + 'deleteWebhook', {'drop_pending_updates': False})
        print('Webhook removed. You can now start the PC bot.')
        return
    url = input('Render service URL (https://NAME.onrender.com): ').strip().rstrip('/')
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.onrender.com') or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.port:
        raise BotError('Enter the HTTPS root URL of your onrender.com service.')
    health = request(url + '/health', timeout=120)
    if health.get('mode') != 'download-only':
        raise BotError('The service did not report download-only mode.')
    while True:
        secret = input('TELEGRAM_WEBHOOK_SECRET from Render (visible): ').strip()
        if re.fullmatch(r'[A-Za-z0-9_-]{1,256}', secret):
            break
        print('Use 1-256 characters: letters, numbers, underscores or hyphens only. '
              'Copy the secret VALUE, without quotes or the variable name. Try again.')
    request(telegram + 'setWebhook', {'url': url + '/telegram', 'secret_token': secret,
            'max_connections': 1, 'allowed_updates': ['message', 'channel_post'], 'drop_pending_updates': False})
    print('Telegram webhook registered. Keep the PC bot stopped and send a Reel to test Render.')


if __name__ == '__main__':
    try:
        main()
    except (BotError, OSError, ValueError) as exc:
        print(str(exc) if isinstance(exc, BotError) else 'Check local configuration and the entered URL.')
        raise SystemExit(1)
