/* Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE) */
/* The admin page's activity sections: who is stuck, who did what, who came by.
   Text goes in with textContent only. The page's own script sends a signed-out
   or non-admin visitor home; this one stays quiet for them. */
(function () {
  var URL = '/blade-book/api/admin/activity';
  var KIND = { link_unclicked: 'sign-in link', password_failed: 'password', rate_limited: 'rate limit',
               save_blocked: 'save blocked', decode_failed: 'decode failed', upload_refused: 'photo refused',
               draft_unfinished: 'unfinished draft' };
  var $ = function (id) { return document.getElementById(id); };
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function fill(id, nodes, empty) {
    var box = $(id); clear(box);
    if (!nodes.length) { box.appendChild(el('div', 'empty', empty)); return box; }
    nodes.forEach(function (n) { box.appendChild(n); });
    return box;
  }
  function ago(iso) {
    var s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 90) return 'just now';
    if (s < 5400) return Math.round(s / 60) + ' min ago';
    if (s < 129600) return Math.round(s / 3600) + ' h ago';
    return Math.round(s / 86400) + ' days ago';
  }
  function local(iso) { return new Date(iso).toLocaleString(); }
  function when(iso) { var w = el('span', 'when', ago(iso)); w.title = local(iso); return w; }
  function plural(n, one) { return n + ' ' + one + (n === 1 ? '' : 's'); }

  function signal(s) {
    var card = el('div', 'sig');
    var top = el('div', 'top');
    top.appendChild(el('span', 'badge', KIND[s.kind] || s.kind));
    top.appendChild(el('span', 'who', s.who));
    top.appendChild(when(s.when));
    card.appendChild(top);
    card.appendChild(el('div', 'detail', s.detail));
    if (s.purge_at) card.appendChild(el('div', 'purge', 'purges the night after ' + local(s.purge_at)));
    return card;
  }

  function list(items, line) {
    var ul = el('ul');
    items.forEach(function (it) {
      var li = el('li', null, line(it) + ' ');
      var t = el('span', null, ago(it.when)); t.title = local(it.when);
      li.appendChild(t);
      ul.appendChild(li);
    });
    return ul;
  }

  function person(p) {
    var row = el('details', 'arow');
    var sum = el('summary');
    sum.appendChild(el('span', 'name', '@' + p.handle));
    if (p.drafts) sum.appendChild(el('span', 'badge warn', plural(p.drafts, 'draft')));
    if (p.trail.length) sum.appendChild(el('span', 'badge', plural(p.trail.length, 'action')));
    if (p.last_active) sum.appendChild(when(p.last_active));
    else sum.appendChild(el('span', 'when', 'never seen'));
    row.appendChild(sum);
    row.appendChild(el('div', 'meta', p.email + ' · ' + plural(p.knives, 'knife').replace('knifes', 'knives')));
    if (p.trail.length) row.appendChild(list(p.trail, function (t) { return t.what; }));
    else row.appendChild(el('div', 'meta', 'nothing in this window'));
    return row;
  }

  function visitor(v) {
    var row = el('details', 'arow' + (v.you ? ' you' : ''));
    var sum = el('summary');
    var name = v.you ? 'you' : v.guess.length ? 'probably @' + v.guess.join(' or @') : 'a visitor';
    sum.appendChild(el('span', 'name', name));
    sum.appendChild(el('span', 'badge', v.device));
    if (v.came_from) sum.appendChild(el('span', 'badge ok', 'from ' + v.came_from));
    sum.appendChild(when(v.last));
    row.appendChild(sum);
    row.appendChild(el('div', 'meta', v.network + ' · ' + plural(v.pages.length, 'page') + ' · ' + plural(v.requests, 'request')));
    row.appendChild(list(v.pages, function (p) { return p.path + (p.status >= 400 ? ' (' + p.status + ')' : ''); }));
    return row;
  }

  function warn(id, src) {
    if (!src || src.ok) return;
    var box = $(id);
    box.insertBefore(el('div', 'srcerr', src.error || 'a source could not be read'), box.firstChild);
  }

  function draw(j) {
    fill('help', (j.needs_help || []).map(signal), 'nobody is stuck');
    fill('people', (j.people || []).map(person), 'no accounts');
    var seen = (j.visitors || []).slice().sort(function (a, b) { return (a.you ? 1 : 0) - (b.you ? 1 : 0); });
    var box = fill('visitors', seen.map(visitor), 'nobody came by');
    var hid = j.hidden || {};
    if (hid.bots) box.appendChild(el('div', 'note', plural(hid.bots, 'bot') + ' and scanners hidden (' + plural(hid.requests, 'request') + ')'));
    var src = j.sources || {};
    warn('help', src.app_log); warn('people', src.app_log); warn('visitors', src.access_log);
    if (src.access_log && src.access_log.stopped) box.appendChild(el('div', 'note', 'the log is long: only the newest part was read'));
  }

  function fail(text) {
    ['help', 'people', 'visitors'].forEach(function (id) { fill(id, [], text); });
  }

  function load(hours) {
    Array.prototype.forEach.call($('window').querySelectorAll('button'), function (b) {
      b.setAttribute('aria-pressed', b.getAttribute('data-hours') === String(hours) ? 'true' : 'false');
    });
    fail('loading…');
    fetch(URL + '?hours=' + hours, { credentials: 'same-origin' }).then(function (r) {
      if (r.status === 401 || r.status === 403) { fail(''); return null; }
      return r.json().then(function (j) {
        if (!r.ok) { fail(j.error || 'could not load activity (' + r.status + ')'); return; }
        draw(j);
      });
    }).catch(function (e) { fail('could not reach the server: ' + e.message); });
  }

  $('window').addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('button[data-hours]') : null;
    if (b) load(parseInt(b.getAttribute('data-hours'), 10));
  });
  load(48);
})();
