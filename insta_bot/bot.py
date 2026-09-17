"""Local Telegram -> Instagram worker. Run with --setup, --check, or no arguments."""
import argparse
import getpass
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LINK = re.compile(r"https?://(?:www\.)?instagram\.com/(?:reel|reels)/([A-Za-z0-9_-]+)/?(?=[\s?#<>]|$)", re.I)
DOWNLOAD_FORMAT = 'best[ext=mp4]'


def download_error(stderr):
    detail = stderr.decode('utf-8', errors='replace') if isinstance(stderr, bytes) else str(stderr or '')
    lower = detail.lower()
    if 'requested format is not available' in lower:
        return 'No compatible MP4 format was found for this Reel.'
    if any(word in lower for word in ('login required', 'log in', 'login', 'cookies', 'rate-limit', 'rate limit', '429')):
        return 'Instagram requires a login or is limiting downloads. The Instagram publishing token does not authenticate downloads.'
    if 'no module named yt_dlp' in lower:
        return 'yt-dlp is missing from this Python environment. Run setup.cmd to install it.'
    if any(word in lower for word in ('403', '401', 'forbidden')):
        return 'Instagram denied access to the video (HTTP 401/403).'
    if any(word in lower for word in ('10013', 'timed out', 'name resolution', 'getaddrinfo', 'connection')):
        return 'A network, firewall, or connection error prevented the download.'
    # Keep useful extractor errors without echoing links or local filesystem paths.
    detail = re.sub(r'https?://\S+', '[URL]', detail)
    detail = re.sub(r'[A-Za-z]:[\\/][^\r\n]+', '[local path]', detail)
    lines = [line.strip() for line in detail.splitlines() if 'ERROR:' in line]
    return (lines[-1][:500] if lines else 'Downloader did not produce an MP4. Try updating yt-dlp.')


class BotError(Exception):
    pass


def request(url, payload=None, headers=None, timeout=60):
    data = None if payload is None else json.dumps(payload).encode()
    headers = dict(headers or {})
    if data is not None:
        headers['Content-Type'] = 'application/json'
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Never print exception URLs: Telegram URLs contain the bot token.
        raise BotError(f'Service returned HTTP {exc.code}. Check authorization and API settings.') from None
    except (OSError, ValueError):
        raise BotError('Network error or invalid service response. Check your connection.') from None
    if result.get('error') or result.get('ok') is False:
        raise BotError('Service rejected the request. Check your account permissions and settings.')
    return result


def parse_link(text):
    match = LINK.search(text)
    if not match:
        return None
    code = match.group(1)
    return code, f'https://www.instagram.com/reel/{code}/'


def connect_db(path):
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS jobs (code TEXT PRIMARY KEY, state TEXT, container TEXT, media TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER)')
    db.commit()
    return db


def reserve(db, code):
    with db:
        cur = db.execute("INSERT OR IGNORE INTO jobs VALUES (?, 'working', '', '')", (code,))
    return cur.rowcount == 1


