# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/db.py — the only module that touches SQLite. Every row except users is
owned (owner_id). Private columns are listed once, here, so the leak test in
later plans has a single source of truth.
"""
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from bb import paths

SCHEMA_VERSION = 5

SALE_STATUSES = ('keeping', 'for_trade', 'for_sale', 'consigned', 'sold')
KNIFE_STATUSES = ('draft', 'live')
SUB_STATUSES = ('free', 'active', 'lapsed')
EVENT_TYPES = ('photographed', 'decoded', 'edited', 'for_trade', 'for_sale',
               'traded', 'sold', 'consigned', 'withdrawn')
WANT_MODES = ('trade', 'sale', 'either')

# never leaves the private register — see spec §5 invariant
PRIVATE_COLUMNS = frozenset({'price_paid', 'acquired_from', 'acquired_date',
                             'location', 'notes_private', 'condition_note',
                             'confidence', 'card_text', 'decode_note'})

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  handle TEXT NOT NULL UNIQUE,
  display_name TEXT,
  auth_subjects TEXT NOT NULL DEFAULT '{{}}',
  created TEXT NOT NULL,
  verified_at TEXT,
  is_admin INTEGER NOT NULL DEFAULT 0,
  sub_status TEXT NOT NULL DEFAULT 'free'
    CHECK (sub_status IN {SUB_STATUSES}),
  sub_source TEXT NOT NULL DEFAULT 'manual',
  stripe_customer_id TEXT,
  free_old_used INTEGER NOT NULL DEFAULT 0,
  share_email_on_intro INTEGER NOT NULL DEFAULT 1,
  hide_born_day INTEGER NOT NULL DEFAULT 0,
  profile_private INTEGER NOT NULL DEFAULT 0,
  public_key TEXT,
  publish_dirty_at TEXT,
  session_secret TEXT NOT NULL DEFAULT '',
  last_tag_no INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS knives (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  tag TEXT NOT NULL,
  maker TEXT NOT NULL DEFAULT 'crk',
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN {KNIFE_STATUSES}),
  -- core
  model TEXT, variant TEXT, blade_steel TEXT, blade_shape TEXT,
  blade_length_in REAL, handle_material TEXT, lock_type TEXT,
  born_on TEXT, born_on_precision TEXT, born_on_source TEXT,
  condition INTEGER,
  has_box INTEGER, has_card INTEGER, has_papers INTEGER, has_pouch INTEGER,
  has_lanyard INTEGER, has_spare_hardware INTEGER,
  -- maker extension (JSON; CRK: generation, size, crk_sku, hand, ...)
  ext TEXT NOT NULL DEFAULT '{{}}',
  -- private
  price_paid REAL, acquired_from TEXT, acquired_date TEXT, location TEXT,
  notes_private TEXT, condition_note TEXT, confidence TEXT,
  card_text TEXT, decode_note TEXT,
  -- public
  notes_public TEXT,
  is_public INTEGER NOT NULL DEFAULT 1,
  sale_status TEXT NOT NULL DEFAULT 'keeping'
    CHECK (sale_status IN {SALE_STATUSES}),
  asking_price REAL, seller_note TEXT,
  hero_photo INTEGER,
  created TEXT NOT NULL, updated TEXT NOT NULL,
  UNIQUE (owner_id, tag)
);
CREATE INDEX IF NOT EXISTS knives_owner ON knives(owner_id);
CREATE INDEX IF NOT EXISTS knives_public ON knives(is_public, status, sale_status);

CREATE TABLE IF NOT EXISTS photos (
  id INTEGER PRIMARY KEY,
  knife_id INTEGER NOT NULL REFERENCES knives(id) ON DELETE CASCADE,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL CHECK (seq BETWEEN 1 AND 3),
  store_key TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  width INTEGER, height INTEGER,
  public_key TEXT,
  created TEXT NOT NULL,
  UNIQUE (knife_id, seq)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  knife_id INTEGER NOT NULL REFERENCES knives(id) ON DELETE CASCADE,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN {EVENT_TYPES}),
  detail TEXT, amount REAL, counterparty TEXT,
  public_visible INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_knife ON events(knife_id);

CREATE TABLE IF NOT EXISTS wants (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  maker TEXT NOT NULL DEFAULT 'crk',
  model TEXT, generation TEXT, size TEXT, blade_shape TEXT, blade_steel TEXT,
  keyword TEXT, born_from TEXT, born_to TEXT,
  mode TEXT NOT NULL DEFAULT 'either' CHECK (mode IN {WANT_MODES}),
  max_price REAL,
  active INTEGER NOT NULL DEFAULT 1,
  created TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intros (
  id INTEGER PRIMARY KEY,
  want_id INTEGER REFERENCES wants(id) ON DELETE SET NULL,
  knife_id INTEGER NOT NULL REFERENCES knives(id) ON DELETE CASCADE,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  from_user INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  to_user INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('match', 'board')),
  sent_at TEXT NOT NULL,
  resend_msg_id TEXT,
  UNIQUE (want_id, knife_id)
);

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY,
  knife_id INTEGER NOT NULL REFERENCES knives(id) ON DELETE CASCADE,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  reporter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  reason TEXT NOT NULL,
  created TEXT NOT NULL,
  resolved_at TEXT, resolution TEXT
);

CREATE TABLE IF NOT EXISTS deleted_users (
  email_hash TEXT PRIMARY KEY,
  deleted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS magic_tokens (
  id INTEGER PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL,
  ip TEXT,
  created TEXT NOT NULL,
  expires TEXT NOT NULL,
  used_at TEXT
);
CREATE INDEX IF NOT EXISTS magic_tokens_email ON magic_tokens(email, created);

CREATE TABLE IF NOT EXISTS auth_attempts (
  id INTEGER PRIMARY KEY,
  ip TEXT NOT NULL,
  ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_attempts_ip ON auth_attempts(ip, ts);

CREATE TABLE IF NOT EXISTS oauth_states (
  state TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  nonce TEXT NOT NULL,
  created TEXT NOT NULL,
  expires TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_cards (
  knife_id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  handle TEXT NOT NULL,
  model TEXT, generation TEXT, size TEXT,
  born_year INTEGER,
  damascus_smith TEXT, damascus_pattern TEXT, special_edition TEXT,
  for_sale INTEGER NOT NULL DEFAULT 0,
  card TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_cards_owner ON search_cards(owner_id);
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(text);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


# Schema upgrades for databases created by an older SCHEMA. CREATE IF NOT EXISTS
# handles new tables; new COLUMNS need ALTERs, listed per target version.
MIGRATIONS = {
    3: ['ALTER TABLE knives ADD COLUMN card_text TEXT',
        'ALTER TABLE knives ADD COLUMN decode_note TEXT'],
    4: ['ALTER TABLE users ADD COLUMN public_key TEXT',
        'ALTER TABLE users ADD COLUMN publish_dirty_at TEXT'],
}


def _migrate(con, have):
    for v in range(have + 1, SCHEMA_VERSION + 1):
        for sql in MIGRATIONS.get(v, []):
            try:
                con.execute(sql)
            except sqlite3.OperationalError as e:
                if 'duplicate column' not in str(e):
                    raise


def connect():
    """Open (and on first use create) the database. WAL + 5 s busy timeout
    so gunicorn workers and the match cron coexist; FK enforcement on so
    account deletion cascades."""
    os.makedirs(os.path.dirname(paths.db_path()), exist_ok=True)
    con = sqlite3.connect(paths.db_path(), timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=5000')
    con.execute('PRAGMA foreign_keys=ON')
    con.executescript(SCHEMA)
    row = con.execute('SELECT version FROM schema_version').fetchone()
    if row is None:
        con.execute('INSERT INTO schema_version VALUES (?)', (SCHEMA_VERSION,))
    elif row[0] != SCHEMA_VERSION:
        _migrate(con, row[0])
        con.execute('UPDATE schema_version SET version = ?', (SCHEMA_VERSION,))
    con.commit()
    return con


def _user_row(row):
    if row is None:
        return None
    d = dict(row)
    d['auth_subjects'] = json.loads(d['auth_subjects'] or '{}')
    return d


def create_user(con, email, handle, display_name=None, auth_subjects=None):
    cur = con.execute(
        'INSERT INTO users (email, handle, display_name, auth_subjects, created) '
        'VALUES (?, ?, ?, ?, ?)',
        (email.strip().lower(), handle, display_name,
         json.dumps(auth_subjects or {}), now()))
    con.commit()
    return cur.lastrowid


def get_user(con, user_id):
    return _user_row(con.execute('SELECT * FROM users WHERE id = ?',
                                 (user_id,)).fetchone())


def get_user_by_email(con, email):
    return _user_row(con.execute('SELECT * FROM users WHERE email = ?',
                                 (email.strip().lower(),)).fetchone())


def get_user_by_handle(con, handle):
    return _user_row(con.execute('SELECT * FROM users WHERE handle = ?',
                                 (handle,)).fetchone())


def next_tag(con, owner_id):
    """K01, K02, ... per owner. Monotonic forever: a purged draft's tag is
    never handed out again, because the counter lives on the user row."""
    con.execute('UPDATE users SET last_tag_no = last_tag_no + 1 WHERE id = ?',
                (owner_id,))
    n = con.execute('SELECT last_tag_no FROM users WHERE id = ?',
                    (owner_id,)).fetchone()[0]
    con.commit()
    return f'K{n:02d}'


MAGIC_TTL_MIN = 15
STATE_TTL_MIN = 10


def _norm_email(email):
    return email.strip().lower()


def _sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def _plus(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


# --- magic links -----------------------------------------------------------

def create_magic_token(con, email, ip):
    token = secrets.token_urlsafe(32)
    con.execute(
        'INSERT INTO magic_tokens (token_hash, email, ip, created, expires) '
        'VALUES (?, ?, ?, ?, ?)',
        (_sha(token), _norm_email(email), ip, now(), _plus(MAGIC_TTL_MIN)))
    con.commit()
    return token


def consume_magic_token(con, token):
    """Email for a live, unused token — and burn it, atomically (RETURNING
    means the UPDATE's WHERE clause is the single point of truth for
    single-use, no read-then-write race). None otherwise."""
    row = con.execute(
        'UPDATE magic_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL '
        'AND expires > ? RETURNING email', (now(), _sha(token), now())).fetchone()
    con.commit()
    return row['email'] if row else None


def count_magic_tokens_since(con, email, since_iso):
    return con.execute(
        'SELECT count(*) FROM magic_tokens WHERE email = ? AND created >= ?',
        (_norm_email(email), since_iso)).fetchone()[0]


# --- per-IP attempts -------------------------------------------------------

def record_attempt(con, ip):
    con.execute('INSERT INTO auth_attempts (ip, ts) VALUES (?, ?)', (ip, now()))
    con.commit()


def count_attempts_since(con, ip, since_iso):
    return con.execute(
        'SELECT count(*) FROM auth_attempts WHERE ip = ? AND ts >= ?',
        (ip, since_iso)).fetchone()[0]


# --- OIDC state (DB, not cookie: Apple's form_post callback is cross-site) --

def create_oauth_state(con, provider, nonce):
    state = secrets.token_urlsafe(24)
    con.execute(
        'INSERT INTO oauth_states (state, provider, nonce, created, expires) '
        'VALUES (?, ?, ?, ?, ?)', (state, provider, nonce, now(), _plus(STATE_TTL_MIN)))
    con.commit()
    return state


def pop_oauth_state(con, state):
    row = con.execute(
        'DELETE FROM oauth_states WHERE state = ? AND expires > ? RETURNING provider, nonce',
        (state, now())).fetchone()
    con.execute('DELETE FROM oauth_states WHERE state = ?', (state,))  # burn an expired one too
    con.commit()
    return dict(row) if row else None


# --- user auth fields ------------------------------------------------------

def rotate_session_secret(con, user_id):
    secret = secrets.token_hex(16)
    con.execute('UPDATE users SET session_secret = ? WHERE id = ?', (secret, user_id))
    con.commit()
    return secret


def set_verified(con, user_id):
    con.execute('UPDATE users SET verified_at = ? WHERE id = ? AND verified_at IS NULL',
                (now(), user_id))
    con.commit()


def set_auth_subject(con, user_id, provider, sub):
    subjects = get_user(con, user_id)['auth_subjects']
    subjects[provider] = sub
    con.execute('UPDATE users SET auth_subjects = ? WHERE id = ?',
                (json.dumps(subjects), user_id))
    con.commit()


def get_user_by_subject(con, provider, sub):
    return _user_row(con.execute(
        "SELECT * FROM users WHERE json_extract(auth_subjects, '$.' || ?) = ?",
        (provider, sub)).fetchone())


def handle_exists(con, handle):
    return con.execute('SELECT 1 FROM users WHERE handle = ?',
                       (handle,)).fetchone() is not None


def purge_auth_tables(con):
    cutoff = _plus(-24 * 60)
    con.execute('DELETE FROM magic_tokens WHERE created < ?', (cutoff,))
    con.execute('DELETE FROM auth_attempts WHERE ts < ?', (cutoff,))
    con.execute('DELETE FROM oauth_states WHERE created < ?', (cutoff,))
    con.commit()


# --- knives + photos (owner-scoped; a wrong owner_id == not found) ------------

class SlotTaken(Exception):
    pass


def thumb_key(store_key):
    return store_key.rsplit('.', 1)[0] + '.thumb.jpg'


def _knife_row(con, row):
    if row is None:
        return None
    d = dict(row)
    d['ext'] = json.loads(d['ext'] or '{}')
    d['confidence'] = json.loads(d['confidence']) if d.get('confidence') else None
    d['photos'] = [dict(r) for r in con.execute(
        'SELECT * FROM photos WHERE knife_id = ? ORDER BY seq', (d['id'],))]
    return d


def create_draft_knife(con, owner_id, maker='crk'):
    tag = next_tag(con, owner_id)
    ts = now()
    cur = con.execute(
        'INSERT INTO knives (owner_id, tag, maker, status, created, updated) '
        "VALUES (?, ?, ?, 'draft', ?, ?)", (owner_id, tag, maker, ts, ts))
    con.commit()
    return get_knife(con, owner_id, cur.lastrowid)


def undecoded_draft_tag(con, owner_id):
    """Tag of the oldest open draft PROCESS has not run on yet (no confidence
    stored), or None. A new draft is refused while one exists — the flow is
    photograph -> process -> save, one knife at a time (Simon, 2026-08-29)."""
    r = con.execute("SELECT tag FROM knives WHERE owner_id = ? AND status = 'draft' "
                    'AND confidence IS NULL ORDER BY id LIMIT 1', (owner_id,)).fetchone()
    return r['tag'] if r else None


def count_drafts(con, owner_id):
    return con.execute(
        "SELECT count(*) FROM knives WHERE owner_id = ? AND status = 'draft'",
        (owner_id,)).fetchone()[0]


def get_knife(con, owner_id, knife_id):
    return _knife_row(con, con.execute(
        'SELECT * FROM knives WHERE id = ? AND owner_id = ?',
        (knife_id, owner_id)).fetchone())


def list_knives(con, owner_id, status=None):
    sql = ('SELECT k.id, k.tag, k.status, k.maker, k.model, k.updated, '
           '(SELECT count(*) FROM photos p WHERE p.knife_id = k.id) AS photo_count '
           'FROM knives k WHERE k.owner_id = ?')
    args = [owner_id]
    if status:
        sql += ' AND k.status = ?'
        args.append(status)
    sql += ' ORDER BY k.id DESC'
    return [dict(r) for r in con.execute(sql, args)]


def _touch(con, owner_id, knife_id):
    return con.execute('UPDATE knives SET updated = ? WHERE id = ? AND owner_id = ?',
                       (now(), knife_id, owner_id)).rowcount == 1


def set_knife_note(con, owner_id, knife_id, text):
    ok = con.execute(
        'UPDATE knives SET notes_private = ?, updated = ? WHERE id = ? AND owner_id = ?',
        (text, now(), knife_id, owner_id)).rowcount == 1
    con.commit()
    return ok


def update_knife(con, owner_id, knife_id, fields):
    """Owner edit. `fields` is column → value, already validated by bb.edit; `ext` is a
    whole dict and REPLACES. Raises ValueError for any column outside EDITABLE_COLUMNS
    (status, tag, owner, sale columns have their own helpers)."""
    bad = set(fields) - EDITABLE_COLUMNS
    if bad:
        raise ValueError(f'not editable: {sorted(bad)}')
    if not fields:
        return get_knife(con, owner_id, knife_id)
    cols, vals = [], []
    for col, v in fields.items():
        cols.append(f'{col} = ?')
        vals.append(json.dumps(v) if col == 'ext' else v)
    vals += [now(), knife_id, owner_id]
    ok = con.execute(f'UPDATE knives SET {", ".join(cols)}, updated = ? WHERE id = ? AND owner_id = ?',
                     vals).rowcount == 1
    con.commit()
    return get_knife(con, owner_id, knife_id) if ok else None


def publish_knife(con, owner_id, knife_id):
    """draft → live. Needs at least one photo. Idempotent on a live knife. Writes the
    `photographed` event once, on the transition. Returns (knife, error)."""
    k = get_knife(con, owner_id, knife_id)
    if k is None:
        return None, 'not found'
    if k['status'] == 'live':
        return k, None
    if not k['photos']:
        return k, 'add a photo first'
    con.execute("UPDATE knives SET status = 'live', updated = ? WHERE id = ? AND owner_id = ?",
                (now(), knife_id, owner_id))
    con.commit()
    n = len(k['photos'])
    add_event(con, owner_id, knife_id, 'photographed', detail=f'{n} photo{"s" if n != 1 else ""}')
    return get_knife(con, owner_id, knife_id), None


SALE_EVENT = {'for_trade': 'for_trade', 'for_sale': 'for_sale', 'sold': 'sold',
              'consigned': 'consigned', 'keeping': 'withdrawn'}
_LISTED = ('for_trade', 'for_sale', 'consigned')


def set_sale(con, owner_id, knife_id, sale_status, asking_price=None, seller_note=None,
             amount=None, counterparty=None):
    """Sale controls. One event per status CHANGE: listing states by name, `keeping`
    as `withdrawn` (only when leaving a listed state), `sold` with amount + counterparty."""
    if sale_status not in SALE_STATUSES:
        raise ValueError(f'bad sale_status {sale_status!r}')
    k = get_knife(con, owner_id, knife_id)
    if k is None:
        return None
    con.execute('UPDATE knives SET sale_status = ?, asking_price = ?, seller_note = ?, updated = ? '
                'WHERE id = ? AND owner_id = ?',
                (sale_status, asking_price, seller_note, now(), knife_id, owner_id))
    con.commit()
    if sale_status != k['sale_status'] and (sale_status != 'keeping' or k['sale_status'] in _LISTED):
        add_event(con, owner_id, knife_id, SALE_EVENT[sale_status], detail=seller_note or None,
                  amount=amount if sale_status == 'sold' else asking_price,
                  counterparty=counterparty)
    return get_knife(con, owner_id, knife_id)


def set_public(con, owner_id, knife_ids, is_public):
    """Bulk public/private. Only the owner's rows change; returns the count."""
    ids = [int(i) for i in knife_ids]
    if not ids:
        return 0
    marks = ','.join('?' * len(ids))
    n = con.execute(f'UPDATE knives SET is_public = ?, updated = ? WHERE owner_id = ? AND id IN ({marks})',
                    [1 if is_public else 0, now(), owner_id, *ids]).rowcount
    con.commit()
    return n


