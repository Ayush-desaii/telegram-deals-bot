"""Read-only connection diagnosis; never prints stored credentials."""
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main():
    config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    secrets = [str(v) for k, v in config.items() if 'token' in k or 'secret' in k]
    def clean(value):
        text = str(value)
        for secret in secrets:
            if secret:
                text = text.replace(secret, '[redacted]')
        return re.sub(r'https?://\S+', '[URL]', text)[:700]
    print('Configured API version:', clean(config.get('api_version')))
    endpoints = [
        ('Telegram identity', 'https://api.telegram.org/bot' + config['telegram_token'] + '/getMe', {}),
        ('Instagram configured account', 'https://graph.instagram.com/' + config['api_version'] + '/' + config['instagram_id'] + '?fields=id,username', {'Authorization': 'Bearer ' + config['instagram_token']}),
        ('Instagram token identity', 'https://graph.instagram.com/' + config['api_version'] + '/me?fields=id,user_id,username', {'Authorization': 'Bearer ' + config['instagram_token']}),
    ]
    for label, url, headers in endpoints:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
                body = json.load(response)
            print(label + ': OK')
            if label.startswith('Instagram'):
                print('ID matches configured:', str(config['instagram_id']) in [str(body.get('id')), str(body.get('user_id'))])
        except urllib.error.HTTPError as exc:
            try:
                body = json.load(exc)
                error = body.get('error', {})
                if not isinstance(error, dict):
                    error = {}
                print(label, 'HTTP', exc.code, 'code', error.get('code'), 'subcode', error.get('error_subcode'), clean(error.get('message') or body.get('description') or 'No details'))
            except ValueError:
                print(label, 'HTTP', exc.code)
        except OSError:
            print(label + ': NETWORK FAILURE')


if __name__ == '__main__':
    main()
