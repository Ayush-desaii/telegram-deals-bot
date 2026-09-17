import unittest
from pathlib import Path
from unittest.mock import patch
from render_app import create_app


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app({'telegram_token': 'test', 'telegram_chat_id': '42', 'webhook_secret': 'secret'}).test_client()
        self.headers = {'X-Telegram-Bot-Api-Secret-Token': 'secret'}
        self.update = {'update_id': 10, 'message': {'chat': {'id': 42}, 'text': 'https://instagram.com/reel/Ab/'}}

    def test_health_needs_no_meta(self):
        self.assertEqual(self.client.get('/health').json['mode'], 'download-only')

    def test_secret_required(self):
        self.assertEqual(self.client.post('/telegram', json=self.update).status_code, 403)

    def test_wrong_chat_ignored(self):
        self.update['message']['chat']['id'] = 99
        with patch('render_app.Worker.download') as download:
            self.assertEqual(self.client.post('/telegram', json=self.update, headers=self.headers).status_code, 200)
            download.assert_not_called()

    def test_retry_not_sent_twice(self):
        def download(url, directory):
            video = Path(directory) / 'reel.mp4'
            video.write_bytes(b'video')
            return video
        with patch('render_app.Worker.download', side_effect=download), patch('render_app.Downloader.say'), patch('render_app.Downloader.send_file') as send:
            for _ in range(2):
                self.assertEqual(self.client.post('/telegram', json=self.update, headers=self.headers).status_code, 200)
            send.assert_called_once()
