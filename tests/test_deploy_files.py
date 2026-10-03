# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
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
        assert re.search(r'Cache-Control "no-cache(, no-store, must-revalidate)?"', s), conf
        assert 'max-age=300' in s, conf
    for f in ('deploy/apache-blade-book.conf', 'deploy/apache-blade-book.com.conf'):
        conf = _read(f)
        assert r'\.tmp(/|$)' in conf, f     # stranded build tmp dirs never served
        assert 'Require all denied' in conf, f


def test_dotcom_vhost_has_header_parity_with_the_alias():
    """Security review 2026-09-04 M4/L18: the .com vhost goes live on domain
    night with the same hardening the alias already sends, no Google Fonts
    hosts (fonts are self-hosted), no-store HTML, and www → apex."""
    s = _read('deploy/apache-blade-book.com.conf')
    for h in ('Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"',
              'X-Content-Type-Options "nosniff"', 'X-Frame-Options "DENY"',
              'Permissions-Policy "camera=(), microphone=(), geolocation=()"', 'X-XSS-Protection "0"',
              'Referrer-Policy "strict-origin-when-cross-origin"', "frame-ancestors 'none'",
              'no-cache, no-store, must-revalidate', 'RewriteCond %{HTTP_HOST} ^www\\.', 'https://blade-book.com%{REQUEST_URI}'):
        assert h in s, h
    assert 'fonts.googleapis.com' not in s and 'fonts.gstatic.com' not in s
    assert '127.0.0.1:5003' not in s


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
    assert 'BLADEBOOK_BACKUP_DIR' in bk                                # overridable so a test can execute it
    assert 'gzip -t "$OUT"' in bk                                      # a truncated/corrupt tarball fails the check
    assert 'OK=1' in bk
    assert 'rm -f "$OUT"' in bk                                        # a failed run (set -e, before OK=1) removes the partial tarball
    assert re.search(r"trap '.*rm -f \"\$OUT\"' EXIT", bk)
    assert 'set -euo pipefail' in bk