class Worker:
    def __init__(self, config, db):
        self.config, self.db = config, db
        self.tg = 'https://api.telegram.org/bot' + config['telegram_token'] + '/'
        self.graph = 'https://graph.instagram.com/' + config['api_version'] + '/'
        self.auth = {'Authorization': 'Bearer ' + config['instagram_token']}

    def telegram(self, method, data):
        return request(self.tg + method, data)['result']

    def say(self, chat, text):
        try:
            self.telegram('sendMessage', {'chat_id': chat, 'text': text, 'link_preview_options': {'is_disabled': True}})
        except BotError:
            print('Could not deliver Telegram status. Posting state is retained locally.', flush=True)

    def graph_call(self, path, data=None):
        return request(self.graph + path, data, self.auth)

    def state(self, code, state, container='', media=''):
        with self.db:
            self.db.execute('UPDATE jobs SET state=?, container=?, media=? WHERE code=?', (state, container, media, code))

    @staticmethod
    def download(url, directory):
        cmd = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist', '--quiet', '--no-warnings',
               '--socket-timeout', '30', '--retries', '2', '--max-filesize', '300M',
               '-f', DOWNLOAD_FORMAT,
               '-o', str(Path(directory) / 'reel.%(ext)s'), url]
        try:
            completed = subprocess.run(cmd, capture_output=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise BotError('Download timed out.') from None
        video = Path(directory) / 'reel.mp4'
        if completed.returncode or not video.exists():
            raise BotError('Download failed: ' + download_error(completed.stderr))
        if not 0 < video.stat().st_size <= 300 * 1024 * 1024:
            raise BotError('Video is empty or exceeds the 300 MB local limit.')
        return video

    def upload(self, container, video):
        # Stream the local MP4; no public file server or inbound port is required.
        import http.client
        connection = http.client.HTTPSConnection('rupload.facebook.com', timeout=300)
        try:
            size = str(video.stat().st_size)
            with video.open('rb') as stream:
                connection.request('POST', f'/ig-api-upload/{self.config["api_version"]}/{container}', body=stream,
                                   headers={'Authorization': 'OAuth ' + self.config['instagram_token'],
                                            'offset': '0', 'file_size': size, 'Content-Length': size,
                                            'Content-Type': 'application/octet-stream'})
                response = connection.getresponse()
                body = json.loads(response.read())
            if response.status >= 400 or not body.get('success'):
                raise BotError('Instagram rejected the video upload. Check format and publishing access.')
        except (OSError, ValueError, http.client.HTTPException):
            raise BotError('Video upload failed. Check your connection and Instagram settings.') from None
        finally:
            connection.close()

    def publish(self, code, url, chat):
        if not reserve(self.db, code):
            row = self.db.execute('SELECT state, media FROM jobs WHERE code=?', (code,)).fetchone()
            self.say(chat, f'Already recorded: {code} ({row[0]}). Use /status to inspect recent posts.')
            return
        publishing = False
        container = ''
        try:
            self.say(chat, 'Downloading your Reel...')
            with tempfile.TemporaryDirectory(dir=ROOT / 'data') as directory:
                video = self.download(url, directory)
                container = self.graph_call(self.config['instagram_id'] + '/media', {
                    'media_type': 'REELS', 'upload_type': 'resumable',
                    'caption': self.config.get('caption', ''), 'share_to_feed': True})['id']
                self.state(code, 'uploading', container)
                self.upload(container, video)
                self.say(chat, 'Uploaded. Waiting for Instagram to process the Reel...')
                for _ in range(60):
                    result = self.graph_call(container + '?fields=status_code')
                    status = result.get('status_code')
                    if status == 'FINISHED':
                        break
                    if status in ('ERROR', 'EXPIRED'):
                        raise BotError('Instagram could not process this video. Check its format or duration.')
                    time.sleep(10)
                else:
                    raise BotError('Instagram processing timed out; nothing was published by this bot.')
                # Persist BEFORE publishing. A timeout may mean Instagram published successfully.
                self.state(code, 'uncertain', container)
                publishing = True
                media = self.graph_call(self.config['instagram_id'] + '/media_publish', {'creation_id': container})['id']
                self.state(code, 'published', container, media)
            try:
                link = self.graph_call(media + '?fields=permalink').get('permalink', '')
            except BotError:
                link = ''
            self.say(chat, 'Published successfully! ' + (link or f'Instagram media ID: {media}'))
        except (BotError, KeyError) as exc:
            if not publishing:
                self.state(code, 'failed', container)
                self.say(chat, (str(exc) if isinstance(exc, BotError) else 'Unexpected Instagram response.') +
                         f'\nTo retry: /retry {code}')
            else:
                self.say(chat, f'Publication outcome is uncertain for {code}. Check your Instagram profile before doing anything else. Automatic retry is blocked to avoid duplicates.')

    def handle(self, update):
        message = update.get('message') or update.get('channel_post')
        if not message or str(message['chat']['id']) != str(self.config['telegram_chat_id']):
            return
        chat = message['chat']['id']
        text = message.get('text') or message.get('caption') or ''
        if text.strip() == '/status':
            rows = self.db.execute('SELECT code, state FROM jobs ORDER BY rowid DESC LIMIT 10').fetchall()
            self.say(chat, '\n'.join(f'{code}: {state}' for code, state in rows) or 'No posts yet.')
            return
        if text.startswith('/retry '):
            code = text.split(maxsplit=1)[1].strip()
            with self.db:
                deleted = self.db.execute("DELETE FROM jobs WHERE code=? AND state='failed'", (code,)).rowcount
            if deleted:
                self.publish(code, f'https://www.instagram.com/reel/{code}/', chat)
            else:
                self.say(chat, 'Only confirmed pre-publication failures can be retried.')
            return
        parsed = parse_link(text)
        if parsed:
            self.publish(*parsed, chat)
        else:
            self.say(chat, 'Send an Instagram /reel/ link to automatically post it. /status shows recent activity.')

    def check(self):
        self.telegram('getMe', {})
        info = self.graph_call(self.config['instagram_id'] + '?fields=id,username')
        print('Telegram token valid. Instagram account: @' + info.get('username', '(unknown)'))
        print('This read-only check does not prove publishing permission or video upload compatibility.')

    def run(self):
        self.check()
        if self.telegram('getWebhookInfo', {}).get('url'):
            raise BotError('This bot already has a webhook. Use a separate Telegram bot for this local worker.')
        # Interrupted work before publication is safe to retry manually.
        with self.db:
            self.db.execute("UPDATE jobs SET state='failed' WHERE state IN ('working','uploading')")
        print('Running. Keep this window open and your PC awake. Ctrl+C stops the bot.', flush=True)
        while True:
            row = self.db.execute("SELECT value FROM settings WHERE key='offset'").fetchone()
            offset = row[0] if row else 0
            try:
                updates = self.telegram('getUpdates', {'offset': offset, 'timeout': 30, 'allowed_updates': ['message', 'channel_post']})
                for update in updates:
                    self.handle(update)
                    with self.db:
                        self.db.execute("INSERT OR REPLACE INTO settings VALUES ('offset', ?)", (update['update_id'] + 1,))
            except BotError as exc:
                print(str(exc), flush=True)
                time.sleep(10)


def setup():
    path = ROOT / 'config.json'
    if path.exists():
        raise BotError('config.json already exists. Edit it locally to change settings.')
    token = getpass.getpass('Telegram bot token from @BotFather (hidden): ').strip()
    base = 'https://api.telegram.org/bot' + token + '/'
    request(base + 'getMe', {})
    print('Send /start to your new bot, then return here.')
    input('Press Enter after sending /start: ')
    updates = request(base + 'getUpdates', {'timeout': 0})['result']
    chats = {str(m['chat']['id']) for u in updates for m in [u.get('message') or u.get('channel_post')] if m}
    print('Chat IDs seen by this bot: ' + ', '.join(sorted(chats)))
    chat = input('Your chat ID (or private channel ID): ').strip()
    if not re.fullmatch(r'-?\d+', chat):
        raise BotError('Chat ID must be numeric.')
    config = {'telegram_token': token, 'telegram_chat_id': chat,
              'instagram_id': input('Instagram API user ID (not your username): ').strip(),
              'instagram_token': getpass.getpass('Instagram Login access token (hidden): ').strip(),
              'api_version': input('Meta API version shown in your app (e.g. v25.0): ').strip(),
              'caption': input('Default caption (blank for none): ').strip()}
    validate(config)
    path.write_text(json.dumps(config, indent=2), encoding='utf-8')
    print('Saved config.json locally. Run start.cmd when ready to automatically publish shared links.')


def validate(config):
    for key in ('telegram_token', 'telegram_chat_id', 'instagram_id', 'instagram_token', 'api_version'):
        if not config.get(key):
            raise BotError('Missing configuration: ' + key)
    if not re.fullmatch(r'v\d+\.\d+', config['api_version']) or not config['instagram_id'].isdigit():
        raise BotError('Invalid API version or Instagram user ID.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--setup', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--test-download', action='store_true', help='Test a Reel download without posting or requiring account credentials')
    args = parser.parse_args()
    if args.test_download:
        parsed = parse_link(input('Paste the Instagram /reel/ link: ').strip())
        if not parsed:
            raise BotError('Expected an Instagram /reel/ or /reels/ link.')
        with tempfile.TemporaryDirectory() as directory:
            video = Worker.download(parsed[1], directory)
            print(f'Download succeeded ({video.stat().st_size:,} bytes). No Instagram post was created.')
        return
    if args.setup:
        setup()
        return
    config_path = ROOT / 'config.json'
    if not config_path.exists():
        raise BotError('Run setup.cmd first to connect your accounts.')
    config = json.loads(config_path.read_text(encoding='utf-8'))
    validate(config)
    (ROOT / 'data').mkdir(exist_ok=True)
    # An OS-held lock prevents two local workers from publishing simultaneously.
    import msvcrt
    with (ROOT / 'data' / 'worker.lock').open('a+b') as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise BotError('Another copy of this bot is already running.') from None
        db = connect_db(ROOT / 'data' / 'posts.db')
        try:
            worker = Worker(config, db)
            worker.check() if args.check else worker.run()
        finally:
            db.close()


if __name__ == '__main__':
    try:
        main()
    except (BotError, ValueError) as error:
        print('Setup/action needed: ' + str(error))
        sys.exit(1)
    except KeyboardInterrupt:
        print('\nStopped.')
