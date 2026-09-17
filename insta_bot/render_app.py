"""Single-worker Render webhook service; never publishes to Instagram."""
import hmac
import os
import sqlite3
import tempfile
import threading
from collections import OrderedDict

from flask import Flask, request, jsonify
from bot import BotError, Worker, parse_link
from downloader import Downloader, MAX_SEND


def create_app(config=None):
    config = config or {
        'telegram_token': os.environ.get('TELEGRAM_BOT_TOKEN', ''),
        'telegram_chat_id': os.environ.get('TELEGRAM_CHAT_ID', ''),
        'webhook_secret': os.environ.get('TELEGRAM_WEBHOOK_SECRET', ''),
    }
    if not all(config.values()):
        raise RuntimeError('Set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID and TELEGRAM_WEBHOOK_SECRET.')
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 256 * 1024
    gate = threading.Lock()
    finished = OrderedDict()

    @app.get('/')
    @app.get('/health')
    def health():
        return jsonify(status='ok', mode='download-only')

    @app.post('/telegram')
    def telegram():
        supplied = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
        if not hmac.compare_digest(supplied, config['webhook_secret']):
            return '', 403
        update = request.get_json(silent=True)
        if not isinstance(update, dict) or not isinstance(update.get('update_id'), int):
            return '', 400
        message = update.get('message') or update.get('channel_post')
        if not isinstance(message, dict) or str(message.get('chat', {}).get('id')) != str(config['telegram_chat_id']):
            return '', 200
        # Return success only after the task finishes. Telegram retries failed or
        # timed-out requests. Reject concurrent work so CPU and RAM stay bounded.
        if not gate.acquire(blocking=False):
            return '', 503
        try:
            uid = update['update_id']
            if uid in finished:
                return '', 200
            db = sqlite3.connect(':memory:')
            try:
                worker = Downloader(config, db)
                text = message.get('text') or message.get('caption') or ''
                parsed = parse_link(text)
                chat = config['telegram_chat_id']
                if not parsed:
                    worker.say(chat, 'Send an Instagram /reel/ link to get its MP4. Download-only mode is active. After idle time the free server takes about a minute to wake up.')
                else:
                    code, url = parsed
                    worker.say(chat, 'Downloading your Reel...')
                    try:
                        with tempfile.TemporaryDirectory() as directory:
                            video = Worker.download(url, directory)
                            if video.stat().st_size > MAX_SEND:
                                worker.say(chat, 'This Reel is too large for Telegram delivery (49 MB limit in this bot). Try a shorter Reel or download it using the PC version.')
                            else:
                                worker.send_file(chat, video, code)
                    except BotError as exc:
                        worker.say(chat, str(exc) + '\nIf the file did not arrive, resend the link to try again.')
                finished[uid] = True
                while len(finished) > 1000:
                    finished.popitem(last=False)
                return '', 200
            finally:
                db.close()
        finally:
            gate.release()
    return app