def test_backup_script_runs_and_removes_partial_on_failure(tmp_path):
    import subprocess
    data = tmp_path / 'blade-book'
    (data / 'photos').mkdir(parents=True)
    (data / 'exports').mkdir()
    (data / 'publish-locks').mkdir()
    (data / 'photos' / 'a.jpg').write_bytes(b'x')
    subprocess.run(['sqlite3', str(data / 'blade-book.db'), 'create table t(x)'], check=True)
    bk = tmp_path / 'backup'
    bk.mkdir()
    env = dict(os.environ, BLADEBOOK_DATA_DIR=str(data), BLADEBOOK_BACKUP_DIR=str(bk))
    r = subprocess.run(['bash', os.path.join(ROOT, 'scripts', 'backup.sh')], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    tgz = list(bk.glob('blade-book-*.tgz'))
    assert len(tgz) == 1 and not list(bk.glob('.stage.*'))
    names = subprocess.run(['tar', 'tzf', str(tgz[0])], capture_output=True, text=True, check=True).stdout.split()
    assert 'blade-book.db' in names and 'blade-book/photos/a.jpg' in names
    assert not any(n.startswith('blade-book/blade-book.db') or '/exports' in n or '/publish-locks' in n for n in names)
    # failure path: unreadable data dir → non-zero exit, no tarball left behind, no stage dir
    for f in tgz:
        f.unlink()
    env['BLADEBOOK_DATA_DIR'] = str(tmp_path / 'missing')
    r = subprocess.run(['bash', os.path.join(ROOT, 'scripts', 'backup.sh')], env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert not list(bk.glob('blade-book-*.tgz')) and not list(bk.glob('.stage.*'))


def _backup_with_a_tar_that_exits(tmp_path, code, note):
    """Run scripts/backup.sh with a `tar` that does the real work and then
    exits `code`, the way GNU tar does when the data dir is written to while
    it reads (1) or when it fails (2)."""
    import subprocess
    data = tmp_path / 'blade-book'
    (data / 'photos').mkdir(parents=True)
    (data / 'photos' / 'a.jpg').write_bytes(b'x')
    subprocess.run(['sqlite3', str(data / 'blade-book.db'), 'create table t(x)'], check=True)
    bk = tmp_path / 'backup'
    bk.mkdir()
    fake = tmp_path / 'bin'
    fake.mkdir()
    (fake / 'tar').write_text(f'#!/bin/bash\n/bin/tar "$@"\necho "tar: {note}" >&2\nexit {code}\n')
    (fake / 'tar').chmod(0o755)
    env = dict(os.environ, BLADEBOOK_DATA_DIR=str(data), BLADEBOOK_BACKUP_DIR=str(bk),
               PATH=f'{fake}:{os.environ["PATH"]}')
    r = subprocess.run(['bash', os.path.join(ROOT, 'scripts', 'backup.sh')], env=env, capture_output=True, text=True)
    return r, bk


def test_backup_survives_a_data_dir_that_changed_while_it_was_read(tmp_path):
    # 2026-09-04 to 09-28: no tarball at all. 03:30 is a cron minute; the monitor writes its state in the data dir,
    # tar says "file changed as we read it" and exits 1, set -e stopped the run and the trap deleted the tarball
    import subprocess
    r, bk = _backup_with_a_tar_that_exits(tmp_path, 1, 'blade-book: file changed as we read it')
    assert r.returncode == 0, r.stderr
    tgz = list(bk.glob('blade-book-*.tgz'))
    assert len(tgz) == 1 and not list(bk.glob('.stage.*'))
    names = subprocess.run(['/bin/tar', 'tzf', str(tgz[0])], capture_output=True, text=True, check=True).stdout.split()
    assert 'blade-book.db' in names and 'blade-book/photos/a.jpg' in names
    assert 'wrote ' in r.stdout and 'changed while it was read' in r.stdout      # kept, and the log says why tar complained


def test_backup_still_fails_when_tar_fails(tmp_path):
    r, bk = _backup_with_a_tar_that_exits(tmp_path, 2, 'Exiting with failure status due to previous errors')
    assert r.returncode != 0
    assert not list(bk.glob('blade-book-*.tgz')) and not list(bk.glob('.stage.*'))
    assert 'wrote ' not in r.stdout


def test_runbook_move_exists_and_names_the_steps():
    s = _read('docs/RUNBOOK-move.md')
    for word in ('supervisorctl stop blade_book', 'rsync', '/var/lib/blade-book',
                 '/etc/blade-book', 'DNS'):
        assert word in s


def test_landing_has_sign_in_wiring():
    html = open(os.path.join(ROOT, 'html', 'index.html')).read()
    for needle in ("'/blade-book/api/auth'", "'/magic'", "'/me'", "'/providers'",
                   "'expired'", "'failed'", "'unverified'", "'required'", 'type="email"',
                   # post-send panel: replaces the form, holds the resend button for 60 s
                   'id="sent"', 'id="sent-to"', 'id="resend"', 'RESEND_WAIT = 60', 'if (sending) return;'):
        assert needle in html, needle
    assert 'fonts.googleapis.com' not in html  # billboard vhost CSP blocks it (plan 05 self-hosts)
    # sign-out itself moved to nav.js (plan 13, single source) — covered by test_nav_js_shape


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
    the six `echo '...'` cron lines straight out of install.sh, so this test
    exercises the actual patterns shipped in the script rather than a
    hand-copied approximation of them."""
    sh = _read('scripts/install.sh')
    m = re.search(r"crontab -u shg -l 2>/dev/null((?: \| grep -v '[^']*')+)", sh)
    assert m, 'could not find the crontab de-dupe grep chain in install.sh'
    lines = re.findall(r"echo '([^']*)'", sh)
    assert len(lines) == 6, 'expected exactly 6 cron lines (backup/purge/publish/match/monitor/rotate_log)'
    return m.group(1), lines


def test_install_cron_dedupe_actually_filters_every_added_line():
    """Regression for a reviewer-caught bug: a grep -v pattern that never
    appears as a substring of its own cron line (e.g. the old
    'blade-book/scripts/purge_drafts.py' pattern against a
    `cd /home/shg/blade-book && python3 scripts/purge_drafts.py ...` line,
    which has no 'blade-book/scripts/' substring) makes install.sh append a
    duplicate crontab entry on every rerun. Feed a fake crontab containing
    exactly the lines install.sh adds through the REAL grep chain
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
                   'older than 12 months', 'Not right? Save, then edit any field in your register.',
                   'id="guess"', 'No card read', 'best guess', 'id="lookup"', 'res.j.lookup', 'rel = \'noopener\'', "'/lookup'", 'pollLookup'):
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
                   'href="/blade-book/vibe.css?v=20260927"'):
        assert needle in html, needle
    assert 'innerHTML' not in html and 'fonts.googleapis.com' not in html
    # me/index.html's shared app header row deliberately has no 'how' link (plan 13)
    for rel in ('index.html', 'me/add/index.html'):
        assert '/blade-book/how/' in open(os.path.join(ROOT, 'html', rel)).read(), rel


def test_blank_card_page_wiring():
    """Simon 2026-09-03: the printable blank birth card (from the OSS bladebook
    repo) lives under the product too, and is linked where a person needs it."""
    html = open(os.path.join(ROOT, 'html', 'card', 'index.html')).read()
    for needle in ('BIRTH CARD', 'not a certificate of authenticity', 'id="print"', 'window.print()',
                   '@media print', 'class="bb-foot"', 'href="/blade-book/vibe.css?v=20260927"',
                   'src="/blade-book/nav.js?v=20260927"', 'href="/blade-book/how/"',
                   'Damascus smith', 'Notes / story'):
        assert needle in html, needle
    assert 'innerHTML' not in html and 'onclick=' not in html and 'fonts.googleapis.com' not in html
    for rel in ('how/index.html', 'me/add/index.html', 'about/index.html'):
        page = open(os.path.join(ROOT, 'html', rel)).read()
        assert 'href="/blade-book/card/"' in page, rel


def test_landing_links_the_register():
    html = open(os.path.join(ROOT, 'html', 'index.html')).read()
    for needle in ('href="/blade-book/vibe.css?v=20260927"', 'href="/blade-book/me/"', "/api/knives/?status=live'", 'id="regcount"'):
        assert needle in html, needle


