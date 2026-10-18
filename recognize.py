#!/usr/bin/env python3
"""Apply the recognition spec to a dump of ECS state and say what every entity is.

    python3 recognize.py state.csv [--events events.csv] [--systems created_by.csv] [--spec generated/recognition.json] [--show NS]

state.csv rows:   component_id_hex, entity_id_hex, value_hex   (one row per current component value; the World's own
                  registries for components and systems may be included, they are skipped)
events.csv rows:  entity_id_hex, transaction_idx   (every transaction that wrote the entity; gives each record the
                  transaction context its keys came from, which is how a live indexer would see it)
created_by.csv:   entity_id_hex, system_name   (the system whose transaction first wrote the entity; for untyped shapes)

The rules, in order, the same ones an indexer would apply:
  1. an EntityType value names the type (and the id is recomputed from the index where the spec has the formula, as a
     check on the spec);
  2. a record is recognised by recomputing its hashed id, keccak256(abi.encodePacked(namespace, keys…)) with the key
     types from the spec: entity-id keys from entities of the parent type, index keys from their index components,
     string keys from the literals the code uses (`key_values`), and, with --events, from the entities and singletons
     the same transaction touched, so a per-account counter is found from the account written beside it;
  3. an address-keyed record (operator.cache) is the entity whose id is a wallet address some entity stores;
  4. what is left is an untyped shape: matched by creating system (when given) and the components written together.
Standard library only. Prints a report; exits 1 if anything stays unrecognised.
"""
import argparse, collections, csv, itertools, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:   # pycryptodome when present (90x faster); the pure-Python keccak in config.py otherwise
    from Crypto.Hash import keccak as _keccak
    def keccak(b): return _keccak.new(digest_bits=256, data=b).digest()
    FAST = True
except ImportError:
    from config import keccak
    FAST = False

MAX_COMBOS = 2_000_000


def abi_string(b):
    try:
        if len(b) >= 64 and int.from_bytes(b[:32], 'big') == 32:
            n = int.from_bytes(b[32:64], 'big'); return b[64:64 + n].decode()
        return b.decode()
    except Exception:
        return None