SETTINGS_COLUMNS = frozenset({'hide_born_day', 'profile_private', 'public_key'})


def set_user_settings(con, user_id, fields):
    """Owner-facing account settings. Whitelisted columns only; returns the
    fresh user row, or None for an unknown user. Values arrive validated by
    the settings route."""
    bad = set(fields) - SETTINGS_COLUMNS
    if bad:
        raise ValueError(f'not a setting: {sorted(bad)}')
    if fields:
        sets = ', '.join(f'{c} = ?' for c in fields)
        con.execute(f'UPDATE users SET {sets} WHERE id = ?', (*fields.values(), user_id))
        con.commit()
    return get_user(con, user_id)


# --- publish dirty flag (debounced rebuild of the public bundle) --------------

def mark_publish_dirty(con, owner_id):
    """Stamp 'this owner's public page needs a rebuild'. Every save re-stamps,
    which is what makes the sweep's quiet window a debounce."""
    con.execute('UPDATE users SET publish_dirty_at = ? WHERE id = ?', (now(), owner_id))
    con.commit()


def clear_publish_dirty_if(con, owner_id, stamp):
    """Compare-and-clear: only clears if the stamp is the one the build read,
    so a save that lands mid-build keeps the owner dirty. Returns whether cleared."""
    ok = con.execute('UPDATE users SET publish_dirty_at = NULL '
                     'WHERE id = ? AND publish_dirty_at = ?', (owner_id, stamp)).rowcount == 1
    con.commit()
    return ok


