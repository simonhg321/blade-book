#!/usr/bin/env python3
# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
scripts/eval_decode.py — which model decodes CRK cards best, measured.

Runs each candidate model over Simon's crkinv ground truth (crk.db + originals,
read by PATH — nothing is imported from billboard), scores per field with the
same normalizers the app uses, prints one table. Responses are cached per
(model, tag) so re-scoring after a normalizer tweak costs nothing.

  python3 scripts/eval_decode.py --crk-db /var/lib/billboard/crk/crk.db \
      --originals /var/lib/billboard/crk/originals \
      --models claude-haiku-4-5,claude-sonnet-5,claude-opus-5 \
      --cache-dir /var/lib/blade-book/eval [--limit 10] [--tags K01,K02] [--rescore]

Not CI. Needs ANTHROPIC_API_KEY in /etc/blade-book/.env (or the environment).
Photos: the first --photos originals of each tag (crkinv shot box/card first,
same convention as blade-book's slot 1). No note is fed — the eval measures the
photos alone; the owner's note only makes the real thing easier.
"""
import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bb import config, decode  # noqa: E402
from bb.makers import crk  # noqa: E402

SCORED_FIELDS = ('model', 'blade_shape', 'blade_steel', 'born_on', 'generation', 'size', 'hand',
                 'crk_sku', 'damascus_smith', 'inlay_material', 'handle_treatment',
                 'has_box', 'has_card', 'has_pouch')
_NUM = re.compile(r'^\d{3}\.[a-z0-9]+$', re.I)


def truth_from_row(row, model_row):
    """crk.db knives row + its models row → flat dict in blade-book field names."""
    return {
        'model': model_row.get('model') or model_row.get('family') or '',
        'blade_shape': model_row.get('blade_shape') or '',
        'blade_steel': row.get('steel') or '',
        'born_on': row.get('born_on') or '',
        'generation': model_row.get('generation') or '',
        'size': model_row.get('size') or '',
        'hand': row.get('hand') or '',
        'crk_sku': row.get('crk_sku') or '',
        'damascus_smith': row.get('damascus_smith') or '',
        'inlay_material': row.get('inlay_material') or '',
        'handle_treatment': row.get('handle_treatment') or '',
        'has_box': row.get('has_box'), 'has_card': row.get('has_card'),
        'has_pouch': row.get('has_pouch'), 'has_lanyard': row.get('has_lanyard'),
        'tag': row.get('tag'),
    }


def predicted_flat(d):
    return {**d.core, **d.ext}


def score(truth, pred):
    out = {}
    for f in SCORED_FIELDS:
        t = crk.norm(f, truth.get(f))
        if t == '' or (f.startswith('has_') and truth.get(f) is None):
            out[f] = None
            continue
        out[f] = crk.norm(f, pred.get(f)) == t
    return out


def summarize(results):
    fields = {}
    for f in SCORED_FIELDS:
        hit = sum(1 for r in results if r['scores'].get(f) is True)
        n = sum(1 for r in results if r['scores'].get(f) is not None)
        fields[f] = (hit, n)
    accs = [h / n for h, n in fields.values() if n]
    failed = sum(1 for r in results if r.get('failed'))
    ms_values = [r['ms'] for r in results if not r.get('failed')]
    return {'n': len(results), 'fields': fields, 'failed': failed,
            'mean_acc': sum(accs) / len(accs) if accs else 0.0,
            'cost_usd': round(sum(r.get('cost_usd') or 0 for r in results), 4),
            'median_ms': statistics.median(ms_values) if ms_values else 0}


def format_table(by_model):
    models = list(by_model)
    w = max(len(m) for m in models) + 2
    lines = ['field'.ljust(18) + ''.join(m.ljust(w) for m in models)]
    for f in SCORED_FIELDS:
        cells = []
        for m in models:
            h, n = by_model[m]['fields'][f]
            cells.append((f'{100 * h / n:.0f}% ({h}/{n})' if n else '—').ljust(w))
        lines.append(f.ljust(18) + ''.join(cells))
    lines.append('MEAN'.ljust(18) + ''.join(f'{100 * by_model[m]["mean_acc"]:.1f}%'.ljust(w) for m in models))
    lines.append('cost $'.ljust(18) + ''.join(f'{by_model[m]["cost_usd"]:.2f}'.ljust(w) for m in models))
    lines.append('median ms'.ljust(18) + ''.join(f'{by_model[m]["median_ms"]:.0f}'.ljust(w) for m in models))
    lines.append('n'.ljust(18) + ''.join(str(by_model[m]['n']).ljust(w) for m in models))
    lines.append('failed'.ljust(18) + ''.join(str(by_model[m]['failed']).ljust(w) for m in models))
    return '\n'.join(lines)


def _load_truth(db_path, tags=None):
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    rows = con.execute('SELECT k.*, m.family, m.model AS m_model, m.generation, m.size, m.blade_shape '
                       'FROM knives k JOIN models m ON m.id = k.model_id ORDER BY k.tag').fetchall()
    out = []
    for r in rows:
        r = dict(r)
        if tags and r['tag'] not in tags:
            continue
        model_row = {'family': r['family'], 'model': r['m_model'], 'generation': r['generation'],
                     'size': r['size'], 'blade_shape': r['blade_shape']}
        out.append(truth_from_row(r, model_row))
    con.close()
    return out


def _photos(originals, tag, n):
    d = os.path.join(originals, tag)
    if not os.path.isdir(d):
        return []
    names = sorted(fn for fn in os.listdir(d) if _NUM.match(fn))[:n]
    out = []
    for fn in names:
        with open(os.path.join(d, fn), 'rb') as f:
            j = decode.prep_image(f.read())
        if j:
            out.append(j)
    return out


def _run_one(dec, cache_dir, tag, jpegs):
    path = os.path.join(cache_dir, f'{dec.model}__{tag}.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    try:
        d = dec.decode(jpegs, '', 'crk')
        rec = {'ok': True, 'core': d.core, 'ext': d.ext, 'confidence': d.confidence, 'flags': d.flags,
               'card_text': d.card_text, 'input_tokens': d.input_tokens, 'output_tokens': d.output_tokens,
               'ms': d.latency_ms, 'cost_usd': decode.cost_usd(dec.model, d.input_tokens, d.output_tokens)}
    except decode.DecodeError as e:
        # not cached: a transient failure (network, auth, 529) must retry on the next run
        return {'ok': False, 'error': str(e), 'ms': 0, 'cost_usd': 0}
    with open(path, 'w') as f:
        json.dump(rec, f, indent=1)
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--crk-db', required=True)
    ap.add_argument('--originals', required=True)
    ap.add_argument('--models', default='claude-haiku-4-5,claude-sonnet-5')
    ap.add_argument('--cache-dir', required=True)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--tags', default='')
    ap.add_argument('--photos', type=int, default=3)
    ap.add_argument('--rescore', action='store_true', help='print from cache only; never call the API')
    a = ap.parse_args(argv)
    os.makedirs(a.cache_dir, exist_ok=True)
    config.load()
    key = config.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_API_KEY')
    if not key and not a.rescore:
        sys.exit('no ANTHROPIC_API_KEY in /etc/blade-book/.env or the environment (use --rescore to print from cache)')
    truths = _load_truth(a.crk_db, set(t for t in a.tags.split(',') if t) or None)
    if a.limit:
        truths = truths[:a.limit]
    by_model, misses = {}, {}
    for model in a.models.split(','):
        dec = decode.ClaudeDecoder(model, api_key=key) if not a.rescore else None
        results = []
        for t in truths:
            tag = t['tag']
            path = os.path.join(a.cache_dir, f'{model}__{tag}.json')
            if a.rescore:
                if not os.path.exists(path):
                    continue
                with open(path) as f:
                    rec = json.load(f)
            else:
                jpegs = _photos(a.originals, tag, a.photos)
                if not jpegs:
                    print(f'{tag}: no photos — skipped', file=sys.stderr); continue
                t0 = time.time()
                rec = _run_one(dec, a.cache_dir, tag, jpegs)
                print(f'{model} {tag}: {"ok" if rec["ok"] else "FAIL " + rec.get("error", "")} '
                      f'{rec["ms"]}ms ${rec.get("cost_usd") or 0:.3f} (+{time.time() - t0:.1f}s)', file=sys.stderr)
            if not rec['ok']:
                results.append({'tag': tag, 'failed': True, 'scores': {}, 'cost_usd': 0, 'ms': 0})
                misses.setdefault(model, []).append(f'{tag} DECODE FAILED: {rec.get("error", "")}')
                continue
            pred = {**rec['core'], **rec['ext']}
            s = score(t, pred)
            results.append({'tag': tag, 'scores': s, 'cost_usd': rec.get('cost_usd') or 0, 'ms': rec['ms']})
            for f, ok in s.items():
                if ok is False:
                    misses.setdefault(model, []).append(f'{tag} {f}: truth={t.get(f)!r} got={pred.get(f)!r}')
        by_model[model] = summarize(results)
    print(format_table(by_model))
    with open(os.path.join(a.cache_dir, 'misses.txt'), 'w') as f:
        for model, lines in misses.items():
            f.write(f'## {model}\n' + '\n'.join(lines) + '\n\n')
    print(f'\nmisses → {os.path.join(a.cache_dir, "misses.txt")}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
