#!/usr/bin/env python3
"""Check the deploy grants the generator flags against the chain.

    python3 grants.py                  # every system with a write deploy.json does not grant: is it granted on chain?
    python3 grants.py KamiMarketBuy    # one system, all of its flagged components plus two granted ones as a control

For each flagged component it asks the deployed component contract whether the deployed system may write it
(OwnableWritable.writeAccess). Read-only eth_call against Yominet through config.py's helpers; the World address
comes from the client's .env.production, the component and system ids from the contracts under .cache/.
"""
import json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as C
SRC = os.path.join(HERE, '.cache', 'src', 'main', 'packages', 'contracts', 'src')
world = C.default_world(); rpc = C.RPC(os.environ.get('KAMI_RPC') or C.DEFAULT_RPC, None)
print('world', world, 'rpc', rpc.url)
reg_c, reg_s = [('0x' + r[-40:]) for r in rpc.batch([(world, C.selector('components()')), (world, C.selector('systems()'))])]
def lit(path):
    m = re.search(r'keccak256\("([^"]+)"\)', open(path).read()); return m.group(1)
def addr(reg, s):
    r = rpc.batch([(reg, C.selector('getEntitiesWithValue(uint256)') + C.word(int.from_bytes(C.keccak(s.encode()), 'big')))])[0]
    return '0x' + r[-40:] if r and int(r[-40:], 16) else None
systems = json.load(open(os.path.join(HERE, 'viz', 'data', 'systems.json')))
checks = {n: list(s['writes_without_access']) for n, s in systems.items() if s.get('writes_without_access')}
if len(sys.argv) > 1: checks = {n: checks.get(n, []) for n in sys.argv[1:]}
checks.setdefault('KamiMarketBuy', []).extend(['IndexKamiListing', 'Value'])   # granted in deploy.json: positive controls
checks.setdefault('Kami721Stake', []).extend(['Balance'])
for sysname, comps in checks.items():
    sa = addr(reg_s, lit(os.path.join(SRC, 'systems', sysname + 'System.sol')))
    row = []
    for c in comps:
        ca = addr(reg_c, lit(os.path.join(SRC, 'components', c + 'Component.sol')))
        if not sa or not ca: row.append('%s=?(%s)' % (c, 'no system' if not sa else 'no component')); continue
        r = rpc.batch([(ca, C.selector('writeAccess(address)') + C.word(int(sa, 16)))])[0]
        row.append('%s=%s' % (c, 'GRANTED' if r and int(r, 16) else 'no'))
    print('%-16s %s  %s' % (sysname, sa or 'not deployed', '  '.join(row)))
