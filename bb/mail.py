# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/mail.py — the Mailer seam (spec §11). One interface, three impls:
FakeMailer (tests), LogMailer (no RESEND_API_KEY — the link lands in
app.log, which is how sign-in works on stark before Resend is wired), and
ResendMailer (prod). Only transactional mail ever goes through here.
"""
import logging

from bb import config

log = logging.getLogger('blade-book.mail')

DEFAULT_FROM = 'blade-book <noreply@blade-book.com>'


class Mailer:
    def send(self, to, subject, text, html=None, reply_to=None):  # -> message id
        raise NotImplementedError


class FakeMailer(Mailer):
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text, html=None, reply_to=None):
        self.sent.append({'to': to, 'subject': subject, 'text': text, 'html': html,
                          'reply_to': reply_to})
        return f'fake-{len(self.sent)}'


class LogMailer(Mailer):
    def send(self, to, subject, text, html=None, reply_to=None):
        log.info('MAIL (not sent — no RESEND_API_KEY) to=%s subject=%r\n%s',
                 to, subject, text)
        return 'log'


class ResendMailer(Mailer):
    def __init__(self, api_key, sender):
        import resend  # imported here so tests without the SDK still load bb.mail
        resend.api_key = api_key
        self._resend = resend
        self.sender = sender

    def send(self, to, subject, text, html=None, reply_to=None):
        params = {'from': self.sender, 'to': [to], 'subject': subject, 'text': text}
        if html:
            params['html'] = html
        if reply_to:
            params['reply_to'] = [reply_to]
        r = self._resend.Emails.send(params)
        mid = r.get('id') if isinstance(r, dict) else getattr(r, 'id', None)
        log.info('MAIL sent to=%s subject=%r id=%s', to, subject, mid)
        return mid


def from_env():
    key = config.get('RESEND_API_KEY')
    if key:
        return ResendMailer(key, config.get('MAIL_FROM', DEFAULT_FROM))
    return LogMailer()


def magic_link_message(link):
    subject = 'Your blade-book sign-in link'
    text = (f'Click to sign in to blade-book:\n\n{link}\n\n'
            'The link works once and expires in 15 minutes. '
            'If you did not request it, ignore this email.\n')
    html = (f'<p>Click to sign in to blade-book:</p>'
            f'<p><a href="{link}">{link}</a></p>'
            f'<p style="color:#666">The link works once and expires in 15 minutes. '
            f'If you did not request it, ignore this email.</p>')
    return subject, text, html