def dirty_owners(con, quiet_s=30):
    """Users whose page is dirty AND whose last save is at least quiet_s old."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=quiet_s)).isoformat()
    return [_user_row(r) for r in con.execute(
        'SELECT * FROM users WHERE publish_dirty_at IS NOT NULL AND publish_dirty_at <= ?',
        (cutoff,))]


def public_knives(con, owner_id):
    """The knives that belong on the owner's PUBLIC page: live, is_public, and
    not sold/consigned (pieces that left the collection or sit with a dealer
    leave the public surface — crkinv rule). Oldest first (register order)."""
    out = []
    for r in con.execute(
            "SELECT * FROM knives WHERE owner_id = ? AND status = 'live' AND is_public = 1 "
            "AND sale_status NOT IN ('sold', 'consigned') ORDER BY id", (owner_id,)):
        out.append(_knife_row(con, r))
    return out


def full_register(con, owner_id):
    """Every knife (drafts included), newest first, each with photos + events. The
    owner's own page — never the input to a public view."""
    out = []
    for r in con.execute('SELECT * FROM knives WHERE owner_id = ? ORDER BY id DESC', (owner_id,)).fetchall():
        k = _knife_row(con, r)
        k['events'] = list_events(con, owner_id, k['id'])
        out.append(k)
    return out