def test_register_page_wiring():
    html = open(os.path.join(ROOT, 'html', 'me', 'index.html')).read()
    for needle in ("'/knives/full'", "'/auth/me'", "'/decode'", "'/sale'", "'/public'", "'/knives/bulk'",
                   "json('PATCH'", "method: 'DELETE'", 'href="/blade-book/vibe.css?v=20260927"', 'href="/blade-book/me/add/"',
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
    for needle in ('featured_knife_id: pinned ? null : k.id', 'face of my register', '★ REGISTER FACE',
                   'not public', 'is_public && !k.hidden_at'):
        assert needle in html, needle
    assert 'innerHTML' not in html


def test_register_face_pin_confirms_first():
    # 2026-09-26: a stray tap on the card's ★ swapped Simon's whole register face to a
    # Spyderco box label, silently. The pin must go through a dialog showing the photo.
    html = _read('html/me/index.html')
    for needle in ('<dialog id="facedlg"', "$('facedlg').showModal()", 'id="faceok"',
                   'id="facecancel"', 'id="facebar"'):
        assert needle in html, needle
    # the button itself never PATCHes — only the dialog's confirm does
    btn = html.split("var heroBtn = art.querySelector('.facebtn');", 1)[1].split('var form =', 1)[0]
    assert "json('PATCH'" not in btn
    # the root cause: the cover <img> also carries class "hero", so querySelector('.hero')
    # grabbed the PHOTO — tapping a cover pinned the knife and the real button never showed
    assert "querySelector('.hero')" not in html
    assert 'class="btn alt facebtn"' in html
    # ★ means the register face only — covers use a different mark
    assert '★ choose covers' not in html


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
                   'href="/blade-book/vibe.css?v=20260927"', 'debounce', '402'):
        assert needle in html, needle
    assert 'innerHTML' not in html and 'fonts.googleapis.com' not in html
    for rel in ('index.html', 'how/index.html'):
        assert '/blade-book/search/' in open(os.path.join(ROOT, 'html', rel)).read(), rel


def test_wants_page_wiring():
    html = open(os.path.join(ROOT, 'html', 'me', 'wants', 'index.html')).read()
    for needle in ('YOUR WANTS', "'/blade-book/api/wants", "'/auth/me'", 'id="wlist"',
                   'id="wform"', 'trade', 'sale', 'either', 'max_price', 'keyword',
                   'born_from', 'born_to', 'share_email_on_intro', "'/settings'",
                   'bbmark', '/blade-book/mark.svg', 'href="/blade-book/vibe.css?v=20260927"',
                   'too many wants', 'href="/blade-book/me/"'):
        assert needle in html, needle
    assert 'innerHTML' not in html and 'fonts.googleapis.com' not in html
    assert '/blade-book/me/wants/' in open(os.path.join(ROOT, 'html', 'me', 'index.html')).read()


def test_board_page_wiring():
    html = open(os.path.join(ROOT, 'html', 'board', 'index.html')).read()
    for needle in ('THE BOARD', "'/blade-book/api/board'", "'/auth/me'", 'id="cards"', 'id="more"',
                   'id="tpl"', 'id="cdlg"', 'id="rdlg"', '/contact', '/report', 'contact seller', 'report',
                   'maxlength="500"', '429', '502', 'sign in to contact', 'bbmark', '/blade-book/mark.svg',
                   'href="/blade-book/vibe.css?v=20260927"', 'seller_note', 'asking_price', 'listed_at',
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
    assert 'innerHTML' not in html


def test_admin_page_wiring():
    html = open(os.path.join(ROOT, 'html', 'admin', 'index.html')).read()
    for needle in ('REPORTS', "'/blade-book/api/admin'", "'/auth/me'", 'is_admin', "'hide'", "'restore'", "'delete'",
                   "'/knives/' + knifeId + '/' + action", 'prompt(', 'confirm(', 'id="queue"', 'reporter_handle', 'owner_handle', 'hidden_at', 'bbmark',
                   'href="/blade-book/vibe.css?v=20260927"', 'noindex'):
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
                   'older than 12 months', 'free older-knife save', 'needs a subscription',
                   'hard_gate', 'Free during early access'):
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
    assert 'scripts/monitor.py' in sh and 'monitor.log' in sh
    assert "grep -v 'blade-book/scripts/monitor.py'" in sh   # anchored: a sibling project's own scripts/monitor.py must not be swept up
    assert 'python3 /home/shg/blade-book/scripts/monitor.py' in sh   # absolute path, matches the anchored grep


def test_env_doc_and_runbook_mention_monitor():
    assert 'BLADEBOOK_ADMIN_EMAIL' in _read('docs/ENV.md')
    rb = _read('docs/RUNBOOK-move.md')
    assert 'scripts/monitor.py' in rb and 'monitor_state.json' in rb


def test_about_page_wiring():
    html = _read('html/about/index.html')
    for needle in ('ABOUT BLADE-BOOK', 'WHAT IT IS', "WHAT IT ISN'T", 'PRICE', 'THE RULES', 'WHO',
                   'certificate of authenticity', 'id="price"', 'id="contact"', "'/blade-book/api/billing'",
                   'href="/blade-book/terms/"', 'href="/blade-book/how/"', 'href="/blade-book/search/"',
                   'property="og:title"', 'href="/blade-book/vibe.css?v=20260927"', 'bbmark', '/blade-book/mark.svg'):
        assert needle in html, needle
    assert 'innerHTML' not in html and 'fonts.googleapis.com' not in html
    assert '<!-- Copyright (c) 2026 Simon SGH' in html
    assert '$4' not in html and '$36' not in html and 'hello@' not in html   # price + contact come from the API
    for rel in ('index.html', 'how/index.html'):
        assert 'href="/blade-book/about/"' in _read('html/' + rel), rel


