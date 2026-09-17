import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import downloader


class DownloadOnlyTests(unittest.TestCase):
    def test_telegram_only_config_check(self):
        with sqlite3.connect(':memory:') as db:
            worker = downloader.Downloader({'telegram_token': 'test', 'telegram_chat_id': '42'}, db)
            with patch.object(worker, 'telegram', return_value={}) as call:
                worker.check()
                call.assert_called_once_with('getMe', {})

    def test_delivery_dedup_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory, sqlite3.connect(':memory:') as db:
            worker = downloader.Downloader({'telegram_token': 'test', 'telegram_chat_id': '42'}, db)
            def download(url, folder):
                file = folder / 'reel.mp4'
                file.write_bytes(b'video')
                return file
            update = {'update_id': 1, 'message': {'chat': {'id': 42}, 'text': 'https://instagram.com/reel/Ab/'}}
            with patch.object(downloader, 'ROOT', Path(directory)), patch.object(worker, 'say'), patch.object(worker, 'send_file') as send, patch.object(downloader.Worker, 'download', side_effect=download), patch.object(downloader.Worker, 'publish', side_effect=AssertionError('Must not publish')):
                worker.handle(update)
                worker.handle(update)
                send.assert_called_once()
                self.assertFalse(list(Path(directory).rglob('*.mp4')))

    def test_wrong_chat_is_ignored(self):
        with sqlite3.connect(':memory:') as db:
            worker = downloader.Downloader({'telegram_token': 'test', 'telegram_chat_id': '42'}, db)
            with patch.object(downloader.Worker, 'download') as download:
                worker.handle({'update_id': 2, 'message': {'chat': {'id': 99}, 'text': 'https://instagram.com/reel/Ab/'}})
                download.assert_not_called()
