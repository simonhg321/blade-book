# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import os
import re
import shlex
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel)) as f:
        return f.read()


def test_supervisor_conf_matches_paths_and_port():
    s = _read('deploy/supervisor-blade_book.conf')
    assert '[program:blade_book]' in s
    assert '127.0.0.1:5004' in s and 'app:app' in s
    assert 'directory=/home/shg/blade-book' in s
    for d in ('/etc/blade-book', '/var/lib/blade-book', '/var/log/blade-book'):
        assert d in s
    assert 'billboard' not in s


def test_apache_conf_proxies_only_the_api_prefix():
    s = _read('deploy/apache-blade-book.conf')
    assert 'ProxyPass        /blade-book/api/  http://127.0.0.1:5004/blade-book/api/' in s
    assert 'Alias /blade-book /var/www/html/blade-book' in s
    assert '5003' not in s


def test_apache_confs_cache_control():
    # bundles regenerate ~30 s after a save; stale-cached HTML looks like a bug
    for conf in ('deploy/apache-blade-book.conf', 'deploy/apache-blade-book.com.conf'):
        s = _read(conf)
        assert 'Cache-Control "no-cache"' in s, conf
        assert 'max-age=300' in s, conf
    for f in ('deploy/apache-blade-book.conf', 'deploy/apache-blade-book.com.conf'):
        conf = _read(f)
        assert r'\.tmp(/|$)' in conf, f     # stranded build tmp dirs never served
        assert 'Require all denied' in conf, f


def test_install_and_backup_scripts_are_idempotent_shell():
    inst = _read('scripts/install.sh')
    assert inst.startswith('#!/bin/bash')
    assert 'set -euo pipefail' in inst
    assert 'mkdir -p' in inst and 'supervisorctl' in inst and 'apache2ctl configtest' in inst
    bk = _read('scripts/backup.sh')
    assert '/home/backup' in bk and 'blade-book-' in bk
    assert re.search(r'ls -1t .*\| tail -n \+15 \| xargs', bk)  # keep 14


def test_backup_snapshots_the_db_instead_of_tarring_it_live():
    bk = _read('scripts/backup.sh')
    assert '.backup' in bk and 'mktemp -d' in bk                      # sqlite3 online backup into a staging dir
    assert 'wal_checkpoint' not in bk                                  # superseded by .backup
    for ex in ('blade-book.db*', 'exports', 'publish-locks'):
        assert f'--exclude=' in bk and ex in bk, ex                    # live db + transient dirs never tarred
    assert 'rm -rf "$STAGE"' in bk                                     # staging dir cleaned on every path
    assert "trap 'rm -rf \"$STAGE\"' EXIT" in bk
    assert 'set -euo pipefail' in bk


def test_runbook_move_exists_and_names_the_steps():
    s = _read('docs/RUNBOOK-move.md')
    for word in ('supervisorctl stop blade_book', 'rsync', '/var/lib/blade-book',
                 '/etc/blade-book', 'DNS'):
        assert word in s


def test_landing_has_sign_in_wiring():
    html = open(os.path.join(ROOT, 'html', 'index.html')).read()
    for needle in ("'/blade-book/api/auth'", "'/magic'", "'/me'", "'/providers'",
                   "'/signout'", "'expired'", "'failed'", "'unverified'", "'required'", 'type="email"',
                   # post-send panel: replaces the form, holds the resend button for 60 s
                   'id="sent"', 'id="sent-to"', 'id="resend"', 'RESEND_WAIT = 60', 'if (sending) return;'):
        assert needle in html, needle
    assert 'fonts.googleapis.com' not in html  # billboard vhost CSP blocks it (plan 05 self-hosts)


def test_env_doc_lists_every_key_the_code_reads():
    doc = open(os.path.join(ROOT, 'docs', 'ENV.md')).read()
    for key in ('SESSION_KEY', 'BASE_URL', 'RESEND_API_KEY', 'MAIL_FROM',
                'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'APPLE_CLIENT_ID',
                'APPLE_TEAM_ID', 'APPLE_KEY_ID', 'APPLE_PRIVATE_KEY',
                'ANTHROPIC_API_KEY', 'DECODER_MODEL',
                'BLADEBOOK_PRICE_TEXT', 'BLADEBOOK_CONTACT_EMAIL'):
        assert key in doc, key