def test_terms_page_wiring():
    html = _read('html/terms/index.html')
    for needle in ('TERMS', 'A record, not a certificate', 'No money', 'Your photos stay yours', 'Takedown',
                   'Delete is real', 'Your data', 'What we store', 'Early access', '24 hours',
                   'class="contact"', "'/blade-book/api/billing'", 'href="/blade-book/about/"',
                   'property="og:title"', 'href="/blade-book/vibe.css?v=20260927"', 'bbmark'):
        assert needle in html, needle
    assert 'innerHTML' not in html and 'fonts.googleapis.com' not in html
    assert '<!-- Copyright (c) 2026 Simon SGH' in html
    assert 'hello@' not in html
    prose = re.sub(r'<(style|script)[\s\S]*?</\1>', ' ', html)
    assert len(re.findall(r'\w+', re.sub(r'<[^>]+>', ' ', prose))) < 900   # plain English, short
    landing = _read('html/index.html')
    assert 'By signing in you agree to the <a href="/blade-book/terms/"' in landing
def test_settings_page_wiring():
    html = _read('html/me/settings/index.html')
    for needle in ('SETTINGS', "'/settings'", 'href="/blade-book/api/settings/export"', "'/settings/delete'",
                   "'/auth/signout-all'", "'/billing'", "'/auth/me'",
                   'id="handle"', 'id="newhandle"', 'id="changehandle"', 'can_change_handle',
                   'id="email"', 'id="since"', 'id="sub"', 'id="subcard"', 'id="subprice"', 'id="submail"',
                   'id="signoutall"', 'id="export"', 'id="confirm"', 'id="delete"',
                   'one change, ever', 'real and complete', 'type your handle',
                   "location.href = '/blade-book/?deleted=1'", 'href="/blade-book/me/"',
                   'href="/blade-book/vibe.css?v=20260927"', 'bbmark', '/blade-book/mark.svg', 'class="bb-display"'):
        assert needle in html, needle
    assert 'innerHTML' not in html and 'fonts.googleapis.com' not in html
    assert '<!-- Copyright (c) 2026 Simon SGH' in html


def test_register_nav_links_settings():
    html = _read('html/me/index.html')
    assert 'href="/blade-book/me/settings/"' in html


def test_nav_js_shape():
    js = _read('html/nav.js')
    assert js.startswith('// Copyright (c) 2026 Simon SGH')
    for needle in ("'/blade-book/api/auth/me'", "'a.bb-auth'", "'my register'", 'adminlink', 'is_admin',
                   'signout', "'/blade-book/api/auth/signout'", "location.href = '/blade-book/'",
                   "credentials: 'same-origin'", "cache: 'no-store'", '.catch('):
        assert needle in js, needle
    assert 'innerHTML' not in js and 'eval(' not in js


def test_vibe_css_has_shared_nav_rules():
    css = _read('html/vibe.css')
    for sel in ('.bb-foot{', '.bb-foot a{', '.bb-head{', '.bb-head h1{', '.bb-nav{', '.bb-nav a{', '.bb-nav a.add{'):
        assert sel in css, sel


PUBLIC_PAGES = ('index.html', 'board/index.html', 'search/index.html', 'how/index.html',
                'faq/index.html', 'about/index.html', 'terms/index.html')
STRIP_LINKS = ('href="/blade-book/search/">search<', 'href="/blade-book/board/">board<', 'href="/blade-book/how/">how<',
               'href="/blade-book/faq/">faq<',
               'href="/blade-book/about/">about<', 'href="/blade-book/terms/">terms<',
               'class="bb-auth" href="/blade-book/me/">sign in<')


def test_public_pages_share_the_footer_strip():
    for rel in PUBLIC_PAGES:
        html = _read('html/' + rel)
        assert html.count('class="bb-foot"') == 1, rel
        foot = html[html.index('class="bb-foot"'):html.index('</footer>')]
        pos = [foot.index(n) for n in STRIP_LINKS]
        assert pos == sorted(pos), (rel, pos)                              # fixed order
        assert '<script src="/blade-book/nav.js?v=20260927" defer></script>' in html[html.index('</footer>'):], rel
        assert html.count('<footer') == 1, rel                              # the strip is the only footer
        assert 'innerHTML' not in html, rel
    assert 'the board — knives for sale' not in _read('html/search/index.html')
    assert 'search the registers' not in _read('html/board/index.html')
    assert 'search the registers' not in _read('html/index.html')
    assert 'about</a> · <a' not in _read('html/index.html').split('class="bb-foot"')[0]   # the old about · terms cluster is gone
    assert "' knives'" in _read('html/search/index.html') and 'knifes' not in _read('html/search/index.html')


TOP_BAR_PAGES = ('board/index.html', 'search/index.html', 'how/index.html',
                 'faq/index.html', 'about/index.html', 'terms/index.html')


def test_public_pages_share_the_top_bar():
    """One bar (2026-09-27), byte for byte what publish._topbar() writes on the
    generated pages — so the static and generated halves cannot drift."""
    from bb import publish
    bar = publish._topbar().strip()
    for rel in TOP_BAR_PAGES:
        html = _read('html/' + rel)
        assert html.count(bar) == 1, rel
        # these pages are tagless HTML5 (no <body>): the bar is the first thing rendered
        assert html.index('</style>') < html.index(bar) < html.index('<div class="page">') < html.index('<header>'), rel
        assert html.rstrip().endswith('</div>'), rel               # .page wraps through to the end
        assert html.count('class="bb-auth"') == 2, rel          # bar + footer; nav.js flips both
        assert 'body{max-width' not in html.replace(' ', ''), rel   # the box moved to .page
    for rel in PUBLIC_PAGES:
        assert 'bb-strip' not in _read('html/' + rel), rel      # the board's old strip is retired
    assert '.bb-strip' not in _read('html/vibe.css')