# --- events + decode -----------------------------------------------------------

def add_event(con, owner_id, knife_id, type, detail=None, amount=None, counterparty=None,
              public_visible=0):
    cur = con.execute(
        'INSERT INTO events (knife_id, owner_id, date, type, detail, amount, counterparty, public_visible) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (knife_id, owner_id, now(), type, detail, amount, counterparty, public_visible))
    con.commit()
    return cur.lastrowid


def list_events(con, owner_id, knife_id):
    return [dict(r) for r in con.execute(
        'SELECT * FROM events WHERE knife_id = ? AND owner_id = ? ORDER BY id', (knife_id, owner_id))]


def decodes_today(con, owner_id):
    start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).isoformat()   # same '+00:00' form as now()
    return con.execute(
        "SELECT count(*) FROM events WHERE owner_id = ? AND type = 'decoded' AND date >= ?",
        (owner_id, start)).fetchone()[0]


_DECODE_COLUMNS = ('model', 'variant', 'blade_steel', 'blade_shape', 'blade_length_in',
                   'handle_material', 'lock_type', 'born_on', 'born_on_precision',
                   'born_on_source', 'condition', 'has_box', 'has_card', 'has_papers',
                   'has_pouch', 'has_lanyard', 'has_spare_hardware')


def apply_decode(con, owner_id, knife_id, d):
    """Write a decode.Decoded onto the knife (core columns, ext, confidence,
    card_text, decode_note) and record a `decoded` event. Empty strings become
    NULL; booleans become 0/1. Status is untouched — decode never publishes."""
    if get_knife(con, owner_id, knife_id) is None:
        return None
    vals = {}
    for c in _DECODE_COLUMNS:
        v = d.core.get(c)
        if c.startswith('has_'):
            v = 1 if v else 0
        elif v == '':
            v = None
        vals[c] = v
    note = d.reasoning.strip()
    if d.flags:
        note += '\nFLAGS: ' + '; '.join(d.flags)
    sets = ', '.join(f'{c} = ?' for c in vals)
    con.execute(
        f'UPDATE knives SET {sets}, ext = ?, confidence = ?, card_text = ?, decode_note = ?, '
        'updated = ? WHERE id = ? AND owner_id = ?',
        (*vals.values(), json.dumps(d.ext), json.dumps(d.confidence), d.card_text or None,
         note or None, now(), knife_id, owner_id))
    add_event(con, owner_id, knife_id, 'decoded',
              detail=f'{d.model} in={d.input_tokens} out={d.output_tokens} ms={d.latency_ms} '
                     f'flags={len(d.flags)}')
    con.commit()   # explicit — atomicity here must not depend on add_event's commit ordering
    return get_knife(con, owner_id, knife_id)


