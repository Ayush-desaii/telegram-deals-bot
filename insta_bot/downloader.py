"""Telegram-only Reel downloader. No Instagram API calls or publishing."""
import argparse
import json
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from bot import BotError, Worker, parse_link, request

ROOT = Path(__file__).resolve().parent
MAX_SEND = 49_000_000


class Downloader:
    def __init__(self, config, db):
        self.config, self.db = config, db
        self.tg = 'https://api.telegram.org/bot' + config['telegram_token'] + '/'
        db.execute('CREATE TABLE IF NOT EXISTS downloads (update_id INTEGER PRIMARY KEY, state TEXT)')
        db.commit()

    def telegram(self, method, data):
        return request(self.tg + method, data)['result']

    def say(self, chat, text):
        try:
            self.telegram('sendMessage', {'chat_id': chat, 'text': text})
        except BotError:
            print('Telegram status could not be delivered.', flush=True)

    def send_file(self, chat, video, code):
        if video.stat().st_size > MAX_SEND:
            raise BotError('Video exceeds the Telegram upload limit for this bot.')
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in {'chat_id': str(chat), 'caption': 'Downloaded Reel: ' + code}.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{code}.mp4"\r\nContent-Type: video/mp4\r\n\r\n'.encode())
        parts.extend([video.read_bytes(), f'\r\n--{boundary}--\r\n'.encode()])
        req = urllib.request.Request(self.tg + 'sendDocument', data=b''.join(parts),
                                     headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                result = json.load(response)
            if not result.get('ok'):
                raise BotError('Telegram rejected the file upload.')
        except urllib.error.HTTPError as exc:
            raise BotError(f'Telegram upload returned HTTP {exc.code}.') from None
        except (OSError, ValueError):
            raise BotError('Telegram upload was interrupted; check the chat before resending.') from None

    def handle(self, update):
        uid = update['update_id']
        if self.db.execute('SELECT 1 FROM downloads WHERE update_id=?', (uid,)).fetchone():
            return
        # Reserve before sending so a restart cannot resend an uncertain upload.
        with self.db:
            self.db.execute("INSERT INTO downloads VALUES (?, 'received')", (uid,))
        message = update.get('message') or update.get('channel_post')
        if not message or str(message['chat']['id']) != str(self.config['telegram_chat_id']):
            return
        chat = message['chat']['id']
        text = message.get('text') or message.get('caption') or ''
        if text.strip() in ('/start', '/help', '/status'):
            self.say(chat, 'Download-only mode is active. Send an Instagram /reel/ link and I will return the MP4. No Instagram posts are created. Resend a link to download it again.')
            return
        if text.startswith('/retry '):
            code = text.split(maxsplit=1)[1].strip()
            text = 'https://www.instagram.com/reel/' + code + '/'
        parsed = parse_link(text)
        if not parsed:
            self.say(chat, 'Send an Instagram /reel/ or /reels/ link. Open shortened share links in your browser and copy the final address.')
            return
        code, url = parsed
        folder = ROOT / 'data' / 'downloads' / str(uid)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            self.say(chat, 'Downloading your Reel...')
            video = Worker.download(url, folder)
            if video.stat().st_size > MAX_SEND:
                self.say(chat, f'This video is too large to send here. Saved on your PC: {video}')
                state = 'saved locally'
            else:
                self.send_file(chat, video, code)
                state = 'delivered'
                video.unlink()
                # Only remove an empty job directory, never another job's files.
                try:
                    folder.rmdir()
                except OSError:
                    pass
        except (BotError, OSError) as exc:
            state = 'failed'
            detail = str(exc) if isinstance(exc, BotError) else 'Local file access failed.'
            self.say(chat, detail + '\nResend the link to retry. Any completed file is retained in data/downloads on your PC.')
        with self.db:
            self.db.execute('UPDATE downloads SET state=? WHERE update_id=?', (state, uid))

    def check(self):
        self.telegram('getMe', {})
        print('Telegram connected. DOWNLOAD-ONLY mode. Facebook/Instagram API authorization is not required.', flush=True)

    def run(self):
        self.check()
        if self.telegram('getWebhookInfo', {}).get('url'):
            raise BotError('A webhook is configured for this Telegram bot. Use a dedicated bot for PC polling.')
        print('Keep this window open and your PC awake. Ctrl+C stops the downloader.', flush=True)
        while True:
            row = self.db.execute('SELECT MAX(update_id) FROM downloads').fetchone()
            offset = row[0] + 1 if row[0] is not None else 0
            try:
                updates = self.telegram('getUpdates', {'offset': offset, 'timeout': 30, 'allowed_updates': ['message', 'channel_post']})
                for update in updates:
                    self.handle(update)
            except BotError as exc:
                print(str(exc), flush=True)
                time.sleep(10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    if not config.get('telegram_token') or not config.get('telegram_chat_id'):
        raise BotError('Telegram token and chat ID are required in config.json.')
    (ROOT / 'data').mkdir(exist_ok=True)
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
            raise BotError('Close the other running bot window first.') from None
        with sqlite3.connect(ROOT / 'data' / 'downloads.db') as db:
            worker = Downloader(config, db)
            worker.check() if args.check else worker.run()


if __name__ == '__main__':
    try:
        main()
    except (BotError, ValueError, OSError) as exc:
        print('Action needed: ' + (str(exc) if isinstance(exc, BotError) else 'Check config.json and local file permissions.'))
        raise SystemExit(1)
    except KeyboardInterrupt:
        print('\nStopped.')