APP_PAGES = {'me/add/index.html': 'ADD A KNIFE', 'me/wants/index.html': 'YOUR WANTS',
             'me/settings/index.html': 'SETTINGS', 'admin/index.html': 'ADMIN'}
ROW_LINKS = ('class="add" href="/blade-book/me/add/">+ add<', 'href="/blade-book/me/">register<', 'href="/blade-book/me/wants/">wants<',
             'href="/blade-book/me/settings/">settings<', 'href="/blade-book/board/">board<', 'href="/blade-book/">home<',
             'id="adminlink" href="/blade-book/admin/" hidden>admin<', 'id="signout" class="btn link" type="button">sign out<')


def test_vault_band_keeps_every_nav_link():
    # /me left the shared bb-head row for the vault band (2026-09-26); every destination stays one tap away
    html = _read('html/me/index.html')
    nav = html[html.index('class="vnav"'):html.index('</nav>')]
    for needle in ('href="/blade-book/me/wants/">wants<', 'href="/blade-book/board/">board<', 'href="/blade-book/me/settings/">settings<',
                   'id="adminlink" href="/blade-book/admin/" hidden>admin<', 'id="signout" class="btn link" type="button">sign out<',
                   'class="add" href="/blade-book/me/add/">+ add a knife<'):
        assert needle in nav, needle
    assert '<script src="/blade-book/nav.js?v=20260927" defer></script>' in html


def test_app_pages_share_the_header_row():
    for rel, title in APP_PAGES.items():
        html = _read('html/' + rel)
        assert html.count('class="bb-head"') == 1 and html.count('class="bb-nav"') == 1, rel
        head = html[html.index('class="bb-head"'):html.index('</header>')]
        assert title in head, (rel, title)
        pos = [head.index(n) for n in ROW_LINKS]
        assert pos == sorted(pos), (rel, pos)
        assert '<script src="/blade-book/nav.js?v=20260927" defer></script>' in html, rel
        style = html[html.index('<style>'):html.index('</style>')]
        for local in ('\n  header{', '\n  nav{', '\n  nav a{', 'header h1{'):
            assert local not in style, (rel, local)                         # shared rules only
        assert 'innerHTML' not in html, rel


ASSET_V = '20260927'   # bump on every vibe.css / nav.js change — Cloudflare caches both at the edge for 4 h


def test_asset_urls_are_versioned_everywhere():
    import glob
    files = glob.glob(os.path.join(ROOT, 'html', '**', '*.html'), recursive=True) + [os.path.join(ROOT, 'bb', 'publish.py')]
    seen = 0
    for f in files:
        s = open(f).read()
        assert '/blade-book/vibe.css"' not in s and '/blade-book/nav.js"' not in s, f     # unversioned reference
        if 'vibe.css' in s:
            assert f'/blade-book/vibe.css?v={ASSET_V}"' in s, f
            seen += 1
        if 'nav.js' in s:
            assert f'/blade-book/nav.js?v={ASSET_V}" defer' in s, f
    assert seen >= 12


# --- OG card (2026-09-15): one 1200x630 site card on every static page -------

STATIC_OG_PAGES = ('index.html', 'about/index.html', 'board/index.html', 'card/index.html',
                   'faq/index.html', 'how/index.html', 'search/index.html', 'terms/index.html')


def test_og_card_file_is_1200x630():
    from PIL import Image
    p = os.path.join(ROOT, 'html', 'og.jpg')
    assert os.path.exists(p)
    assert Image.open(p).size == (1200, 630)
    assert os.path.getsize(p) < 600_000


def test_static_pages_carry_full_og_card():
    for rel in STATIC_OG_PAGES:
        html = _read('html/' + rel)
        head = html.split('<style>')[0]
        for needle in ('<meta property="og:title" content="',
                       '<meta property="og:description" content="',
                       '<meta property="og:image" content="https://blade-book.com/og.jpg">',
                       '<meta property="og:image:width" content="1200">',
                       '<meta property="og:image:height" content="630">',
                       '<meta property="og:site_name" content="blade-book">',
                       '<meta property="og:type" content="website">',
                       '<meta name="twitter:card" content="summary_large_image">'):
            assert needle in head, (rel, needle)
        url = 'https://blade-book.com/' + rel.replace('index.html', '')
        assert f'<meta property="og:url" content="{url}">' in head, (rel, url)


def test_take_my_data_csv_button_on_register_and_settings():
    for page in ('html/me/index.html', 'html/me/settings/index.html'):
        s = _read(page)
        assert '/blade-book/api/settings/export.csv' in s, page
        assert 'take my data' in s, page


def test_search_is_linkable_and_lays_out_as_a_grid():
    """Audit 2026-09-19: /search/?q=damascus ignored the query (a search could not be shared) and an
    empty search stacked 50 full-width photos (27,000 px on a desktop)."""
    html = open(os.path.join(ROOT, 'html', 'search', 'index.html')).read()
    assert "new URLSearchParams(location.search)" in html and 'seedFromUrl();\n  runSearch();' in html
    assert 'history.replaceState' in html
    assert 'repeat(auto-fill,minmax(160px,1fr))' in html