def test_install_has_purge_cron():
    sh = open(os.path.join(ROOT, 'scripts', 'install.sh')).read()
    assert 'scripts/purge_drafts.py' in sh and 'purge.log' in sh


def test_install_has_publish_sweep_cron():
    sh = open(os.path.join(ROOT, 'scripts', 'install.sh')).read()
    assert 'scripts/publish_sweep.py' in sh and 'publish.log' in sh
    # first-publish for pre-existing users, run as shg (never root — a
    # root-owned bundle can never be rebuilt by the app again)
    assert 'publish_sweep.py" --all' in sh
    assert 'sudo -u shg' in sh


def test_runbook_move_lists_publish_sweep():
    assert 'scripts/publish_sweep.py' in _read('docs/RUNBOOK-move.md')


def test_install_has_match_cron():
    sh = open(os.path.join(ROOT, 'scripts', 'install.sh')).read()
    assert 'scripts/match_cron.py' in sh and 'match.log' in sh


def test_runbook_move_lists_match_cron():
    assert 'match_cron' in _read('docs/RUNBOOK-move.md')


def _cron_dedupe_chain_and_lines():
    """Pull the real `grep -v '...' | grep -v '...' | ...` de-dupe chain and
    the five `echo '...'` cron lines straight out of install.sh, so this test
    exercises the actual patterns shipped in the script rather than a
    hand-copied approximation of them."""
    sh = _read('scripts/install.sh')
    m = re.search(r"crontab -u shg -l 2>/dev/null((?: \| grep -v '[^']*')+)", sh)
    assert m, 'could not find the crontab de-dupe grep chain in install.sh'
    lines = re.findall(r"echo '([^']*)'", sh)
    assert len(lines) == 5, 'expected exactly 5 cron lines (backup/purge/publish/match/monitor)'
    return m.group(1), lines


def test_install_cron_dedupe_actually_filters_every_added_line():
    """Regression for a reviewer-caught bug: a grep -v pattern that never
    appears as a substring of its own cron line (e.g. the old
    'blade-book/scripts/purge_drafts.py' pattern against a
    `cd /home/shg/blade-book && python3 scripts/purge_drafts.py ...` line,
    which has no 'blade-book/scripts/' substring) makes install.sh append a
    duplicate crontab entry on every rerun. Feed a fake crontab containing
    exactly the five lines install.sh adds through the REAL grep chain
    extracted from the script; every line must come out filtered — the
    property being pinned is: for every cron line install.sh adds, its own
    grep -v pattern matches that line."""
    chain, lines = _cron_dedupe_chain_and_lines()
    fake_crontab = '\n'.join(lines) + '\n'
    cmd = f"printf '%s' {shlex.quote(fake_crontab)} | cat{chain}"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert r.returncode == 1, r.stdout   # grep exits 1 when it filters everything to nothing
    assert r.stdout == '', f'a cron line survived the de-dupe chain: {r.stdout!r}'


def test_runbook_move_covers_the_search_index():
    s = _read('docs/RUNBOOK-move.md')
    assert 'search_cards' in s and 'search_fts' in s
    assert 'derived data' in s


def test_install_ships_the_whole_html_tree():
    sh = open(os.path.join(ROOT, 'scripts', 'install.sh')).read()
    assert 'cp -r "$CODE/html/." /var/www/html/blade-book/' in sh
    assert 'cp "$CODE/html/index.html"' not in sh


def test_runbook_move_lists_both_crontab_lines():
    s = _read('docs/RUNBOOK-move.md')
    assert 'scripts/backup.sh' in s and 'scripts/purge_drafts.py' in s


