"""Check publishing quota and upload a test container; do not publish it."""
import json
import sys
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from bot import Worker

config = json.loads(Path(sys.argv[1]).read_text())
token = config['instagram_token']
base = 'https://graph.instagram.com/' + config['api_version'] + '/'


def call(path, data=None):
    headers = {'Authorization': 'Bearer ' + token}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(base + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = json.load(exc)
        error = body.get('error', {})
        print('HTTP', exc.code, 'code', error.get('code'), 'message:', str(error.get('message', '')).replace(token, '[redacted]'))
        raise SystemExit(1)


print('Publishing quota:', call(config['instagram_id'] + '/content_publishing_limit?fields=quota_usage,config'))
with tempfile.TemporaryDirectory() as directory:
    video = Worker.download('https://www.instagram.com/reel/Dc_dppZhIP7/', directory)
    fetched = subprocess.run([sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist', '--quiet', '-f', 'best[ext=mp4]', '--get-url', 'https://www.instagram.com/reel/Dc_dppZhIP7/'], capture_output=True, text=True, timeout=120)
    if fetched.returncode:
        raise SystemExit('Could not resolve test video URL.')
    video_url = sys.argv[2] if len(sys.argv) > 2 else fetched.stdout.strip()
    result = call(config['instagram_id'] + '/media', {'media_type': 'REELS', 'video_url': video_url})
    container = result['id']
    print('Test container created. No publish request will be made.', flush=True)
    print('Meta is fetching the test video URL.', flush=True)
    for _ in range(12):
        status = call(container + '?fields=status_code,status')
        print('Processing:', status.get('status_code'), flush=True)
        if status.get('status_code') == 'FINISHED':
            print('Upload and processing verified. Test container left unpublished.')
            break
        if status.get('status_code') in ('ERROR', 'EXPIRED'):
            print(str(status.get('status', '')).replace(token, '[redacted]'))
            raise SystemExit(1)
        time.sleep(10)
    else:
        raise SystemExit('Still processing. No post created.')