def test_404_page_and_vhost_line():
    page = open(os.path.join(ROOT, 'html', '404.html')).read()
    assert 'noindex' in page and '/blade-book/search/' in page and 'vibe.css' in page
    conf = open(os.path.join(ROOT, 'deploy', 'apache-blade-book.com.conf')).read()
    assert 'ErrorDocument 404 /404.html' in conf


def test_save_tells_the_owner_the_public_page_takes_a_minute():
    add = open(os.path.join(ROOT, 'html', 'me', 'add', 'index.html')).read()
    me = open(os.path.join(ROOT, 'html', 'me', 'index.html')).read()
    assert "sessionStorage.setItem('bbJustSaved'" in add
    assert "sessionStorage.removeItem('bbJustSaved')" in me and 'about a minute' in me


def test_landing_pitches_before_it_asks_and_keeps_the_app_home():
    """Audit 2026-09-19 blocker #1: / was a bare sign-in form. Signed-out visitors now get the pitch
    (hero photo, one-photo-vs-spreadsheet, private/public, show them off, sell/hunt, leave any time) ABOVE
    the unchanged sign-in panel; signed-in collectors keep / as their app home (html.in hides the pitch)."""
    html = open(os.path.join(ROOT, 'html', 'index.html')).read()
    assert html.index('class="hero"') < html.index('id="magic"')          # pitch first, ask second
    assert 'href="#start"' in html and '<main id="start">' in html         # CTAs land on the real form
    for needle in ('One photo.', 'The private side', 'The public side', 'Show them off.',
                   'Your data walks out', "html.in .mk, html.in .hero, html.in .out-only{display:none}"):
        assert needle in html, needle
    assert "localStorage.getItem('bbIn')" in html and "classList.toggle('in', me.ok &&" in html
    assert 'href="/blade-book/?pitch=1"' in html and html.count('pitch=1') >= 3   # owner can see what visitors see
    assert "' knives' : ' knife'" in html and "'knife' + (n > 1 ? 's'" not in html   # "81 knifes" (Simon, 09-21)
    # every landing image is a local, EXIF-free file that exists (CSP img-src 'self')
    import re
    imgs = re.findall(r'src="/blade-book/(img/landing/[^"]+)"', html)
    assert len(imgs) == 4
    from PIL import Image
    for rel in imgs:
        assert not dict(Image.open(os.path.join(ROOT, 'html', rel)).getexif()), rel
    # the app's own panels survived the move
    for pid in ('signed-out', 'sent', 'signed-in', 'board', 'providers', 'resend', 'signout'):
        assert f'id="{pid}"' in html, pid


def test_cover_picker_in_the_add_flow_and_as_a_page():
    """Audit 2026-09-19 blocker #2: every public photo was the paperwork flat-lay because the cover
    (hero_photo) defaulted to photo 1 and the only control was a dropdown deep in the edit form.
    The owner picks — never automatic: slots 2/3 can hold a box label or an invoice with an address."""
    add = _read('html/me/add/index.html')
    assert "JSON.stringify({ hero_photo: seq })" in add and "method: 'PATCH'" in add
    assert "if (p && n > 1)" in add                     # no picker for a single photo
    covers = _read('html/me/covers/index.html')
    assert 'noindex' in covers and "api('/knives/full')" in covers
    assert "JSON.stringify({ hero_photo: p.seq })" in covers
    assert '(k.photos || []).length > 1' in covers
    assert 'innerHTML' not in covers and 'innerHTML' not in add.split('setCover')[1][:900]
    me = _read('html/me/index.html')
    assert 'href="/blade-book/me/covers/"' in me and 'coverwrap' in me
    sh = _read('scripts/deploy_landing.sh')
    assert 'me/covers' in sh


def test_signed_in_audit_fixes_2026_09_21():
    me = _read('html/me/index.html')
    assert "' knife' + (" not in me and me.count("' knives')") == 2          # "0 knifes" / "3 knifes made public"
    assert 'a.btn{display:inline-block;text-decoration:none}' in _read('html/me/settings/index.html')


def test_public_copy_says_free_during_early_access():
    """2026-09-26, Simon: free and open for now; charging is a conversation at ~50
    collectors. The FAQ and the about page must not read like a bill."""
    faq = _read('html/faq/index.html')
    about = _read('html/about/index.html')
    for page in (faq, about):
        assert 'free during early access' in page.lower()
        assert 'After that blade-book is a subscription' not in page
        assert 'After that, blade-book is the price above' not in page
    assert 'hear it from us first' in faq and 'id="price"' in faq


def test_born_private_copy_and_add_page_choice():
    # 2026-09-26: knives are born private; the add page asks, the copy says so, nothing promises
    # extra photos on the public page (only the cover goes public).
    add = _read('html/me/add/index.html')
    assert 'id="showpubbox" type="checkbox">' in add and 'is_public: show' in add
    assert 'checked' not in add.split('id="showpubbox"', 1)[1].split('>', 1)[0]      # unticked by default
    for p in ('html/me/add/index.html', 'html/how/index.html'):
        assert 'for your public page' not in _read(p) and 'prettier page' not in _read(p)
    assert 'every knife starts private' in _read('html/index.html')
    assert 'Every knife starts private' in _read('html/faq/index.html')
    assert 'Every knife starts private' in _read('html/how/index.html')
    me = _read('html/me/index.html')
    assert 'made_public' in me and 'private, only you can see it' in me


# --- the vault (/me redesign step 1, docs/superpowers/plans/2026-09-26-vault.md) ---