def test_intake_page_wiring():
    html = open(os.path.join(ROOT, 'html', 'me', 'add', 'index.html')).read()
    for needle in ("'/blade-book/api'", "'/auth/me'", "'/knives/'", "/photos/", "/note",
                   "file.accept = 'image/*'", 'id="file" type="file" multiple', '?replace=1',
                   'ADD PHOTOS TO', 'BOX + KIT + CARD', 'HERO', 'PROCESS',
                   "/decode", 'id="card"', "'confidence'", 'no_card',
                   "'/save'", 'id="save"', "location.href = '/blade-book/me/#'", 'age_months',
                   'older than 12 months', 'Not right? Save, then edit any field in your register.'):
        assert needle in html, needle
    # no capture= attribute: iOS's own Take Photo / Photo Library sheet (as crk/ uses) is
    # what collectors expect, and it is not gated by the vhost's Permissions-Policy camera=()
    assert 'capture' not in html
    assert 'fonts.googleapis.com' not in html
    assert 'innerHTML' not in html
    landing = open(os.path.join(ROOT, 'html', 'index.html')).read()
    assert '/blade-book/me/add/' in landing and 'ADD A KNIFE' in landing
    assert "'/blade-book/api/knives/?status=draft'" in landing


def test_requirements_pin_anthropic():
    assert 'anthropic>=' in _read('requirements.txt')


def test_requirements_and_runbook_mention_pillow_heif():
    assert 'pillow-heif' in _read('requirements.txt')
    assert 'pillow-heif' in _read('docs/RUNBOOK-move.md')


def test_wire_anthropic_script():
    path = os.path.join(ROOT, 'scripts', 'wire_anthropic.sh')
    assert os.path.exists(path)
    s = _read('scripts/wire_anthropic.sh')
    for needle in ('ANTHROPIC_API_KEY', 'DECODER_MODEL', '--break-system-packages',
                   'supervisorctl restart blade_book'):
        assert needle in s, needle
    assert 'echo "$KEY"' not in s and 'echo $KEY' not in s


def test_vibe_fonts_are_self_hosted():
    css = open(os.path.join(ROOT, 'html', 'vibe.css')).read()
    for needle in ("font-family: 'Bebas Neue'", "font-family: 'DM Sans'", "url('/blade-book/fonts/BebasNeue-Regular.woff2')",
                   "url('/blade-book/fonts/DMSans.woff2')", "url('/blade-book/fonts/DMSans-Italic.woff2')",
                   '.bb-display', '.card', '.dot.high', '.badge', '--cream'):
        assert needle in css, needle
    for f in ('BebasNeue-Regular.woff2', 'DMSans.woff2', 'DMSans-Italic.woff2'):
        p = os.path.join(ROOT, 'html', 'fonts', f)
        assert os.path.getsize(p) > 10_000, f
        assert open(p, 'rb').read(4) == b'wOF2', f
    assert 'Open Font License' in open(os.path.join(ROOT, 'html', 'fonts', 'OFL.txt')).read()
    for dirpath, _, files in os.walk(os.path.join(ROOT, 'html')):
        for f in files:
            if f.endswith(('.html', '.css', '.js')):
                body = open(os.path.join(dirpath, f)).read()
                assert 'fonts.googleapis.com' not in body and 'fonts.gstatic.com' not in body, f


def test_brandmark_assets_and_wiring():
    svg = open(os.path.join(ROOT, 'html', 'mark.svg')).read()
    for needle in ('<svg', '#1a1a1a', '#b8452c', 'M143 30'):
        assert needle in svg, needle
    png = open(os.path.join(ROOT, 'html', 'apple-touch-icon.png'), 'rb').read()
    assert png[:4] == b'\x89PNG' and len(png) > 1000
    for rel in ('index.html', 'me/index.html', 'me/add/index.html'):
        html = open(os.path.join(ROOT, 'html', rel)).read()
        assert '/blade-book/mark.svg' in html, rel
        assert '/blade-book/apple-touch-icon.png' in html, rel
        assert 'bbmark' in html, rel


def test_how_page_wiring():
    html = open(os.path.join(ROOT, 'html', 'how', 'index.html')).read()
    for needle in ('HOW IT WORKS', 'birth card', 'PROCESS', 'The one shot',
                   '/blade-book/me/add/', '/blade-book/@simon-collector/', 'bbmark',
                   '/blade-book/how/example.jpg',  # real flat-lay example (watermarked, EXIF-free)
                   'href="/blade-book/vibe.css"'):
        assert needle in html, needle
    assert 'innerHTML' not in html and 'fonts.googleapis.com' not in html
    for rel in ('index.html', 'me/index.html', 'me/add/index.html'):
        assert '/blade-book/how/' in open(os.path.join(ROOT, 'html', rel)).read(), rel