EDITABLE_COLUMNS = frozenset(_DECODE_COLUMNS) | {
    'ext', 'price_paid', 'acquired_from', 'acquired_date', 'location', 'notes_private',
    'condition_note', 'notes_public', 'hero_photo'}


def _photo_keys(con, knife_id):
    keys = []
    for r in con.execute('SELECT store_key FROM photos WHERE knife_id = ?', (knife_id,)):
        keys += [r['store_key'], thumb_key(r['store_key'])]
    return keys


def delete_draft_knife(con, owner_id, knife_id):
    """Delete a knife, but ONLY if it's still a draft (no-op on a live knife). The
    DELETE /knives/<id> route uses delete_knife (any status) instead; the daily sweep
    uses purge_stale_drafts. Kept for the draft-only contract — only tests call this
    directly today."""
    row = con.execute("SELECT id FROM knives WHERE id = ? AND owner_id = ? AND status = 'draft'",
                      (knife_id, owner_id)).fetchone()
    if row is None:
        return []
    keys = _photo_keys(con, knife_id)
    con.execute('DELETE FROM knives WHERE id = ? AND owner_id = ?', (knife_id, owner_id))  # photos cascade
    con.commit()
    return keys


def delete_knife(con, owner_id, knife_id):
    """Delete a knife of ANY status (the owner's data; anti-sticky). Photos cascade;
    events are removed explicitly. Returns the store keys to remove, None if not owned."""
    row = con.execute('SELECT id FROM knives WHERE id = ? AND owner_id = ?', (knife_id, owner_id)).fetchone()
    if row is None:
        return None
    keys = _photo_keys(con, knife_id)
    con.execute('DELETE FROM events WHERE knife_id = ? AND owner_id = ?', (knife_id, owner_id))
    con.execute('DELETE FROM knives WHERE id = ? AND owner_id = ?', (knife_id, owner_id))  # photos cascade
    con.commit()
    return keys