def test_vault_shelf_helpers_present():
    html = _read('html/me/index.html')
    for needle in ('function shelfTitle(k)', 'function bornLabel(k)', 'function shelfCounts(', 'function isShown(k)',
                   'e.graphic_name || e.special_edition || k.variant || e.damascus_pattern'):
        assert needle in html, needle


def test_vault_band_and_lens():
    html = _read('html/me/index.html')
    for needle in ('id="vband"', 'id="lensvault"', 'id="lenspub"', 'function renderBand(', 'function setLens(',
                   'MY VAULT', 'open my public page'):
        assert needle in html, needle


def test_vault_shelf_grid():
    html = _read('html/me/index.html')
    for needle in ('function renderTile(k)', 'class="vgrid"', 'data-f="priv"', 'data-f="shown"', 'data-f="sale"',
                   "'vt-vis '", "'vt-add'", 'e.stopPropagation()', 'nothing here'):
        assert needle in html, needle
    assert "querySelector('.hero')" not in html


def test_vault_sheet_wraps_the_full_card():
    html = _read('html/me/index.html')
    for needle in ('id="vsheet"', 'function openSheet(k)', 'function closeSheet()', "'Escape'"):
        assert needle in html, needle
    body = html.split('function openSheet(k)', 1)[1].split('\n  }\n', 1)[0]
    assert 'renderOne(' in body


def test_vault_select_mode_and_folded_public_settings():
    html = _read('html/me/index.html')
    for needle in ('id="vselect"', 'selecting', 'class="vshows"'):
        assert needle in html, needle


def test_vibe_css_has_the_top_bar_rules():
    css = _read('html/vibe.css')
    for sel in ('.bb-top{', '.bb-top .brand{', '.bb-top .whose{', '.bb-top nav{',
                '.bb-top nav a{', '.bb-top nav a.bb-auth{'):
        assert sel in css, sel
    assert '@media (max-width:640px){.bb-top .whose{display:none}' in css


def test_every_page_asks_for_the_current_assets():
    import glob
    import re
    seen = {}
    for path in glob.glob(os.path.join(ROOT, 'html', '**', '*.html'), recursive=True) + [
            os.path.join(ROOT, 'bb', 'publish.py')]:
        for v in re.findall(r'(?:vibe\.css|nav\.js)\?v=(\d+)', open(path).read()):
            seen.setdefault(v, []).append(os.path.relpath(path, ROOT))
    assert set(seen) == {ASSET_V}, {v: fs for v, fs in seen.items() if v != ASSET_V}


def test_top_bar_survives_a_narrow_phone_and_a_longer_sign_in_label():
    """Review: signed in, the pill reads 'my register'; at 360px the brand wrapped
    and at 320px the page scrolled sideways."""
    css = _read('html/vibe.css')
    brand = css[css.index('.bb-top .brand{'):].split('}')[0]
    assert 'white-space:nowrap' in brand
    assert '@media (max-width:400px){.bb-top{' in css


def test_deploy_page_script_stops_loudly_when_the_app_does_not_come_back():
    sh = _read('scripts/deploy_page.sh')
    assert 'sleep 1 && curl' not in sh                   # under set -e an && list hides the failure
    assert 'if ! supervisorctl restart blade_book' in sh and sh.count('exit 1') >= 2
    assert 'for i in 1 2 3 4 5 6 7 8 9 10' in sh and 'curl -sf http://127.0.0.1:5004/blade-book/api/healthz' in sh
    assert sh.index('cp -r') < sh.index('supervisorctl restart') < sh.index('publish_sweep.py --all')


def test_admin_activity_wiring():
    html = _read('html/admin/index.html')
    for needle in ('NEEDS HELP', 'PEOPLE', 'VISITORS', 'id="window"', 'id="help"', 'id="people"', 'id="visitors"',
                   'data-hours="48"', 'data-hours="168"', 'data-hours="336"',
                   '<script src="/blade-book/admin/activity.js?v=20260928" defer></script>'):
        assert needle in html, needle
    assert html.index('NEEDS HELP') < html.index('USERS') < html.index('REPORTS')
    js = _read('html/admin/activity.js')
    for needle in ("'/blade-book/api/admin/activity'", 'needs_help', 'people', 'visitors', 'hidden', 'sources',
                   'probably', 'nobody is stuck', 'purge_at', 'textContent', 'Copyright (c) 2026 Simon SGH'):
        assert needle in js, needle
    assert 'innerHTML' not in js and 'insertAdjacentHTML' not in js and 'document.write' not in js


def test_terms_and_faq_say_we_keep_logs():
    # final review I4: every claim here has to be true of the box
    for needle in ('Like every web server, ours keeps a log of requests (address, browser, page, the page you came from, time) for two weeks.',
                   'The app keeps its own log of what happens on your account: sign-ins (email and the address they came from), and what you add, change and delete.',
                   'The app keeps that log for 90 days.',
                   'We read both to fix problems and to help people who get stuck.',
                   'No ads, no trackers, no analytics scripts.'):
        for page in ('html/terms/index.html', 'html/faq/index.html'):
            assert needle in _read(page), (page, needle)
    for page in ('html/terms/index.html', 'html/faq/index.html'):
        assert 'Nothing else' not in _read(page), page
        assert 'no time limit' not in _read(page), page
    terms = _read('html/terms/index.html')
    assert 'We keep one thing' not in terms
    assert 'The logs described below keep the lines they already wrote.' in terms