def test_landing_links_the_register():
    html = open(os.path.join(ROOT, 'html', 'index.html')).read()
    for needle in ('href="/blade-book/vibe.css"', 'href="/blade-book/me/"', "/api/knives/?status=live'", 'id="regcount"'):
        assert needle in html, needle


def test_register_page_wiring():
    html = open(os.path.join(ROOT, 'html', 'me', 'index.html')).read()
    for needle in ("'/knives/full'", "'/auth/me'", "'/auth/signout'", "'/decode'", "'/sale'", "'/public'", "'/knives/bulk'",
                   "json('PATCH'", "method: 'DELETE'", 'href="/blade-book/vibe.css"', 'href="/blade-book/me/add/"',
                   'id="q"', 'id="cards"', 'id="bulkbar"', 'id="tpl"', 'class="bb-display"',
                   "location.href = '/blade-book/'", 'prompt(', 'FIELDS = [', 'sale_status', 'is_public',
                   'window.scrollTo(0, y)',  # re-render must not send the reader back to the top
                   '<details class="more">', 'openMap',  # compact cards: details collapsed, state survives re-render
                   'hero_photo', 'notes_public', 'notes_private', 'price_paid', 'events'):
        assert needle in html, needle
    assert 'innerHTML' not in html
    assert 'fonts.googleapis.com' not in html


def test_register_page_hero_pin_wiring():
    html = _read('html/me/index.html')
    for needle in ('featured_knife_id: pinned ? null : k.id', 'register hero', '★ HERO',
                   'not public', 'is_public && !k.hidden_at'):
        assert needle in html, needle
    assert 'innerHTML' not in html


def test_register_page_has_public_section():
    html = open(os.path.join(ROOT, 'html', 'me', 'index.html')).read()
    for needle in ("'/settings'", 'id="pubsec"', 'id="publink"', 'id="hideday"',
                   'id="privprof"', 'id="pagekey"', 'has_key', 'public_url',
                   'YOUR PUBLIC PAGE'):
        assert needle in html, needle
    assert 'innerHTML' not in html


def test_landing_links_public_page():
    html = open(os.path.join(ROOT, 'html', 'index.html')).read()
    assert 'id="publink"' in html and "'/blade-book/@'" in html


def test_search_page_wiring():
    html = open(os.path.join(ROOT, 'html', 'search', 'index.html')).read()
    for needle in ('SEARCH THE REGISTERS', "'/blade-book/api/search", 'id="q"',
                   'id="results"', 'id="aggs"', 'id="filters"', 'id="owners"',
                   'early-access', 'bbmark', '/blade-book/mark.svg',
                   'href="/blade-book/vibe.css"', 'debounce', '402'):
        assert needle in html, needle
    assert 'innerHTML' not in html and 'fonts.googleapis.com' not in html
    for rel in ('index.html', 'how/index.html'):
        assert '/blade-book/search/' in open(os.path.join(ROOT, 'html', rel)).read(), rel


def test_wants_page_wiring():
    html = open(os.path.join(ROOT, 'html', 'me', 'wants', 'index.html')).read()
    for needle in ('YOUR WANTS', "'/blade-book/api/wants", "'/auth/me'", 'id="wlist"',
                   'id="wform"', 'trade', 'sale', 'either', 'max_price', 'keyword',
                   'born_from', 'born_to', 'share_email_on_intro', "'/settings'",
                   'bbmark', '/blade-book/mark.svg', 'href="/blade-book/vibe.css"',
                   'too many wants', 'href="/blade-book/me/"'):
        assert needle in html, needle
    assert 'innerHTML' not in html and 'fonts.googleapis.com' not in html
    assert '/blade-book/me/wants/' in open(os.path.join(ROOT, 'html', 'me', 'index.html')).read()