def width(ty):
    m = re.fullmatch(r'u?int(\d*)', ty)
    return (int(m.group(1)) // 8 if m.group(1) else 32) if m else None


def packed(ty, v):
    """abi.encodePacked of one value: ints big-endian at their width, strings raw, addresses 20 bytes."""
    if ty == 'string': return v if isinstance(v, bytes) else str(v).encode()
    if ty == 'address': return int(v).to_bytes(20, 'big')
    w = width(ty)
    if w: return int(v).to_bytes(w, 'big')
    if ty.startswith('bytes') and ty != 'bytes': return int(v).to_bytes(int(ty[5:]), 'big')
    raise ValueError(ty)


def hashed_id(ns, key_order, key_types, values, packed_encoding):
    parts = [('string', ns)] + [(key_types[k], values[k]) for k in key_order]
    if packed_encoding:
        return keccak(b''.join(packed(t, v) for t, v in parts))
    head, tail, n = b'', b'', 32 * len(parts)
    for t, v in parts:
        if t == 'string':
            data = v if isinstance(v, bytes) else str(v).encode()
            head += (n + len(tail)).to_bytes(32, 'big'); tail += len(data).to_bytes(32, 'big') + data + b'\0' * (-len(data) % 32)
        else:
            head += packed(t, v).rjust(32, b'\0')
    return keccak(head + tail)


def singleton_ids(spec):
    out = {}
    for name, g in spec.get('singletons', {}).items():
        m = re.fullmatch(r'keccak256\("([^"]+)"\)', g.get('id', ''))
        if m: out[keccak(m.group(1).encode()).hex()] = name
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('state'); ap.add_argument('--events'); ap.add_argument('--systems')
    ap.add_argument('--spec', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'generated', 'recognition.json'))
    ap.add_argument('--show', help='list the entities recognised as this namespace or type')
    a = ap.parse_args()
    spec = json.load(open(a.spec))
    by_id = {keccak(c['id'].encode()).hex(): n for n, c in spec['components'].items() if c.get('id')}
    framework = {keccak(c['id'].encode()).hex() for c in spec['framework_components'].values()}
    ents = collections.defaultdict(dict); ghosts = collections.defaultdict(dict); skipped = 0
    # ids are 32-byte words; the indexer stores them as bytea without leading zero bytes, so left-pad on the way in
    pad = lambda h: h.rjust(64, '0')
    for row in csv.reader(open(a.state)):
        cid, eid, val = pad(row[0]), pad(row[1]), row[2]; removed = len(row) > 3 and row[3] in ('t', 'true', '1')
        if cid in framework: skipped += 1; continue
        n = by_id.get(cid)
        if n: (ghosts if removed else ents)[eid][n] = bytes.fromhex(val)
    # a removed value is still context: a droptable commit is deleted in the reveal that writes the log keyed by it
    for eid, comps in ghosts.items():
        if eid not in ents: ents[eid] = {}
        for c, v in comps.items(): ents[eid].setdefault(c, v)
    live = {eid for eid in ents if any(c not in ghosts.get(eid, {}) for c in ents[eid])}   # has at least one current value
    created_by = {}
    if a.systems:
        for eid, sysname in csv.reader(open(a.systems)): created_by[pad(eid)] = sysname
    tx_of = collections.defaultdict(set); in_tx = collections.defaultdict(set)
    if a.events:
        for eid, tx in csv.reader(open(a.events)):
            eid = pad(eid)
            if eid in ents: tx_of[eid].add(tx); in_tx[tx].add(eid)
    singles = singleton_ids(spec)

    # 1. EntityType, and the id formula as a check
    kind = {}; typed = collections.Counter(); unknown_types = collections.Counter()
    for eid, comps in ents.items():
        if eid not in live: continue
        if 'EntityType' in comps:
            t = abi_string(comps['EntityType'])
            if t in spec['entities']: kind[eid] = ('type', t); typed[t] += 1
            else: unknown_types[t] += 1
    verified, unverified = collections.Counter(), collections.defaultdict(list)
    index_of = {t: e.get('index') for t, e in spec['entities'].items()}
    for eid, (_, t) in list(kind.items()):
        e = spec['entities'][t]
        for which, m in e.get('id_hash', {}).items():
            if len(m['key_order']) != 1 or not e.get('index') or e['index'] not in ents[eid]: continue
            ns = e['id'][which].split('(')[0]; v = int.from_bytes(ents[eid][e['index']], 'big')
            ok = hashed_id(ns, m['key_order'], m['key_types'], {m['key_order'][0]: v}, 'Packed' in m['encoding']).hex() == eid
            (verified.__setitem__(t, verified[t] + 1) if ok else unverified[t].append(v))

    # 2. records by recomputed id
    records = {}
    for t, e in spec['entities'].items():
        for ns, r in e.get('records', {}).items(): records[ns] = r
    for ns, r in spec['shared_records'].items(): records[ns] = r
    by_type = collections.defaultdict(list)
    for eid, (_, t) in kind.items(): by_type[t].append(eid)
    rec_hits = collections.Counter(); rec_note = {}
    unrecognised = [eid for eid in live if eid not in kind]

    pointer_comps = [c for c, m in spec['components'].items() if m.get('type') == 'uint256' and re.match(r'^(ID|Id)[A-Z]', c)]
    index_comps = [c for c, m in spec['components'].items() if c.startswith('Index') and (m.get('type') or '').startswith('uint')]
    string_comps = [c for c, m in spec['components'].items() if m.get('type') == 'string']
    def candidates(ns, r, key, ctx_ents, targets=()):
        """Values to try for one key. Whole-state pass (ctx_ents None): ids or index values of the parent types, and
        the string literals the code uses. Transaction pass: the ids written in the transaction and the ids they point
        at (an inventory names its account), the singletons, every Index* value seen on them, the literals, and the
        string components the candidate records themselves carry (a flag's Type is its flag type)."""
        ty, parents = r['key_types'].get(key), r['keys'].get(key)
        parents = parents if isinstance(parents, list) else [parents] if parents else []
        parents = [p for p in parents if p in spec['entities']]
        if ty == 'string':
            vals = {v.encode() for v in (r.get('key_values') or {}).get(key, [])}
            for e in targets:
                for c in string_comps:
                    if c in ents[e]:
                        v = abi_string(ents[e][c])
                        if v: vals.add(v.encode())
            return sorted(vals)
        if ty == 'uint256':
            if ctx_ents is None: return [int(x, 16) for p in parents for x in by_type[p]] if parents else []
            vals = {int(x, 16) for x in ctx_ents} | {int(x, 16) for x in singles}
            for x in ctx_ents:
                for c in pointer_comps:
                    if c in ents[x]: vals.add(int.from_bytes(ents[x][c], 'big'))
            if parents:   # keep ids of the parent types, and pointed-at ids whose type we know to be a parent
                vals = {v for v in vals if kind.get('%064x' % v, ('', ''))[1] in parents or '%064x' % v in singles or '%064x' % v not in kind}
            return sorted(vals)
        if ty and ty.startswith('uint'):
            if ctx_ents is None:
                return sorted({int.from_bytes(ents[x][index_of[p]], 'big') for p in parents if index_of.get(p) for x in by_type[p] if index_of[p] in ents[x]})
            vals = {0}
            for x in ctx_ents:
                for c in index_comps:
                    if c in ents[x]: vals.add(int.from_bytes(ents[x][c], 'big'))
            return sorted(vals)
        return []

    # 2a. whole-state pass: records whose keys are all parent ids/indexes/literals (small products)
    for ns, r in sorted(records.items()):
        if 'key_order' not in r: continue
        cands = [candidates(ns, r, k, None) for k in r['key_order']]
        if any(not c for c in cands): rec_note[ns] = 'needs transaction context (%s)' % ', '.join('%s:%s' % (k, r['key_types'].get(k)) for k in r['key_order']); continue
        total = 1
        for c in cands: total *= len(c)
        if total > MAX_COMBOS: rec_note[ns] = 'too many combinations without context (%d)' % total; continue
        for combo in itertools.product(*cands):
            h = hashed_id(ns, r['key_order'], r['key_types'], dict(zip(r['key_order'], combo)), 'Packed' in r['encoding']).hex()
            if h in ents and h not in kind: kind[h] = ('record', ns); rec_hits[ns] += 1
    # 2b. per-transaction pass with context, for what is left: every id a record could have in that transaction,
    # computed once per transaction and looked up for all the entities it wrote
    ctx_hits = collections.Counter(); big_tx = 0
    if a.events:
        ctx_records = [(ns, r) for ns, r in records.items() if 'key_order' in r]
        pending = collections.defaultdict(set)   # tx -> unrecognised entities it wrote
        for eid in live:
            if eid in kind: continue
            for tx in tx_of.get(eid, ()): pending[tx].add(eid)
        for tx, targets in sorted(pending.items(), key=lambda kv: len(in_tx[kv[0]])):
            targets = {e for e in targets if e not in kind}
            if not targets: continue
            ctx = in_tx[tx]
            if len(ctx) > 600: big_tx += 1; continue   # a registry setup writing thousands of entities: no useful context
            fields = {ns: set(r['fields']) for ns, r in ctx_records}
            for ns, r in ctx_records:
                if not any(set(ents[e]) <= fields[ns] for e in targets if e not in kind): continue   # no target could be this record
                here = [e for e in targets if e not in kind and set(ents[e]) <= fields[ns]]
                cands = [candidates(ns, r, k, ctx, here) for k in r['key_order']]
                if any(not c for c in cands): continue
                total = 1
                for c in cands: total *= len(c)
                if total > 300_000: continue
                for combo in itertools.product(*cands):
                    h = hashed_id(ns, r['key_order'], r['key_types'], dict(zip(r['key_order'], combo)), 'Packed' in r['encoding']).hex()
                    if h in targets and h not in kind: kind[h] = ('record', ns); ctx_hits[ns] += 1
                if all(e in kind for e in targets): break

    # 2c. no context (imported by a snapshot): every parent id and index value in the state, and the literals.
    # Only worth it with the fast keccak; the pure-Python one would take hours.
    brute_hits = collections.Counter()
    if FAST and any(e not in kind for e in live):
        all_ids = [int(x, 16) for x in kind if kind[x][0] == 'type'] + [int(x, 16) for x in singles] + [0]
        all_idx = sorted({int.from_bytes(ents[x][c], 'big') for x in ents for c in index_comps if c in ents[x]} | {0})
        for ns, r in sorted(records.items(), key=lambda kv: len(kv[1].get('key_order', ()))):
            if 'key_order' not in r: continue
            targets = {e for e in live if e not in kind and set(ents[e]) <= set(r['fields'])}
            if not targets: continue
            cands = []
            for k in r['key_order']:
                ty = r['key_types'].get(k)
                if ty == 'string': cands.append(candidates(ns, r, k, None, targets))
                elif ty == 'uint256': cands.append(all_ids)
                elif ty and ty.startswith('uint'): cands.append(all_idx)
                else: cands = []; break
            if not cands or any(not c for c in cands): continue
            total = 1
            for c in cands: total *= len(c)
            if total > 150_000_000: continue
            pk = 'Packed' in r['encoding']
            for combo in itertools.product(*cands):
                h = hashed_id(ns, r['key_order'], r['key_types'], dict(zip(r['key_order'], combo)), pk).hex()
                if h in targets and h not in kind: kind[h] = ('record', ns); brute_hits[ns] += 1

    # 3. address-keyed records: the id is a wallet address some entity stores
    addr_hits = collections.Counter()
    addr_records = {ns: r for ns, r in records.items() if str(r.get('id', '')).startswith('address(')}
    if addr_records:
        addr_vals = collections.defaultdict(set)   # component -> {int(address)}
        for comps in ents.values():
            for c, v in comps.items():
                if spec['components'].get(c, {}).get('type') == 'address': addr_vals[c].add(int.from_bytes(v, 'big'))
        for eid in [x for x in live if x not in kind]:
            n = int(eid, 16)
            if n >= 1 << 160: continue
            for ns, r in addr_records.items():
                m = re.search(r'value of (\w+) set', r.get('parent_from', '') or '')
                comp = m.group(1) if m else None
                if comp and n in addr_vals.get(comp, ()) or (not comp and any(n in s for s in addr_vals.values())):
                    kind[eid] = ('record', ns); addr_hits[ns] += 1; break

    # 4. untyped shapes by creating system and signature
    shapes = {t: e['recognize'] for t, e in spec['entities'].items() if e.get('recognize')}
    sig_hits = collections.Counter(); unmatched = collections.Counter()
    for eid, comps in ents.items():
        if eid in kind or eid not in live: continue
        have = set(comps)
        m = [t for t, rz in shapes.items() if have <= set(rz['signature']) and (not created_by.get(eid) or created_by[eid] in rz['created_by'])]
        if len(m) == 1: kind[eid] = ('shape', m[0]); sig_hits[m[0]] += 1
        else: unmatched[tuple(sorted(have))] += 1

    print('entities %d (framework registry rows skipped: %d)' % (len(ents), skipped))
    print('\n1. by EntityType: %d' % sum(typed.values()))
    for t, n in typed.most_common():
        print('   %-14s %6d%s%s' % (t, n, ('  id formula verified on %d' % verified[t]) if verified[t] else '',
                                   ('  NOT matching on %d (indexes %s)' % (len(unverified[t]), sorted(unverified[t])[:6])) if unverified[t] else ''))
    if unknown_types: print('   EntityType values not in the spec:', dict(unknown_types))
    print('\n2. records by recomputed id: %d from the whole state, %d with transaction context%s' % (sum(rec_hits.values()), sum(ctx_hits.values()), (' (%d transactions too large for context)' % big_tx) if big_tx else ''))
    for ns in sorted(set(rec_hits) | set(ctx_hits) | set(brute_hits)): print('   %-26s %6d  %6d  %6d' % (ns, rec_hits[ns], ctx_hits[ns], brute_hits[ns]))
    if brute_hits: print('   (columns: whole state, transaction context, brute force over all parents for imported records)')
    for ns, why in sorted(rec_note.items()):
        if ns not in ctx_hits and ns not in rec_hits: print('   %-26s   %s' % (ns, why))
    print('\n3. address-keyed records: %d' % sum(addr_hits.values()))
    for ns, n in addr_hits.most_common(): print('   %-26s %6d' % (ns, n))
    print('\n4. untyped shapes by signature%s: %d' % (' and creating system' if created_by else '', sum(sig_hits.values())))
    for k, n in sig_hits.most_common(): print('   %-26s %6d' % (k, n))
    print('\nunrecognised: %d entities' % sum(unmatched.values()))
    for sig, n in unmatched.most_common(10): print('   %6d  %s' % (n, ', '.join(sig)))
    if a.show:
        for eid, (how, what) in kind.items():
            if what == a.show: print(eid, how, sorted(ents[eid]))
    return 1 if unmatched else 0


if __name__ == '__main__':
    sys.exit(main())
