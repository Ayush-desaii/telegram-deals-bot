import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot
from yt_dlp import YoutubeDL


class WorkflowTests(unittest.TestCase):
    def test_mp4_with_unknown_codecs_is_accepted(self):
        formats = [{'url': 'https://example.com/video.mp4', 'ext': 'mp4', 'format_id': 'mp4'}]
        with YoutubeDL({'quiet': True}) as downloader:
            selected = list(downloader.build_format_selector(bot.DOWNLOAD_FORMAT)({
                'formats': formats, 'has_merged_format': False, 'incomplete_formats': False}))
        self.assertEqual(len(selected), 1)

    def test_downloader_reports_login_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch('bot.subprocess.run') as run:
            run.return_value.returncode = 1
            run.return_value.stderr = b'ERROR: Instagram login required'
            with self.assertRaisesRegex(bot.BotError, 'requires a login'):
                bot.Worker.download('https://instagram.com/reel/Ab/', directory)

    def test_error_redacts_urls(self):
        error = bot.download_error(b'ERROR: fetch failed https://example.com/?token=private')
        self.assertNotIn('private', error)
        self.assertIn('fetch failed', error)

    def setUp(self):
        self.db = bot.connect_db(':memory:')
        self.worker = bot.Worker({'telegram_token': 'test', 'instagram_token': 'test',
                                  'instagram_id': '123', 'telegram_chat_id': '42',
                                  'api_version': 'v25.0'}, self.db)

    def tearDown(self):
        self.db.close()

    def test_links(self):
        self.assertEqual(bot.parse_link('Look https://www.instagram.com/reel/Ab_12/?igsh=x')[0], 'Ab_12')
        self.assertIsNone(bot.parse_link('https://instagram.com.evil.test/reel/Ab/'))
        self.assertIsNone(bot.parse_link('https://instagram.com/p/Ab/'))

    def test_other_chat_cannot_publish(self):
        with patch.object(self.worker, 'publish') as publish:
            self.worker.handle({'message': {'chat': {'id': 99}, 'text': 'https://instagram.com/reel/Ab/'}})
            publish.assert_not_called()

    def test_restart_preserves_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'db.sqlite'
            first = bot.connect_db(path)
            self.assertTrue(bot.reserve(first, 'Ab'))
            first.close()
            second = bot.connect_db(path)
            self.assertFalse(bot.reserve(second, 'Ab'))
            second.close()

    def test_uncertain_cannot_retry(self):
        bot.reserve(self.db, 'Ab')
        self.worker.state('Ab', 'uncertain', 'container')
        with patch.object(self.worker, 'publish') as publish, patch.object(self.worker, 'say'):
            self.worker.handle({'message': {'chat': {'id': 42}, 'text': '/retry Ab'}})
            publish.assert_not_called()

    def test_timeout_during_publish_blocks_duplicate(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bot, 'ROOT', Path(directory)):
            (Path(directory) / 'data').mkdir()
            with patch.object(self.worker, 'say'), patch.object(self.worker, 'download', return_value=Path('fake.mp4')), patch.object(self.worker, 'upload'), patch.object(self.worker, 'graph_call', side_effect=[{'id': 'container'}, {'status_code': 'FINISHED'}, bot.BotError('timeout')]):
                self.worker.publish('Ab', 'https://instagram.com/reel/Ab/', 42)
        self.assertEqual(self.db.execute('SELECT state FROM jobs').fetchone()[0], 'uncertain')
        self.assertFalse(bot.reserve(self.db, 'Ab'))

    def test_failed_permalink_does_not_undo_publication(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bot, 'ROOT', Path(directory)):
            (Path(directory) / 'data').mkdir()
            with patch.object(self.worker, 'say'), patch.object(self.worker, 'download', return_value=Path('fake.mp4')), patch.object(self.worker, 'upload'), patch.object(self.worker, 'graph_call', side_effect=[{'id': 'container'}, {'status_code': 'FINISHED'}, {'id': 'media'}, bot.BotError('timeout')]):
                self.worker.publish('Ab', 'https://instagram.com/reel/Ab/', 42)
        self.assertEqual(self.db.execute('SELECT state, media FROM jobs').fetchone(), ('published', 'media'))


if __name__ == '__main__':
    unittest.main()