def test_board_page_wiring():
    html = open(os.path.join(ROOT, 'html', 'board', 'index.html')).read()
    for needle in ('THE BOARD', "'/blade-book/api/board'", "'/auth/me'", 'id="cards"', 'id="more"',
                   'id="tpl"', 'id="cdlg"', 'id="rdlg"', '/contact', '/report', 'contact seller', 'report',
                   'maxlength="500"', '429', '502', 'sign in to contact', 'bbmark', '/blade-book/mark.svg',
                   'href="/blade-book/vibe.css"', 'seller_note', 'asking_price', 'listed_at',
                   "'/blade-book/@' + k.handle + '/img/' + k.img_t"):
        assert needle in html, needle
    assert 'innerHTML' not in html and 'fonts.googleapis.com' not in html
    for rel in ('index.html', 'search/index.html'):
        assert '/blade-book/board/' in open(os.path.join(ROOT, 'html', rel)).read(), rel


def test_landing_board_strip_and_admin_link():
    html = open(os.path.join(ROOT, 'html', 'index.html')).read()
    for needle in ('id="board"', 'id="bcards"', "'/blade-book/api/board?limit=6'", 'href="/blade-book/board/"',
                   'ON THE BOARD', 'id="adminlink"', 'is_admin', 'href="/blade-book/admin/"',
                   "fetch('/blade-book/api/board?limit=6')"):
        assert needle in html, needle
    # the strip renders via createElement — the only innerHTML on the landing page is the
    # pre-existing OIDC provider button (plan 02), which is a trusted literal
    assert html.count('innerHTML') == 1


def test_admin_page_wiring():
    html = open(os.path.join(ROOT, 'html', 'admin', 'index.html')).read()
    for needle in ('REPORTS', "'/blade-book/api/admin'", "'/auth/me'", 'is_admin', "'hide'", "'restore'", "'delete'",
                   "'/knives/' + knifeId + '/' + action", 'prompt(', 'confirm(', 'id="queue"', 'reporter_handle', 'owner_handle', 'hidden_at', 'bbmark',
                   'href="/blade-book/vibe.css"', 'noindex'):
        assert needle in html, needle
    assert 'innerHTML' not in html and 'fonts.googleapis.com' not in html


def test_register_page_shows_hidden_badge():
    html = open(os.path.join(ROOT, 'html', 'me', 'index.html')).read()
    assert 'hidden_at' in html and 'under review' in html


def test_runbook_move_mentions_reports_and_hidden():
    s = _read('docs/RUNBOOK-move.md')
    assert 'reports' in s and 'hidden_at' in s


def test_landing_says_one_shot():
    html = open(os.path.join(ROOT, 'html', 'index.html')).read()
    assert 'How it works — the one shot →' in html and 'three shots' not in html


def test_intake_page_gate_wiring():
    html = open(os.path.join(ROOT, 'html', 'me', 'add', 'index.html')).read()
    for needle in ("'/billing'", 'free_old_left', 'account_free_days_left', 'id="paywall"', '<dialog',
                   'res.status === 402', 'res.j.price', 'res.j.contact', 'EARLY ACCESS', "'mailto:' +",
                   'older than 12 months', 'free older-knife save', 'needs a subscription'):
        assert needle in html, needle
    assert 'Free while we are in early access' not in html
    assert '@blade-book' not in html          # the address comes from the API, never the page
    assert 'innerHTML' not in html


def test_search_page_shows_the_price_on_402():
    html = open(os.path.join(ROOT, 'html', 'search', 'index.html')).read()
    for needle in ("'/blade-book/api/billing'", 'b.price', 'b.contact', 'EARLY ACCESS', "'mailto:' +", '402'):
        assert needle in html, needle
    assert '@blade-book' not in html and 'innerHTML' not in html


def test_admin_page_users_wiring():
    html = open(os.path.join(ROOT, 'html', 'admin', 'index.html')).read()
    for needle in ('USERS', "'/users'", "'/users/' + userId + '/sub'", "'active'", "'free'", "'lapsed'",
                   'free_old_used', 'sub_status', 'id="users"', 'knives', 'verified_at'):
        assert needle in html, needle
    assert 'innerHTML' not in html


def test_install_has_monitor_cron():
    sh = _read('scripts/install.sh')
    assert 'scripts/monitor.py' in sh and 'monitor.log' in sh and "grep -v 'scripts/monitor.py'" in sh


def test_env_doc_and_runbook_mention_monitor():
    assert 'BLADEBOOK_ADMIN_EMAIL' in _read('docs/ENV.md')
    rb = _read('docs/RUNBOOK-move.md')
    assert 'scripts/monitor.py' in rb and 'monitor_state.json' in rb
