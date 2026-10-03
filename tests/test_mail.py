# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
import logging

from bb import mail


def test_fake_mailer_records_sends():
    m = mail.FakeMailer()
    mid = m.send('sam@example.com', 'hi', 'text body', '<p>html</p>')
    assert mid == 'fake-1'
    assert m.sent == [{'to': 'sam@example.com', 'subject': 'hi',
                       'text': 'text body', 'html': '<p>html</p>', 'reply_to': None}]


def test_log_mailer_logs_the_body(caplog):
    m = mail.LogMailer()
    with caplog.at_level(logging.INFO, logger='blade-book.mail'):
        assert m.send('sam@example.com', 'subj', 'the link is here') == 'log'
    assert 'the link is here' in caplog.text and 'sam@example.com' in caplog.text


def test_from_env_picks_log_without_key_and_resend_with(monkeypatch):
    monkeypatch.delenv('RESEND_API_KEY', raising=False)
    assert isinstance(mail.from_env(), mail.LogMailer)
    monkeypatch.setenv('RESEND_API_KEY', 're_test_123')
    m = mail.from_env()
    assert isinstance(m, mail.ResendMailer)
    assert m.sender.startswith('blade-book <noreply@')


def test_resend_mailer_calls_sdk(monkeypatch):
    calls = []

    class FakeEmails:
        @staticmethod
        def send(params):
            calls.append(params)
            return {'id': 'msg_abc'}
    import resend
    monkeypatch.setattr(resend, 'Emails', FakeEmails)
    m = mail.ResendMailer('re_test', 'blade-book <noreply@example.com>')
    assert m.send('sam@example.com', 'subj', 'txt', '<b>h</b>') == 'msg_abc'
    assert calls == [{'from': 'blade-book <noreply@example.com>',
                      'to': ['sam@example.com'], 'subject': 'subj',
                      'text': 'txt', 'html': '<b>h</b>'}]


def test_magic_link_message_contains_link_and_ttl():
    subject, text, html = mail.magic_link_message('https://x/blade-book/api/auth/magic?t=abc')
    assert 'blade-book' in subject.lower()
    assert 'https://x/blade-book/api/auth/magic?t=abc' in text
    assert 'https://x/blade-book/api/auth/magic?t=abc' in html
    assert '15 minutes' in text