def add_photo(con, owner_id, knife_id, seq, store_key, sha256, width, height):
    if get_knife(con, owner_id, knife_id) is None:
        raise LookupError(f'knife {knife_id} not found for owner {owner_id}')
    try:
        cur = con.execute(
            'INSERT INTO photos (knife_id, owner_id, seq, store_key, sha256, width, height, created) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (knife_id, owner_id, seq, store_key, sha256, width, height, now()))
    except sqlite3.IntegrityError as e:
        con.rollback()
        raise SlotTaken(f'knife {knife_id} slot {seq} is taken') from e
    _touch(con, owner_id, knife_id)
    con.commit()
    return cur.lastrowid


def get_photo(con, owner_id, knife_id, seq):
    row = con.execute(
        'SELECT * FROM photos WHERE knife_id = ? AND owner_id = ? AND seq = ?',
        (knife_id, owner_id, seq)).fetchone()
    return dict(row) if row else None


def delete_photo(con, owner_id, knife_id, seq):
    row = get_photo(con, owner_id, knife_id, seq)
    if row is None:
        return None
    con.execute('DELETE FROM photos WHERE id = ? AND owner_id = ?', (row['id'], owner_id))
    _touch(con, owner_id, knife_id)
    con.commit()
    return row


def purge_stale_drafts(con, days=7):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    stale = [dict(r) for r in con.execute(
        "SELECT id, owner_id, tag FROM knives WHERE status = 'draft' AND updated < ?", (cutoff,))]
    out = []
    for k in stale:
        k['keys'] = _photo_keys(con, k['id'])
        con.execute('DELETE FROM knives WHERE id = ?', (k['id'],))
        out.append(k)
    con.commit()
    return out
