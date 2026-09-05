# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import os

from bb import cdn


class _Resp:
    def __init__(self, status=200, ok=True):
        self.status_code = status
        self._ok = ok

    def json(self):
        return {'success': self._ok, 'errors': [] if self._ok else [{'message': 'nope'}]}


def _capture(monkeypatch, status=200, ok=True, raise_exc=None):
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        if raise_exc:
            raise raise_exc
        calls.append({'url': url, 'json': json, 'headers': headers, 'timeout': timeout})
        return _Resp(status, ok)
    monkeypatch.setattr(cdn.requests, 'post', post)
    return calls


def test_disabled_without_env(monkeypatch):
    monkeypatch.delenv('CF_API_TOKEN', raising=False)
    monkeypatch.delenv('CF_ZONE_ID', raising=False)
    calls = _capture(monkeypatch)
    assert cdn.enabled() is False
    assert cdn.purge_urls(['https://blade-book.com/blade-book/@sam/index.html']) == 0
    assert calls == []


def test_purges_in_batches_of_30_with_bearer(monkeypatch):
    monkeypatch.setenv('CF_API_TOKEN', 'cf-test-token')
    monkeypatch.setenv('CF_ZONE_ID', 'zone123')
    calls = _capture(monkeypatch)
    urls = [f'https://blade-book.com/blade-book/@sam/img/K{i:02d}.jpg' for i in range(65)]
    assert cdn.purge_urls(urls) == 65
    assert [len(c['json']['files']) for c in calls] == [30, 30, 5]
    assert calls[0]['url'] == 'https://api.cloudflare.com/client/v4/zones/zone123/purge_cache'
    assert calls[0]['headers']['Authorization'] == 'Bearer cf-test-token'
    assert calls[0]['timeout'] == 10


def test_failures_are_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setenv('CF_API_TOKEN', 't')
    monkeypatch.setenv('CF_ZONE_ID', 'z')
    _capture(monkeypatch, raise_exc=ConnectionError('down'))
    assert cdn.purge_urls(['https://blade-book.com/x']) == 0
    assert 'purge failed' in caplog.text
    _capture(monkeypatch, status=403, ok=False)
    assert cdn.purge_urls(['https://blade-book.com/x']) == 0
    assert '403' in caplog.text


def test_public_url_and_bundle_files(tmp_path, monkeypatch):
    monkeypatch.setenv('BASE_URL', 'https://blade-book.com')
    from bb import config
    config.load()
    assert cdn.public_url('sam', 'img/K01.jpg') == 'https://blade-book.com/blade-book/@sam/img/K01.jpg'
    os.makedirs(tmp_path / 'img')
    (tmp_path / 'index.html').write_text('x')
    (tmp_path / 'img' / 'K01.jpg').write_bytes(b'x')
    (tmp_path / 'K01').mkdir()
    (tmp_path / 'K01' / 'index.html').write_text('x')
    assert sorted(cdn.bundle_files(str(tmp_path))) == ['K01/index.html', 'img/K01.jpg', 'index.html']
    assert cdn.bundle_files(str(tmp_path / 'missing')) == []
