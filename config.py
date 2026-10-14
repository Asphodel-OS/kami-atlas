#!/usr/bin/env python3
"""Read Kamigotchi config values from Yominet, on demand.

    python3 kami/kami-domain/config.py KAMI_LIQ_SALVAGE KAMI_HARV_BOUNTY   # named keys
    python3 kami/kami-domain/config.py --system HarvestLiquidate           # every key a system depends on
    python3 kami/kami-domain/config.py --formula LibKill.calcSalvage       # every key a formula reads
    python3 kami/kami-domain/config.py --all                               # every key the code reads
    python3 kami/kami-domain/config.py --all --diff                        # only keys whose live value differs from the deploy script
    options: --block <n> (historical read)  --json  --world <addr>  --rpc <url>

Read-only: eth_call only, no keys, no transactions. Mirrors LibConfig: a config lives in the Value
component at entity keccak256("is.config" ++ name); arrays are 8 x uint32 packed big-end first;
strings are solady packOne (length byte, then the bytes). The getter the code uses for each key
(getArray / get / getString / getBool / getAddress) decides how the value is decoded.
"""
import argparse, json, os, re, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACTS = os.environ.get('KAMI_CONTRACTS') or os.path.join(os.path.dirname(HERE), 'kamigotchi', 'packages', 'contracts')
GEN = os.path.join(HERE, 'generated')
DEFAULT_RPC = 'https://jsonrpc-yominet-1.anvil.asia-southeast.initia.xyz'
ARCHIVE_RPC = 'https://archival-jsonrpc-yominet-1.anvil.asia-southeast.initia.xyz'  # used for --block (historical state)


# ---------------- keccak-256 (Ethereum's, not NIST SHA3) ----------------

_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000, 0x000000000000808B,
       0x0000000080000001, 0x8000000080008081, 0x8000000000008009, 0x000000000000008A, 0x0000000000000088,
       0x0000000080008009, 0x000000008000000A, 0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
       0x8000000000008003, 0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
       0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_M = (1 << 64) - 1