def test_deploy_activity_script():
    path = os.path.join(ROOT, 'scripts', 'deploy_activity.sh')
    sh = open(path).read()
    assert subprocess.run(['bash', '-n', path]).returncode == 0
    for needle in ('set -euo pipefail', 'supervisorctl restart blade_book', '/blade-book/api/healthz',
                   '/blade-book/api/admin/activity', 'def activity_summary', 'blade-book_access.log',
                   'sudo -u shg -H cp -r', 'Like every web server', 'STOP:'):
        assert needle in sh, needle
    assert 'publish_sweep' not in sh          # publish.py did not change


def test_install_has_the_log_limit_cron():
    sh = open(os.path.join(ROOT, 'scripts', 'install.sh')).read()
    assert "echo '7 0 * * * cd /home/shg/blade-book && python3 scripts/rotate_log.py >> /var/log/blade-book/rotate.log 2>&1'" in sh
    assert "grep -v 'scripts/rotate_log.py'" in sh


def test_deploy_log_limit_script():
    path = os.path.join(ROOT, 'scripts', 'deploy_log_limit.sh')
    sh = open(path).read()
    assert subprocess.run(['bash', '-n', path]).returncode == 0
    for needle in ('set -euo pipefail', 'supervisorctl restart blade_book', '/blade-book/api/healthz',
                   '_PrivateWatchedFile', 'sudo -u shg -H cp -r', 'sudo -u shg -H python3 scripts/rotate_log.py',
                   'bash "$CODE/scripts/add_cron_line.sh" shg \'scripts/rotate_log.py\' "$CRON"',
                   '7 0 * * * cd /home/shg/blade-book && python3 scripts/rotate_log.py >> /var/log/blade-book/rotate.log 2>&1',
                   'The app keeps that log for 90 days.', 'STOP:'):
        assert needle in sh, needle
    assert '| crontab' not in sh              # review I2: only add_cron_line.sh writes a crontab
    # an app that has not restarted keeps writing into the file the run moves away, and those lines are deleted with it
    restart = sh.index('supervisorctl restart blade_book')
    cron = sh.index('bash "$CODE/scripts/add_cron_line.sh"')
    assert restart < sh.index('sudo -u shg -H python3 scripts/rotate_log.py') < cron
    # review I1: the pages promise 90 days, so they go live last, when the nightly run is installed
    assert cron < sh.index('sudo -u shg -H cp -r')
    assert 'publish_sweep' not in sh          # publish.py did not change


CRON_LINE = '7 0 * * * cd /home/shg/blade-book && python3 scripts/rotate_log.py >> /var/log/blade-book/rotate.log 2>&1'


def _add_cron_line(tmp_path, table, fail=''):
    """Run scripts/add_cron_line.sh against a `crontab` that keeps its table
    in a file. table=None: the user has no crontab. fail: `-l` breaks with
    this message. Returns the run and the table afterwards."""
    fake = tmp_path / 'bin'
    fake.mkdir()
    store = tmp_path / 'table'
    if table is not None:
        store.write_text(table)
    (fake / 'crontab').write_text(f'''#!/bin/bash
[ "$1" = -u ] && shift 2
if [ "$1" = -l ]; then
  if [ -n "{fail}" ]; then echo "{fail}" >&2; exit 1; fi
  if [ ! -f {store} ]; then echo "no crontab for shg" >&2; exit 1; fi
  cat {store}; exit 0
fi
cat > {store}
''')
    (fake / 'crontab').chmod(0o755)
    r = subprocess.run(['bash', os.path.join(ROOT, 'scripts', 'add_cron_line.sh'), 'shg', 'scripts/rotate_log.py', CRON_LINE],
                       env=dict(os.environ, PATH=f'{fake}:{os.environ["PATH"]}'), capture_output=True, text=True)
    return r, (store.read_text() if store.exists() else None)


def test_add_cron_line_keeps_every_other_line(tmp_path):
    others = '*/5 * * * * /usr/bin/python3 /home/shg/billboard/billboard_monitor.py\n30 3 * * * bash backup.sh\n'
    r, table = _add_cron_line(tmp_path, others)
    assert r.returncode == 0, r.stderr
    assert table == others + CRON_LINE + '\n'


def test_add_cron_line_twice_is_one_line(tmp_path):
    others = '30 3 * * * bash backup.sh\n'
    r, table = _add_cron_line(tmp_path, others + '0 0 * * * python3 scripts/rotate_log.py --old\n' + CRON_LINE + '\n')
    assert r.returncode == 0, r.stderr
    assert table == others + CRON_LINE + '\n'


def test_add_cron_line_never_writes_when_the_table_cannot_be_read(tmp_path):
    # review I2: `( crontab -l | grep -v …; echo … ) | crontab -` under set -e installs an EMPTY table when -l fails
    others = '30 3 * * * bash backup.sh\n'
    r, table = _add_cron_line(tmp_path, others, fail='crontab: must be privileged')
    assert r.returncode != 0 and 'STOP:' in r.stderr
    assert table == others


def test_add_cron_line_starts_a_table_for_a_user_with_none(tmp_path):
    r, table = _add_cron_line(tmp_path, None)
    assert r.returncode == 0, r.stderr
    assert table == CRON_LINE + '\n'


def test_add_cron_line_with_only_our_line_in_the_table(tmp_path):
    r, table = _add_cron_line(tmp_path, CRON_LINE + '\n')
    assert r.returncode == 0, r.stderr
    assert table == CRON_LINE + '\n'