def _f(a):
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ (((c[(x + 1) % 5] << 1) | (c[(x + 1) % 5] >> 63)) & _M) for x in range(5)]
        a = [[a[x][y] ^ d[x] for y in range(5)] for x in range(5)]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                r = _ROT[x][y]
                b[y][(2 * x + 3 * y) % 5] = ((a[x][y] << r) | (a[x][y] >> (64 - r))) & _M if r else a[x][y]
        a = [[b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
        a[0][0] ^= rc
    return a


def keccak(data: bytes) -> bytes:
    rate = 136
    msg = bytearray(data) + b'\x01'
    msg += b'\x00' * (-len(msg) % rate)
    msg[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(msg), rate):
        for i in range(rate // 8):
            a[i % 5][i // 5] ^= int.from_bytes(msg[off + 8 * i: off + 8 * i + 8], 'little')
        a = _f(a)
    return b''.join(a[i % 5][i // 5].to_bytes(8, 'little') for i in range(4))


def selector(sig):
    return keccak(sig.encode())[:4].hex()


# ---------------- json-rpc ----------------

class RPC:
    def __init__(self, url, block):
        self.url, self.block = url, hex(block) if block is not None else 'latest'

    def batch(self, calls):
        """calls: [(to, data_hex)] -> [result_hex or None]; chunked, one HTTP request per chunk."""
        out = []
        for i in range(0, len(calls), 100):
            chunk = calls[i:i + 100]
            body = [{'jsonrpc': '2.0', 'id': j, 'method': 'eth_call',
                     'params': [{'to': to, 'data': '0x' + data}, self.block]} for j, (to, data) in enumerate(chunk)]
            req = urllib.request.Request(self.url, json.dumps(body).encode(),
                                         {'content-type': 'application/json', 'user-agent': 'kami-domain/config.py'})
            res = {r['id']: r for r in json.load(urllib.request.urlopen(req, timeout=60))}
            out += [res[j].get('result') if 'error' not in res[j] else None for j in range(len(chunk))]
            self.last_error = next((res[j]['error'] for j in range(len(chunk)) if 'error' in res[j]), None)
        return out


def word(n):
    return '%064x' % n


# ---------------- what the code reads ----------------

GETTERS = {'getArray': 'array', 'get': 'uint', 'getString': 'string', 'getBool': 'bool', 'getAddress': 'address',
           'getAddresses': 'address', 'has': 'bool'}


def code_keys():
    """{key: kind} from every LibConfig call with a literal key in the generated function table and systems."""
    kinds = {}
    pat = re.compile(r'LibConfig\.(\w+)(?:\([^)"\\]*\))?[ (]\\?"([A-Z0-9_]+)\\?"')
    for d in ('functions', 'systems'):
        for f in os.listdir(os.path.join(GEN, d)):
            for g, k in pat.findall(open(os.path.join(GEN, d, f)).read()):
                if g in GETTERS: kinds.setdefault(k, GETTERS[g])
    return kinds


def deploy_values():
    """{key: value} from the deploy script (deployment/world/state/configs/configs.ts)."""
    p = os.path.join(CONTRACTS, 'deployment', 'world', 'state', 'configs', 'configs.ts')
    out, kinds = {}, {}
    if not os.path.exists(p): return out, kinds
    for m in re.finditer(r"config\.set\.(\w+)\(\s*'([A-Z0-9_]+)'\s*,\s*([^)]*?)\)\s*;", open(p).read(), re.S):
        kind, key, val = m.groups()
        kinds[key] = {'array': 'array', 'string': 'string', 'bool': 'bool', 'address': 'address'}.get(kind, 'uint')
        val = val.strip()
        try: out[key] = json.loads(val.replace("'", '"'))
        except ValueError:
            # simple arithmetic like 1 * 24 * 3600: evaluate digits and operators only
            out[key] = eval(val, {'__builtins__': {}}) if re.fullmatch(r'[\d\s*+\-/()]+', val) else val
    return out, kinds


def default_world():
    for p in (os.path.join(CONTRACTS, '..', 'client', '.env.production'), os.path.join(CONTRACTS, '.env.production')):
        if os.path.exists(p):
            m = re.search(r"(?:VITE_WORLD_ADDRESS|WORLD)\s*=\s*['\"]?(0x[0-9a-fA-F]{40})", open(p).read())
            if m: return m.group(1)
    return None


def keys_for(section, name):
    p = os.path.join(GEN, section if section != 'formula' else '', '')
    if section == 'system':
        text = open(os.path.join(GEN, 'systems', name + '.yaml')).read()
        m = re.search(r'^  config: \[(.*?)\]|^  config:\n((?:    - .*\n)+)', text, re.M)
    else:
        text = open(os.path.join(GEN, 'formulas.yaml')).read()
        blk = re.search(r'^%s:\n(.*?)(?=^\S)' % re.escape(name), text + '\nEND', re.M | re.S)
        if not blk: sys.exit('no formula %s in generated/formulas.yaml' % name)
        m = re.search(r'^  config: \[(.*?)\]|^  config:\n((?:    - .*\n)+)', blk.group(1), re.M)
    if not m: return []
    raw = m.group(1) or m.group(2)
    return [k.strip(' -"\n') for k in re.split(r'[,\n]', raw) if k.strip(' -"\n')]


# ---------------- decode ----------------

def decode(kind, hexval):
    v = int(hexval, 16)
    if kind == 'array':
        return [(v >> (32 * (7 - i))) & 0xFFFFFFFF for i in range(8)]
    if kind == 'string':
        b = v.to_bytes(32, 'big'); n = b[0]
        return b[1:1 + n].decode(errors='replace') if 0 < n < 32 else None
    if kind == 'address':
        return '0x%040x' % (v & ((1 << 160) - 1))
    if kind == 'bool':
        return True
    return v


def same(live, dep):
    if isinstance(live, list) and isinstance(dep, list):
        return live == (dep + [0] * 8)[:8]
    return str(live).lower() == str(dep).lower()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('keys', nargs='*')
    ap.add_argument('--system'); ap.add_argument('--formula'); ap.add_argument('--all', action='store_true')
    ap.add_argument('--diff', action='store_true'); ap.add_argument('--json', action='store_true')
    ap.add_argument('--block', type=int); ap.add_argument('--world', default=default_world())
    ap.add_argument('--rpc', default=os.environ.get('KAMI_RPC'))
    a = ap.parse_args()
    if not a.world: sys.exit('no World address: pass --world')

    kinds = code_keys()
    keys = list(a.keys)
    if a.system: keys += keys_for('system', a.system)
    if a.formula: keys += keys_for('formula', a.formula)
    if a.all: keys += sorted(kinds)
    keys = list(dict.fromkeys(keys))
    if not keys: ap.print_help(); return 1

    rpc = RPC(a.rpc or (ARCHIVE_RPC if a.block is not None else DEFAULT_RPC), a.block)
    r = rpc.batch([(a.world, selector('components()'))])[0]
    if not r: sys.exit('could not read the World at %s: %s' % (rpc.url, rpc.last_error))
    comps = '0x' + r[-40:]
    value_id = int.from_bytes(keccak(b'component.value'), 'big')
    ents = rpc.batch([(comps, selector('getEntitiesWithValue(uint256)') + word(value_id))])[0]
    value_comp = '0x' + ents[-40:]  # last word of the returned array = first (only) entity

    ids = [int.from_bytes(keccak(b'is.config' + k.encode()), 'big') for k in keys]
    has = rpc.batch([(value_comp, selector('has(uint256)') + word(i)) for i in ids])
    got = rpc.batch([(value_comp, selector('get(uint256)') + word(i)) for i, h in zip(ids, has) if h and int(h, 16)])
    dep, dep_kinds = deploy_values()

    rows, gi = [], iter(got)
    for k, h in zip(keys, has):
        kind = kinds.get(k) or dep_kinds.get(k, 'uint')
        live = decode(kind, next(gi)) if h and int(h, 16) else (False if kind == 'bool' else None)
        row = {'key': k, 'kind': kind, 'value': live}
        if k in dep:
            row['deploy'] = dep[k]
            row['changed_since_deploy'] = not same(live, dep[k])
        if a.diff and not row.get('changed_since_deploy'): continue
        rows.append(row)

    meta = {'world': a.world, 'block': a.block or 'latest', 'value_component': value_comp}
    if a.json:
        print(json.dumps({'meta': meta, 'configs': rows}, indent=1)); return 0
    print('# World %s · block %s · Value component %s' % (a.world, meta['block'], value_comp))
    w = max((len(r['key']) for r in rows), default=10)
    for r in rows:
        v = 'not set' if r['value'] is None else json.dumps(r['value'])
        flag = '  # deploy script: %s' % json.dumps(r['deploy']) if r.get('changed_since_deploy') else ''
        print('%-*s  %-7s %s%s' % (w, r['key'], r['kind'], v, flag))
    return 0


if __name__ == '__main__':
    sys.exit(main())
