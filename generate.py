#!/usr/bin/env python3
"""Generate the Kamigotchi domain manifest from the compiler's syntax trees.

    python3 kami/kami-atlas/generate.py            # compile (cached) + generate
    python3 kami/kami-atlas/generate.py --no-build # reuse .cache/out as is

Reads kami/kamigotchi/packages/contracts by default (override: KAMI_CONTRACTS=<packages/contracts>).
Writes only generated/. notes/ is written by people and never touched here.

Output: systems/ (one file per system: summary + its own steps), functions/ (every library function
reachable from a system, written once, one file per library), formulas.yaml (the calc* functions'
code and inputs), components.yaml, entities.yaml.

Calls are resolved by the compiler (overloads exact). Component access is found where a component
is fetched by its ID (getAddrByID / getCompByID) and a method is called on it; stat components
fetched by name ("HEALTH", ...) are resolved at the call site that passes the literal.
"""
import fcntl, json, os, re, subprocess, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(os.path.dirname(HERE), 'kamigotchi')   # the game checkout next to this repo


def pick_source(argv):
    """Which contracts to read. By default a private worktree of origin/main under .cache/, so the atlas tracks main
    no matter what branch the kamigotchi checkout is on; `--ref X` tracks another branch or tag; `--local` (or
    KAMI_CONTRACTS=<path>) reads the checkout as is. Returns (ref label, packages/contracts path, cache dir)."""
    ref = argv[argv.index('--ref') + 1] if '--ref' in argv else 'main'
    if '--local' in argv or os.environ.get('KAMI_CONTRACTS'):
        root = os.environ.get('KAMI_CONTRACTS') or os.path.join(REPO, 'packages', 'contracts')
        return 'local', root, os.path.join(HERE, '.cache', 'local')
    wt = os.path.join(HERE, '.cache', 'src', ref.replace('/', '_'))
    return ref, os.path.join(wt, 'packages', 'contracts'), os.path.join(HERE, '.cache', ref.replace('/', '_'))


def sync_worktree(ref, wt):
    """Create or fast-forward the worktree at wt to origin/<ref>. Dependencies are symlinked from the checkout
    (pnpm's node_modules are not tracked), so forge resolves imports exactly as it would there."""
    git = lambda *a, **k: subprocess.run(['git', *a], capture_output=True, text=True, **k)
    f = git('-C', REPO, 'fetch', '-q', 'origin', ref)
    if f.returncode != 0: sys.exit('git fetch origin %s failed:\n%s' % (ref, f.stderr[-1500:]))
    if not os.path.isdir(os.path.join(wt, '.git')) and not os.path.isfile(os.path.join(wt, '.git')):
        os.makedirs(os.path.dirname(wt), exist_ok=True)
        r = git('-C', REPO, 'worktree', 'add', '--detach', wt, 'origin/' + ref)
        if r.returncode != 0: sys.exit('git worktree add failed:\n' + r.stderr[-1500:])
    else:
        r = git('-C', wt, 'checkout', '-q', '--detach', 'origin/' + ref)
        if r.returncode != 0: sys.exit('git checkout origin/%s failed:\n%s' % (ref, r.stderr[-1500:]))
    for rel in ('node_modules', os.path.join('packages', 'contracts', 'node_modules')):
        link, target = os.path.join(wt, rel), os.path.join(REPO, rel)
        if not os.path.lexists(link) and os.path.isdir(target): os.symlink(target, link)


REF, ROOT, CACHE = pick_source(sys.argv)
GEN = os.path.join(HERE, 'generated')
NOTES = os.path.join(HERE, 'notes')

WRITE_PREFIXES = ('set', 'remove', 'inc', 'dec', 'extract', 'push', 'transfer', 'extend')
STAT_NAMES = {'HEALTH': 'Health', 'POWER': 'Power', 'HARMONY': 'Harmony', 'VIOLENCE': 'Violence',
              'SLOTS': 'Slots', 'STAMINA': 'Stamina'}  # LibStat.typeToCompID
STAT, ANY = '@stat', '@component'
AMBIENT = {'IUint256Component', 'IUintComp', 'IWorld'}  # passed to nearly every function; left out of signatures
KEYED_LIBS = {'LibConfig': 'config', 'LibBonus': 'bonuses', 'LibFlag': 'flags', 'LibData': 'data', 'LibStat': 'stats'}


# ---------------- build + load ----------------

def build():
    if REF != 'local': sync_worktree(REF, os.path.dirname(os.path.dirname(ROOT)))
    cmd = ['forge', 'build', '--skip', 'test', '--skip', 'script', '--ast',
           '--out', os.path.join(CACHE, 'out'), '--cache-path', os.path.join(CACHE, 'cache')]
    env = dict(os.environ, FOUNDRY_PROFILE='build')
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit('forge build failed:\n' + r.stderr[-3000:])


def load_units():
    units, out = {}, os.path.join(CACHE, 'out')
    for d in sorted(os.listdir(out)):   # sorted: directory order differs between macOS and Linux, and unit order decides ties
        for f in sorted(os.listdir(os.path.join(out, d))):
            if not f.endswith('.json'): continue
            j = json.load(open(os.path.join(out, d, f)))
            ast = j.get('ast')
            if ast and ast['absolutePath'].startswith('src/') and ast['absolutePath'] not in units:
                units[ast['absolutePath']] = ast
    return units


def walk(n):
    if isinstance(n, dict):
        yield n
        for v in n.values(): yield from walk(v)
    elif isinstance(n, list):
        for v in n: yield from walk(v)


# ---------------- index ----------------

class Index:
    def __init__(self, units):
        self.units, self.src = units, {}
        self.fn = {}            # fn id -> (contract name, FunctionDefinition)
        self.comp_id_decl = {}  # VariableDeclaration id of a component's ID -> component name
        self.contract_kind = {}
        for path, u in units.items():
            self.src[path] = open(os.path.join(ROOT, path), encoding='utf-8').read().encode()
            m = re.match(r'src/components/(\w+)Component\.sol$', path)
            for n in u['nodes']:
                if m and n['nodeType'] == 'VariableDeclaration' and n['name'] == 'ID':
                    self.comp_id_decl[n['id']] = m.group(1)
                if n['nodeType'] == 'ContractDefinition':
                    self.contract_kind[n['name']] = n['contractKind']
                    for f in n['nodes']:
                        if f['nodeType'] == 'FunctionDefinition':
                            self.fn[f['id']] = (n['name'], f, path)
        self.summ, self.events_cache = {}, {}
        self.str_consts = {}  # declaration id -> value, for `string constant X = "..."`
        for u in units.values():
            for n in walk(u):
                if n.get('nodeType') == 'VariableDeclaration' and n.get('constant') and isinstance(n.get('value'), dict) \
                        and n['value'].get('nodeType') == 'Literal' and n['value'].get('kind') == 'string':
                    self.str_consts[n['id']] = n['value']['value']
        groups = defaultdict(list)
        for fid, (c, f, _) in self.fn.items(): groups[(c, f['name'] or f['kind'])].append(fid)
        self.keys = {}
        for (c, nm), fids in groups.items():
            for fid in fids:
                if len(fids) == 1: self.keys[fid] = '%s.%s' % (c, nm)
                else:
                    ps = self.fn[fid][1]['parameters']['parameters']
                    self.keys[fid] = '%s.%s(%s)' % (c, nm, ','.join(short_type(x) for x in ps if short_type(x) not in AMBIENT))

    def text(self, path, src, limit=70):
        s, l, _ = map(int, src.split(':'))
        t = ' '.join(self.src[path][s:s + l].decode(errors='replace').split())
        return t if len(t) <= limit else t[:limit - 1] + '…'

    def name(self, fid):
        c, f, _ = self.fn[fid]
        return '%s.%s' % (c, f['name'] or f['kind'])

    def key(self, fid):
        return self.keys[fid]

    # -- component resolution --
    def comp_of(self, expr, locals_):
        """Component name for an expression that holds a component, else None."""
        e = expr
        while e.get('nodeType') == 'FunctionCall' and e.get('kind') == 'typeConversion':
            e = e['arguments'][0]
        if e.get('nodeType') == 'Identifier' and e.get('referencedDeclaration') in locals_:
            return locals_[e['referencedDeclaration']]
        if e.get('nodeType') == 'FunctionCall':
            callee = e['expression']
            nm = callee.get('name') or callee.get('memberName')
            if nm in ('getAddrByID', 'getCompByID') and len(e['arguments']) >= 2:
                a = e['arguments'][1]
                if a.get('nodeType') == 'Identifier' and a.get('referencedDeclaration') in self.comp_id_decl:
                    return self.comp_id_decl[a['referencedDeclaration']]
                if 'StatComponent' in expr.get('typeDescriptions', {}).get('typeString', ''):
                    return STAT
                return ANY
            if nm == 'getStatComp':
                return STAT
            # a component chosen by name at runtime: LibTraitRegistry.getIndexComp(comps, "BODY") returns one of five
            # Index* components depending on the string. Enumerate what the callee can return, so a write through the
            # result counts as a possible write to each of them ('any-of').
            cands = self.returned_components(callee.get('referencedDeclaration'))
            if cands: return ('any-of', frozenset(cands))
        return None

    def returned_components(self, fid, depth=0):
        """Component names a function can return: the component ID constants in its return statements, following
        one level of helper calls (getIndexComp -> IndexBodyCompID…; getStatComp -> typeToCompID -> HealthCompID…)."""
        if fid not in self.fn or depth > 2: return set()
        out = set()
        for n in walk(self.fn[fid][1].get('body') or {}):
            if n.get('nodeType') != 'Return' or not n.get('expression'): continue
            for x in walk(n['expression']):
                if x.get('nodeType') == 'Identifier' and x.get('referencedDeclaration') in self.comp_id_decl:
                    out.add(self.comp_id_decl[x['referencedDeclaration']])
                elif x.get('nodeType') == 'FunctionCall' and x.get('kind') == 'functionCall':
                    ref = x['expression'].get('referencedDeclaration')
                    if ref in self.fn and ref != fid: out |= self.returned_components(ref, depth + 1)
        return out

    # -- ordered events of one function --
    def events(self, fid):
        """Ordered steps: ('call', fid, literals, src) | ('access', comp, method) | ('revert', msg)
        | ('if', cond, then_steps, else_steps) | ('loop', head, steps) | ('emit', name)."""
        if fid in self.events_cache: return self.events_cache[fid]
        c, f, path = self.fn[fid]
        locals_ = {}

        def strval(a):
            if a.get('nodeType') == 'Literal' and a.get('kind') == 'string': return a.get('value')
            if a.get('nodeType') == 'Identifier' and a.get('referencedDeclaration') in self.str_consts:
                return self.str_consts[a['referencedDeclaration']]
            return None

        def lits(call):
            return [v for v in (strval(a) for a in call.get('arguments', [])) if v is not None]

        def expr_steps(n):
            out = []
            if isinstance(n, list):
                for x in n: out += expr_steps(x)
                return out
            if not isinstance(n, dict): return out
            t = n.get('nodeType')
            if t == 'FunctionCall' and n.get('kind') == 'functionCall':
                for a in n['arguments']: out += expr_steps(a)
                callee = n['expression']
                if callee.get('nodeType') == 'MemberAccess':
                    out += expr_steps(callee['expression'])
                    comp = self.comp_of(callee['expression'], locals_)
                    if comp:
                        argt = [self.text(path, x['src'], 60) for x in n['arguments']]
                        if isinstance(comp, tuple):   # chosen by name at runtime: one possible access per candidate
                            for c_ in sorted(comp[1]): out.append(('access', c_, callee['memberName'], argt))
                        else:
                            out.append(('access', comp, callee['memberName'], argt))
                        return out
                nm = callee.get('name')
                if nm == 'require' and len(n['arguments']) >= 2:
                    m2 = n['arguments'][-1]
                    msg = m2.get('value') if m2.get('nodeType') == 'Literal' else self.text(path, m2['src'], 60)
                    out += expr_steps(n['arguments'][0])
                    out.append(('if', '!(%s)' % self.text(path, n['arguments'][0]['src']), [('revert', msg)], [])); return out
                if nm in ('revert', 'require'):
                    msg = [a.get('value') for a in n['arguments'] if a.get('nodeType') == 'Literal' and a.get('kind') == 'string']
                    if not msg and nm == 'revert' and n['arguments']:
                        t = self.text(path, n['arguments'][0]['src'], 80)
                        m = re.match(r'LibString\.concat\("([^"]*)",\s*(\w+)\)', t)
                        msg = [m.group(1) + '<' + m.group(2) + '>' if m else t]
                    out.append(('revert', (msg or ['(no message)'])[-1])); return out
                ref = callee.get('referencedDeclaration')
                if ref in self.fn:
                    args = [('lit', strval(a)) if strval(a) is not None
                            else ('ref', a.get('referencedDeclaration'), a.get('name')) if a.get('nodeType') == 'Identifier'
                            else ('expr', None, self.text(path, a['src'], 60))
                            for a in n['arguments']]
                    if callee.get('nodeType') == 'MemberAccess' and self.fn[ref][1].get('parameters') and \
                            len(self.fn[ref][1]['parameters']['parameters']) == len(args) + 1:
                        args = [None] + args  # `x.f(a)` via `using for`: x is the first parameter
                    out.append(('call', ref, lits(n), n['src'], args))
                return out
            if t == 'IfStatement':
                cond = self.text(path, n['condition']['src'])
                out += expr_steps(n['condition'])
                th, el = stmt_steps(n['trueBody']), stmt_steps(n.get('falseBody')) if n.get('falseBody') else []
                if th or el: out.append(('if', cond, th, el))
                return out
            if t in ('ForStatement', 'WhileStatement', 'DoWhileStatement'):
                body = stmt_steps(n['body'])
                if body: out.append(('loop', self.text(path, n.get('condition', n)['src'], 50), body))
                return out
            if t == 'Conditional':
                out += expr_steps(n['condition'])
                th, el = expr_steps(n['trueExpression']), expr_steps(n['falseExpression'])
                if th or el: out.append(('if', self.text(path, n['condition']['src']), th, el))
                return out
            if t == 'VariableDeclarationStatement':
                init = n.get('initialValue')
                if init is not None:
                    comp = self.comp_of(init, locals_)
                    if comp and len(n['declarations']) == 1 and n['declarations'][0]:
                        locals_[n['declarations'][0]['id']] = comp
                        return out
                    out += expr_steps(init)
                return out
            if t == 'EmitStatement':
                out += expr_steps(n['eventCall']['arguments'])
                out.append(('emit', n['eventCall']['expression'].get('name') or n['eventCall']['expression'].get('memberName')))
                return out
            if t == 'RevertStatement':
                out.append(('revert', n['errorCall']['expression'].get('name', 'custom error'))); return out
            for k, v in n.items():
                if k in ('typeDescriptions', 'src', 'id', 'nodeType'): continue
                out += expr_steps(v)
            return out

        def stmt_steps(n):
            if n is None: return []
            if n.get('nodeType') == 'Block' or n.get('nodeType') == 'UncheckedBlock':
                out = []
                for s in n['statements']: out += expr_steps(s)
                return out
            return expr_steps(n)

        steps = stmt_steps(f['body']) if f.get('body') else []
        self.events_cache[fid] = steps
        return steps

    # -- transitive summary --
    def summary(self, fid, stack=()):
        """{'reads','writes','reverts'} transitively, with @stat left for the caller to bind."""
        if fid in self.summ: return self.summ[fid]
        empty = lambda: {'reads': set(), 'writes': set(), 'reverts': [], 'keys': defaultdict(set), 'event_params': set()}
        if fid in stack: return empty()
        s = empty()
        c, f, _ = self.fn[fid]
        pids = [x['id'] for x in f['parameters']['parameters']]
        if c == 'LibEmitter' and (f['name'] or '').startswith('emit'):
            names = [x['name'] for x in f['parameters']['parameters']]
            if 'identifier' in names: s['event_params'].add(names.index('identifier'))
            else: s['keys']['events'].add('MESSAGE' if 'message' in names else f['name'])

        def add(steps):
            for st in steps:
                if st[0] == 'access':
                    (s['writes'] if st[2].startswith(WRITE_PREFIXES) else s['reads']).add(st[1])
                elif st[0] == 'revert':
                    if st[1] not in s['reverts']: s['reverts'].append(st[1])
                elif st[0] == 'call':
                    sub = bind(self.summary(st[1], stack + (fid,)), st[2])
                    s['reads'] |= sub['reads']; s['writes'] |= sub['writes']
                    s['reverts'] += [r for r in sub['reverts'] if r not in s['reverts']]
                    for k, v in sub['keys'].items(): s['keys'][k] |= v
                    lib = self.fn[st[1]][0]
                    if lib in KEYED_LIBS and st[2]: s['keys'][KEYED_LIBS[lib]].add(st[2][0])
                    for pi in sub['event_params']:
                        a = st[4][pi] if pi < len(st[4]) else None
                        if a and a[0] == 'lit': s['keys']['events'].add(a[1])
                        elif a and a[0] == 'ref' and a[1] in pids: s['event_params'].add(pids.index(a[1]))
                elif st[0] == 'if':
                    add(st[2]); add(st[3])
                elif st[0] == 'loop':
                    add(st[2])
        add(self.events(fid))
        if not stack: self.summ[fid] = s
        return s


def bind(summ, literals):
    stats = [STAT_NAMES[x] for x in literals if x in STAT_NAMES]
    if not stats: return summ
    sub = lambda xs: {y for x in xs for y in (stats if x == STAT else [x])}
    rv = [r for r in summ['reverts'] if r != 'LibStat: invalid stat type']  # unreachable for a known stat literal
    return {'reads': sub(summ['reads']), 'writes': sub(summ['writes']), 'reverts': rv, 'keys': summ['keys'],
            'event_params': summ['event_params']}


# ---------------- steps ----------------

def short_type(p):
    t = p['typeDescriptions']['typeString']
    for x in (' memory', ' calldata', ' storage', ' pointer', 'contract ', 'struct ', 'enum '): t = t.replace(x, '')
    return t.split('.')[-1]


def cname(c):
    return 'Stat[by name]' if c == STAT else 'Component[by id]' if c == ANY else c


def steps(ix, fid):
    """One function's own steps, in source order. Calls are named, not expanded: look them up in functions/."""
    def conv(sts):
        out = []
        for st in sts:
            k = st[0]
            if k == 'access':
                item = '%s %s' % (st[2], cname(st[1]))
                if not (out and out[-1] == item): out.append(item)
            elif k == 'revert': out.append('revert: %s' % st[1])
            elif k == 'emit': out.append('emit %s' % st[1])
            elif k == 'call': out.append(call_label(ix, st))
            elif k == 'if':
                th, el = conv(st[2]), conv(st[3])
                if th and not el and all(isinstance(x, str) and x.startswith('revert: ') for x in th):
                    out.append('revert if %s: %s' % (st[1], ' / '.join(x[8:] for x in th)))
                elif th or el:
                    node = {('if ' + st[1]) if th else ('unless ' + st[1]): th or el}
                    if th and el: node['else'] = el
                    out.append(node)
            elif k == 'loop':
                body = conv(st[2])
                if body: out.append({'for each (%s)' % st[1]: body})
        return out
    return conv(ix.events(fid))


def flat_events(sts):
    for st in sts:
        yield st
        if st[0] == 'if': yield from flat_events(st[2]); yield from flat_events(st[3])
        elif st[0] == 'loop': yield from flat_events(st[2])


def write_path(ix, roots, comp):
    """Shortest call chain from a system's entry points to a function that writes comp directly, as function keys."""
    def direct(fid):
        return any(st[0] == 'access' and cname(st[1]) == comp and st[2].startswith(WRITE_PREFIXES) for st in flat_events(ix.events(fid)))
    seen, queue = {r: None for r in roots}, list(roots)
    while queue:
        x = queue.pop(0)
        if direct(x):
            path = []
            while x is not None: path.append(ix.key(x)); x = seen[x]
            return path[::-1]
        for y in called(ix, x):
            if y not in seen: seen[y] = x; queue.append(y)
    return None


def called(ix, fid):
    out = []
    def go(sts):
        for st in sts:
            if st[0] == 'call': out.append(st[1])
            elif st[0] == 'if': go(st[2]); go(st[3])
            elif st[0] == 'loop': go(st[2])
    go(ix.events(fid))
    return out


def code_of(ix, fid):
    c, f, path = ix.fn[fid]
    s_, l, _ = map(int, f['src'].split(':'))
    raw = ix.src[path]
    col = s_ - (raw.rfind(b'\n', 0, s_) + 1)  # column of `function`: dedent the body by it
    lines = raw[s_:s_ + l].decode().split('\n')
    return '\n'.join([lines[0]] + [x[col:] if x[:col].strip() == '' else x.lstrip() for x in lines[1:]]).rstrip()


def call_label(ix, st):
    lits = st[2]
    k = ix.key(st[1])
    if not lits: return k
    q = ', '.join(json.dumps(x) for x in lits)
    return '%s %s' % (k, q) if k.endswith(')') else '%s(%s)' % (k, q)


# ---------------- yaml emitter ----------------

def scalar(x):
    if x is None: return 'null'
    if isinstance(x, bool): return 'true' if x else 'false'
    if isinstance(x, (int, float)): return str(x)
    s = str(x)
    plain = re.fullmatch(r'[A-Za-z_@][\w./<>?@-]*', s) and s.lower() not in ('true', 'false', 'null', 'yes', 'no', 'on', 'off')
    return s if plain else json.dumps(s, ensure_ascii=False)


def y(v, ind=0):
    pad = '  ' * ind
    if isinstance(v, dict):
        if not v: return pad + '{}'
        lines = []
        for k, x in v.items():
            if isinstance(x, list) and x and y(x).startswith('['):
                lines.append('%s%s: %s' % (pad, scalar(k), y(x)))
            elif isinstance(x, (dict, list)) and x:
                lines.append('%s%s:' % (pad, scalar(k))); lines.append(y(x, ind + 1))
            elif isinstance(x, str) and '\n' in x:
                lines.append('%s%s: |-' % (pad, scalar(k)))
                lines += [(pad + '  ' + ln) if ln.strip() else '' for ln in x.split('\n')]
            else:
                lines.append('%s%s: %s' % (pad, scalar(k), '[]' if x == [] else '{}' if x == {} else scalar(x)))
        return '\n'.join(lines)
    if isinstance(v, list):
        if v and all(not isinstance(x, (dict, list)) and '\n' not in str(x) for x in v):
            flow = '[%s]' % ', '.join(scalar(x) if re.fullmatch(r'[\w@./<>?\[\]-]+', str(x)) and ' ' not in str(x) else json.dumps(str(x), ensure_ascii=False) for x in v)
            if len(flow) <= 180:
                return pad + flow
        lines = []
        for x in v:
            if isinstance(x, dict) and x:
                inner = y(x, ind + 1).split('\n')
                lines.append(pad + '- ' + inner[0].lstrip()); lines += inner[1:]
            else:
                lines.append(pad + '- ' + scalar(x))
        return '\n'.join(lines)
    return pad + scalar(v)


def write(path, header, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('# GENERATED by kami/kami-atlas/generate.py from %s. Do not edit; notes go in kami/kami-atlas/notes/.\n' % header)
        f.write(y(data) + '\n')


def write_spec(ix, model, ents, comps, systems, id_ns, src_note):
    """generated/recognition.json: the machine-readable contract an indexer needs to turn ECS events into domain
    records. Everything here is already in the model; this is the same facts arranged by what a consumer does with
    them: look up a component or system by its on-chain id, classify an entity, and know which column a write lands in."""
    ent_ns = {}   # namespace -> entity type whose id it is (for recognising hashed ids by the keys that made them)
    for et, e in model['entities'].items():
        for which, f in (e.get('id') or {}).items():
            ent_ns[f.split('(')[0]] = et
    def pointer(comp, l):
        to = l.get('to'); to = [to] if isinstance(to, str) else list(to or [])
        out = {'to': to, 'relation': l['type'], 'required': bool(l.get('required'))}
        if l.get('key'): out['key'] = l['key']
        if l.get('via'): out['via'] = l['via']
        if l.get('only_when'): out['only_when'] = l['only_when']
        if l.get('cardinality'): out['cardinality'] = l['cardinality']
        if l.get('settled_in_notes'): out['from_notes'] = True
        elif l.get('inferred'): out['inferred'] = True
        return out
    def record(ns, s):
        r = {'id': s.get('id'), 'keys': s.get('keys') or {}, 'kind': s.get('kind'), 'fields': s.get('fields') or []}
        if s.get('shape'): r['shape'] = s['shape']
        if s.get('links'): r['pointers'] = {c: pointer(c, l) for c, l in s['links'].items()}
        if s.get('held_by'): r['held_by'] = s['held_by']
        if s.get('pointed_at_by'): r['pointed_at_by'] = s['pointed_at_by']
        return r
    entities = {}
    for et, e in model['entities'].items():
        rec = ents.get(et, {})
        typed = e.get('typed', True)
        spec = {'entity_type': et if typed else None, 'layer': e['layer'],
                'id': e.get('id') or {}, 'index': e.get('index'),
                'components': rec.get('components_at_creation') or e.get('components') or [],
                'components_later': e.get('components_later') or [], 'components_later_in': e.get('components_later_in') or {},
                'created_by': e.get('created_by') or [], 'created_in': e.get('created_in') or [],
                'pointers': {c: pointer(c, l) for c, l in (e.get('links') or {}).items() if not c.startswith('key:') and '.' not in c},
                'keyed_by': {c: pointer(c, l) for c, l in (e.get('links') or {}).items() if c.startswith('key:')},
                'records': {ns: record(ns, s) for ns, s in (e.get('sub_entities') or {}).items()}}
        if e.get('extends'): spec['extends'] = e['extends']
        if not typed:
            # no EntityType value ever arrives for these: recognise them by who creates them and what they write together
            spec['recognize'] = {'reason': 'no EntityType in the contracts', 'created_by': e.get('created_by') or [],
                                 'signature': sorted(c for c in spec['components'] if c != 'EntityType'), 'named_by': e.get('name_from')}
        entities[et] = spec
    shared = {ns: record(ns, s) for ns, s in (model.get('shared_sub_entities') or {}).items()}
    # the ECS framework's own registries are components too (the World registers components and systems in them);
    # an indexer sees their writes like any other and should know to skip them
    try: consts = open(os.path.join(ROOT, 'src', 'solecs', 'constants.sol')).read()   # no contract in it, so not among the compiled units
    except OSError: consts = ''
    framework = {m.group(1): {'id': m.group(2), 'what': 'the World\'s registry of %s: entity = contract address, value = id' % m.group(1).split('_')[0].lower()}
                 for m in re.finditer(r'(\w+)_COMPONENT_ID = uint256\(keccak256\("([^"]+)"\)\)', consts)}
    spec = {'source': model['source'], 'generated_from': src_note,
            'about': 'Recognition spec for indexers: components and systems by on-chain id; entities by EntityType value or, for untyped shapes, by creating system and write signature; records by id namespace and keys. See README: Recognition spec.',
            'components': {n: {'id': c.get('id'), 'type': c.get('type'), 'indexed': bool(c.get('indexed')),
                               **({'client_name': c['client_name']} if c.get('client_name') else {})} for n, c in comps.items()},
            'systems': {n: {'id': r.get('id'), 'deployed': bool(r.get('deployed')), 'writes': r.get('writes') or [],
                            **({'disabled': r['disabled']} if r.get('disabled') else {})} for n, r in systems.items() if not n.startswith('__')},
            'framework_components': framework,
            'entities': entities, 'shared_records': shared,
            'singletons': model.get('singletons') or {},
            'id_namespaces': {ns: {'entity': ent_ns.get(ns)} for ns in id_ns},
            'left_out': model['source'].get('left_out') or {}}
    with open(os.path.join(GEN, 'recognition.json'), 'w') as f_: json.dump(spec, f_, indent=1, sort_keys=False)
    return spec


# ---------------- main ----------------

def header_comment(raw):
    lines = raw.split('\n')
    for k, line in enumerate(lines):
        if re.match(r'\s*(abstract\s+)?(contract|library)\s+\w+', line):
            out, j = [], k - 1
            while j >= 0 and (lines[j].strip().startswith(('//', '*', '/*'))):
                t = re.sub(r'^\s*(///?|/\*+|\*/|\*)\s?', '', lines[j]).rstrip()
                if t: out.append(t)
                j -= 1
            return ' '.join(reversed(out)).strip() or None
    return None


# ---------------- domain model derivation ----------------

NAME_TYPES = [('account', 'ACCOUNT'), ('acc', 'ACCOUNT'), ('kami', 'KAMI'), ('pet', 'KAMI'), ('harvest', 'HARVEST'), ('harv', 'HARVEST'),
              ('node', 'NODE'), ('npc', 'NPC'), ('merchant', 'NPC'), ('payitem', 'ITEM'), ('currency', 'ITEM'), ('item', 'ITEM'),
              ('room', 'ROOM'), ('quest', 'QUEST'), ('skill', 'SKILL'), ('rel', 'RELATIONSHIP'), ('recipe', 'RECIPE'),
              ('faction', 'FACTION'), ('goal', 'GOAL'), ('pool', 'POOL'), ('trade', 'TRADE'), ('auction', 'AUCTION'),
              ('listing', 'LISTING'), ('trait', 'TRAIT'), ('gacha', 'GACHA_POOL'), ('tier', 'GOAL')]
AMBIGUOUS = ('holder', 'anchor', 'target', 'source', 'sender', 'payer', 'recipient', 'taxer', 'entity', 'owner', 'to', 'from', 'id', 'reg')
INDEX_TARGET = {'IndexItem': 'ITEM', 'IndexCurrency': 'ITEM', 'IndexRoom': 'ROOM', 'IndexNode': 'NODE', 'IndexNPC': 'NPC',
                'IndexQuest': 'QUEST', 'IndexSkill': 'SKILL', 'IndexRecipe': 'RECIPE', 'IndexFaction': 'FACTION',
                'IndexKami': 'KAMI', 'IndexKamiListing': 'KAMI', 'IndexRelationship': 'RELATIONSHIP', 'IndexAccount': 'ACCOUNT'}


def name_type(name):
    """Entity type a variable or expression name implies, or None if ambiguous."""
    if not name: return None
    seg = re.split(r'[.\s(]', name.strip())[-1] if '.' in name else name
    low = re.sub(r'[^a-z]', '', seg.lower())
    if any(low.startswith(a) for a in AMBIGUOUS) and not any(low.startswith(t) for t, _ in NAME_TYPES[:4]): return None
    for tok, t in NAME_TYPES:
        if low.startswith(tok): return t
    return None


REL_TYPES = {
    'ownership': 'The holder belongs to the referenced entity (IDOwns*, IdHolder).',
    'registry': 'The holder is an instance of, or uses, a registry entry, referenced by its index (Index*).',
    'directed': 'A relation from a source entity to a target entity (IdSource, IdTarget, IDFrom, IDTo).',
    'attachment': 'The holder is attached to an anchor entity, e.g. a requirement or reward on a quest (IDAnchor).',
    'classifier': 'The holder is classified by the referenced entity, e.g. a share by its pool (IDType).',
    'keyed': 'The id is hashed from the referenced entity\'s key (a sub-entity from its parent, registry.scavenge from a node index): no stored pointer is needed to find it.',
}


def rel_type(comp):
    if comp.startswith('IDOwns') or comp == 'IdHolder': return 'ownership'
    if comp.startswith('Index'): return 'registry'
    if comp in ('IdSource', 'IdTarget', 'IDFrom', 'IDTo'): return 'directed'
    if comp == 'IDAnchor': return 'attachment'
    if comp == 'IDType': return 'classifier'
    return 'directed'


def split_args(raw, i):
    """Top-level comma-separated arguments of a call whose '(' ends just before i, as normalized text."""
    depth, cur, out = 1, [], []
    while i < len(raw) and depth:
        ch = chr(raw[i])
        if ch in '([': depth += 1
        elif ch in ')]': depth -= 1
        if depth == 1 and ch == ',': out.append(''.join(cur)); cur = []
        elif depth: cur.append(ch)
        i += 1
    out.append(''.join(cur))
    return [' '.join(a.split()) for a in out if a.strip()]


COMP_METHODS = ('set', 'get', 'safeGet', 'has', 'remove', 'inc', 'dec', 'extract', 'getValue', 'setAll', 'getEntitiesWithValue', 'eqString')


def classify_namespaces(ix, ns_struct):
    """Is each hashed namespace a stored record (components are written at the id) or an anchor key (the id is only ever
    a value other records point at)? Follows the generator function's result through locals, returns and parameters."""
    gen = {}  # fid -> ns, for functions that return a namespace id
    for ns, occ in ns_struct.items():
        for ofid, _ in occ:
            if ofid is not None: gen.setdefault(ofid, ns)
    def unwrap(e):
        while isinstance(e, dict) and e.get('nodeType') == 'FunctionCall' and e.get('kind') == 'typeConversion': e = e['arguments'][0]
        return e
    changed = True
    def returned_ns(f):
        """The namespace a function hands back, if every return is a generator call, a local assigned from one, or 0:
        `return genID(i)`, and getters like `uint256 id = genID(i); return exists ? id : 0`."""
        local, out = {}, set()
        for n in walk(f['body']):
            if n.get('nodeType') == 'VariableDeclarationStatement' and n.get('initialValue') and len(n['declarations']) == 1 and n['declarations'][0]:
                e = unwrap(n['initialValue'])
                if e.get('nodeType') == 'FunctionCall' and e['expression'].get('referencedDeclaration') in gen: local[n['declarations'][0]['id']] = gen[e['expression']['referencedDeclaration']]
        def branches(e):
            e = unwrap(e)
            return branches(e['trueExpression']) + branches(e['falseExpression']) if e.get('nodeType') == 'Conditional' else [e]
        for n in walk(f['body']):
            if n.get('nodeType') != 'Return' or not n.get('expression'): continue
            for e in branches(n['expression']):
                if e.get('nodeType') == 'Literal' and str(e.get('value')) == '0': continue
                if e.get('nodeType') == 'FunctionCall' and e['expression'].get('referencedDeclaration') in gen: out.add(gen[e['expression']['referencedDeclaration']])
                elif e.get('nodeType') == 'Identifier' and e.get('referencedDeclaration') in local: out.add(local[e['referencedDeclaration']])
                else: return None
        return next(iter(out)) if len(out) == 1 else None
    while changed:  # wrappers: a function that returns what genX(...) produces also produces that namespace
        changed = False
        for fid, (c, f, path) in ix.fn.items():
            if fid in gen or not f.get('body'): continue
            ns_ = returned_ns(f)
            if ns_: gen[fid] = ns_; changed = True
    role_cache, ptr_roles = {}, defaultdict(list)   # ptr_roles[(fid, pi)]: pointer sets on the id passed as parameter pi
    ns_local_cache = {}
    def ns_locals(fid):
        """local variable id -> namespace, for locals assigned from a namespace generator in fid."""
        if fid in ns_local_cache: return ns_local_cache[fid]
        local = {}
        for n in walk(ix.fn[fid][1].get('body') or {}):
            if n.get('nodeType') == 'VariableDeclarationStatement' and n.get('initialValue') and len(n['declarations']) == 1 and n['declarations'][0]:
                e = unwrap(n['initialValue'])
                if e.get('nodeType') == 'FunctionCall' and e['expression'].get('referencedDeclaration') in gen: local[n['declarations'][0]['id']] = gen[e['expression']['referencedDeclaration']]
            if n.get('nodeType') == 'Assignment' and n['leftHandSide'].get('nodeType') == 'Identifier':
                e = unwrap(n['rightHandSide'])
                if e.get('nodeType') == 'FunctionCall' and e['expression'].get('referencedDeclaration') in gen: local[n['leftHandSide']['referencedDeclaration']] = gen[e['expression']['referencedDeclaration']]
        ns_local_cache[fid] = local
        return local
    def ns_of_in(fid, a):
        a = unwrap(a)
        if not isinstance(a, dict): return None
        if a.get('nodeType') == 'FunctionCall' and a['expression'].get('referencedDeclaration') in gen: return gen[a['expression']['referencedDeclaration']]
        if a.get('nodeType') == 'Identifier': return ns_locals(fid).get(a.get('referencedDeclaration'))
        return None
    def param_index(fid, a):
        a = unwrap(a)
        ps = [x['id'] for x in ix.fn[fid][1]['parameters']['parameters']]
        return ps.index(a['referencedDeclaration']) if isinstance(a, dict) and a.get('nodeType') == 'Identifier' and a.get('referencedDeclaration') in ps else None
    def ptr_entry(fid, n):
        """(component, where, value text, parameter index of the value if it is one, namespace of the value if known)."""
        v = n['arguments'][-1]
        return (comp_name(n['expression']), ix.key(fid), ix.text(ix.fn[fid][2], v['src'], 60), param_index(fid, v), ns_of_in(fid, v))
    def lift(entries, fid, args):
        """Re-express pointer entries from a callee at this call site: a value that was the callee's parameter becomes the argument passed here."""
        out = []
        for comp, where, text, pi, nsv in entries:
            a = args[pi] if pi is not None and pi < len(args) else None
            if a is None: out.append((comp, where, text, None if pi is None else pi, nsv)); continue
            out.append((comp, ix.key(fid), ix.text(ix.fn[fid][2], a['src'], 60), param_index(fid, a), ns_of_in(fid, a) or nsv))
        return out
    def is_comp_call(callee):
        return callee.get('nodeType') == 'MemberAccess' and callee.get('memberName') in COMP_METHODS and \
            'Component' in callee['expression'].get('typeDescriptions', {}).get('typeString', '')
    def comp_name(callee):
        t = callee['expression'].get('typeDescriptions', {}).get('typeString', '')
        c = ix.comp_of(callee['expression'], {})
        if isinstance(c, tuple): c = None   # chosen by name at runtime: fall back to the declared type's name
        return c if c and not c.startswith('@') else re.sub(r'^contract\s+|Component$', '', t)
    def call_args(n):
        callee = n['expression']; args = list(n['arguments'])
        ref = callee.get('referencedDeclaration')
        if ref in ix.fn and callee.get('nodeType') == 'MemberAccess' and len(ix.fn[ref][1]['parameters']['parameters']) == len(args) + 1:
            args = [None] + args  # using-for
        return ref, args
    def param_roles(fid, pi, depth=0):
        """[(role, component or consumer)] for how parameter pi of fid is used."""
        key = (fid, pi)
        if key in role_cache: return role_cache[key]
        role_cache[key] = []
        if depth > 4 or fid not in ix.fn or not ix.fn[fid][1].get('body'): return []
        ps = ix.fn[fid][1]['parameters']['parameters']
        if pi >= len(ps): return []
        pid, out = ps[pi]['id'], []
        for n in walk(ix.fn[fid][1]['body']):
            if n.get('nodeType') != 'FunctionCall' or n.get('kind') != 'functionCall': continue
            callee = n['expression']
            for j, a in enumerate(n['arguments']):
                a = unwrap(a)
                if not (isinstance(a, dict) and a.get('nodeType') == 'Identifier' and a.get('referencedDeclaration') == pid): continue
                if is_comp_call(callee):
                    out.append(('id' if j == 0 and callee['memberName'] != 'getEntitiesWithValue' else 'value', comp_name(callee)))
                    if j == 0 and callee['memberName'] in ('set', 'setBatch') and len(n['arguments']) >= 2 and re.match(r'^(ID|Id|Index)[A-Z]', comp_name(callee)):
                        ptr_roles[key].append(ptr_entry(fid, n))
                else:
                    ref, args = call_args(n)
                    if ref in ix.fn:
                        jj = args.index(n['arguments'][j]) if n['arguments'][j] in args else j
                        sub = param_roles(ref, jj, depth + 1)
                        ptr_roles[key] += lift(ptr_roles.get((ref, jj), []), fid, args)
                        if sub and ix.name(ref) == 'LibConditional.create' and any(r == 'id' for r, _ in sub):
                            sub = sub + [('shape', 'CONDITION')]
                        out += sub
        role_cache[key] = out
        return out
    uses = defaultdict(list)  # ns -> [(role, component, where)]
    ptrs = defaultdict(list)  # ns -> [(pointer component, where, value text)]: references a record stores
    for fid, (c, f, path) in ix.fn.items():
        if not f.get('body'): continue
        local = {}
        for n in walk(f['body']):
            if n.get('nodeType') == 'VariableDeclarationStatement' and n.get('initialValue') and len(n['declarations']) == 1 and n['declarations'][0]:
                e = unwrap(n['initialValue'])
                if e.get('nodeType') == 'FunctionCall' and e['expression'].get('referencedDeclaration') in gen:
                    local[n['declarations'][0]['id']] = gen[e['expression']['referencedDeclaration']]
            if n.get('nodeType') == 'Assignment' and n['leftHandSide'].get('nodeType') == 'Identifier':
                e = unwrap(n['rightHandSide'])
                if e.get('nodeType') == 'FunctionCall' and e['expression'].get('referencedDeclaration') in gen:
                    local[n['leftHandSide']['referencedDeclaration']] = gen[e['expression']['referencedDeclaration']]
        def ns_of(a):
            a = unwrap(a)
            if not isinstance(a, dict): return None
            if a.get('nodeType') == 'FunctionCall' and a['expression'].get('referencedDeclaration') in gen: return gen[a['expression']['referencedDeclaration']]
            if a.get('nodeType') == 'Identifier': return local.get(a.get('referencedDeclaration'))
            return None
        for n in walk(f['body']):
            if n.get('nodeType') != 'FunctionCall' or n.get('kind') != 'functionCall': continue
            callee = n['expression']
            for j, a in enumerate(n['arguments']):
                ns = ns_of(a)
                if not ns: continue
                if is_comp_call(callee):
                    role = 'id' if j == 0 and callee['memberName'] != 'getEntitiesWithValue' else 'value'
                    uses[ns].append((role, comp_name(callee), ix.key(fid)))
                    if role == 'id' and callee['memberName'] in ('set', 'setBatch') and len(n['arguments']) >= 2 and re.match(r'^(ID|Id|Index)[A-Z]', comp_name(callee)):
                        ptrs[ns].append(ptr_entry(fid, n))
                else:
                    ref, args = call_args(n)
                    if ref in ix.fn and ref not in gen:
                        jj = args.index(n['arguments'][j]) if n['arguments'][j] in args else j
                        roles_ = param_roles(ref, jj)
                        if ix.name(ref) == 'LibConditional.create' and any(r == 'id' for r, _ in roles_):
                            roles_ = roles_ + [('shape', 'CONDITION')]
                        for r, comp in roles_:
                            uses[ns].append((r, comp, ix.key(fid)))
                        ptrs[ns] += lift(ptr_roles.get((ref, jj), []), fid, args)
    out = {}
    comp_names = set(ix.comp_id_decl.values())
    for ns in ns_struct:
        u = uses.get(ns, [])
        ids = [x for x in u if x[0] == 'id']; vals = [x for x in u if x[0] == 'value']
        kind = 'record' if ids else 'anchor key' if vals else 'unused'
        rec = {'kind': kind}
        real = lambda c: c in comp_names
        if ids: rec['fields'] = sorted({c for _, c, _ in ids if real(c)})
        if vals and not ids: rec['pointed_at_by'] = sorted({c for _, c, _ in vals if real(c)})
        if any(r == 'shape' for r, _, _ in u): rec['shape'] = 'CONDITION'
        if ids and ptrs.get(ns): rec['pointers'] = sorted({(c, w, t, nsv) for c, w, t, _, nsv in ptrs[ns]})
        out[ns] = rec
    return out, gen


SHAPE_NAMES = {'LibConditional': 'CONDITION', 'LibRoom.createGate': 'ROOM_GATE', 'LibCommit': 'COMMIT',
               'LibKill._logBounty': 'KILL_BOUNTY_LOG'}  # names for untyped shapes; others default to the library name
SINGLETON_NAMES = {'gacha.id': 'GACHA_POOL'}
DEPRECATED = {'KILL_BOUNTY_LOG': 'deprecated, to be deleted from the contracts (Acheron, 2026-10-16)'}  # why a dead shape is dead, when someone has said
ADDRESS_KEYED_NAMES = {'CacheOperator': 'operator.cache'}
# records whose index key means different things depending on a string key next to it (a stat counter's index is an
# item, a node, ... by `type_`): no single entity to draw a key-derived link to
POLYMORPHIC_KEYS = {'is.data', 'is.score', 'is.config', 'has.flag', 'score.total', 'score.type', 'flag.type'}
ADMIN_MODIFIERS = {'onlyAdmin', 'onlyOwner', 'onlyCommManager', 'onlyLocal'}  # entry points players can't call


def derive_domain(ix, ents, creators, ns_struct, systems, indexed=None, untyped=(), singletons=None, ns_kind=None, gen_ns=None, reach_fids=None):
    indexed, ns_kind, gen_ns = indexed or {}, ns_kind or {}, gen_ns or {}
    via_anchor = []  # anchor keys passed through while resolving the current link
    """Top-level entities, their links and sub-entities, inferred from the code. `inferred` marks name-based guesses."""
    callers = defaultdict(list)  # callee fid -> [(caller fid, args)]
    for fid in ix.fn:
        for st in flat_events(ix.events(fid)):
            if st[0] == 'call' and len(st) > 4: callers[st[1]].append((fid, st[4]))

    def gen_owner(text, where_lib):
        """For a value like genReqAnchor(nodeIndex): the namespace it produces and the entity whose library defines it."""
        m = re.match(r'(?:(\w+)\.)?(\w+)\(', text or '')
        if not m: return None, None
        lib = m.group(1) or where_lib
        fid = key_fid.get('%s.%s' % (lib, m.group(2)))
        if fid is None or fid not in gen_ns: return None, None
        return owner_of_ns(fid)

    def owner_of_ns(fid):
        """(namespace, entity) for a generator or getter: the entity whose id the namespace is (node(index) -> NODE), else
        the one whose library defines it, in which case the namespace is an anchor key the reference passes through."""
        ns_ = gen_ns[fid]
        if ns_ in claimed: return ns_, claimed[ns_]
        owners = lib_creates.get(ix.fn[fid][0]) or {e for e, cs in creators.items() if any(stem(ix.fn[c][0]) == stem(ix.fn[fid][0]) for c in cs)}
        owners = {o for o in owners if o not in untyped}
        if len(owners) != 1: return ns_, None
        via_anchor.append(ns_)
        return ns_, next(iter(owners))

    gl_cache = {}
    def gen_locals(fid):
        """Locals assigned from a namespace generator or getter (`uint256 regID = LibItem.getByIndex(comps, index)`):
        local id and name -> the generator's function id."""
        if fid in gl_cache: return gl_cache[fid]
        out = {}
        f = ix.fn[fid][1] if fid in ix.fn else None
        for n in walk((f or {}).get('body') or {}):
            if n.get('nodeType') == 'VariableDeclarationStatement' and n.get('initialValue') and len(n['declarations']) == 1 and n['declarations'][0]:
                e, keys = n['initialValue'], (n['declarations'][0]['id'], n['declarations'][0]['name'])
            elif n.get('nodeType') == 'Assignment' and n['leftHandSide'].get('nodeType') == 'Identifier':
                e, keys = n['rightHandSide'], (n['leftHandSide'].get('referencedDeclaration'), n['leftHandSide'].get('name'))
            else: continue
            while e.get('nodeType') == 'FunctionCall' and e.get('kind') == 'typeConversion': e = e['arguments'][0]
            if e.get('nodeType') == 'FunctionCall' and e['expression'].get('referencedDeclaration') in gen_ns:
                for k in keys: out[k] = e['expression']['referencedDeclaration']
        gl_cache[fid] = out
        return out

    trace_notes = []  # evidence gathered while resolving the current link: how a value was validated along the way
    pass_cache, alias_cache = {}, {}
    def passthrough(fid):
        """A pure helper that only rearranges its parameters (LibPoolRegistry.sortIndices): its results carry its arguments' types."""
        if fid in pass_cache: return pass_cache[fid]
        f = ix.fn[fid][1] if fid in ix.fn else None
        ok = False
        if f and f.get('body'):
            ids = {p['id'] for p in f['parameters']['parameters']} | {p['id'] for p in (f.get('returnParameters') or {}).get('parameters', [])}
            body = list(walk(f['body']))
            ok = all(n.get('referencedDeclaration') in ids for n in body if n.get('nodeType') == 'Identifier') \
                and not any(n.get('nodeType') == 'FunctionCall' and n.get('kind') == 'functionCall' for n in body)
        pass_cache[fid] = ok
        return ok
    def tuple_alias(fid):
        """Locals bound by `(lo, hi) = helper(a, b)` where helper only rearranges its parameters: local id and name -> the call's arguments."""
        if fid in alias_cache: return alias_cache[fid]
        out = {}
        f = ix.fn[fid][1] if fid in ix.fn else None
        for n in walk((f or {}).get('body') or {}):
            if n.get('nodeType') != 'VariableDeclarationStatement' or not n.get('initialValue') or len(n['declarations']) < 2: continue
            e = n['initialValue']
            if e.get('nodeType') != 'FunctionCall' or not passthrough(e['expression'].get('referencedDeclaration')): continue
            args = [('lit', a.get('value')) if a.get('nodeType') == 'Literal'
                    else ('ref', a.get('referencedDeclaration'), a.get('name')) if a.get('nodeType') == 'Identifier'
                    else ('expr', None, ix.text(ix.fn[fid][2], a['src'], 60)) for a in e['arguments']]
            for d in n['declarations']:
                if d: out[d['id']] = args; out[d['name']] = args
        alias_cache[fid] = out
        return out
    def used_as(fid, declid):
        """Entity type a value in fid is validated as, from the calls it is passed to: into a parameter named after an entity
        (itemIndex), or as the registry `index` of a library that creates exactly one entity (LibItem.getByIndex(comps, index))."""
        found = set()
        for st in flat_events(ix.events(fid)):
            if st[0] != 'call' or st[1] not in ix.fn: continue
            for j, a in enumerate(st[4]):
                if not a or a[0] != 'ref' or a[1] != declid: continue
                ps = ix.fn[st[1]][1]['parameters']['parameters']
                if j >= len(ps): continue
                pn = ps[j]['name']
                t = name_type(pn)
                if not t and 'index' in pn.lower():
                    owners = {o for o in lib_creates.get(ix.fn[st[1]][0], ()) if o not in untyped}
                    if len(owners) == 1:
                        own = next(iter(owners))
                        if any(pn in ks for o, ks in ns_struct.get(id_of.get(own, {}).get('registry') or '', []) if o is not None): t = own
                if t:
                    found.add(t); trace_notes.append('%s is passed as %s to %s' % (a[2], pn, ix.key(st[1])))
        return next(iter(found)) if len(found) == 1 else None

    def arg_types(caller, arg, depth, seen):
        """Entity types an argument in caller denotes: by name, by how the caller validates it, through its own callers, or through a pass-through helper."""
        if not arg: return set()
        kind, ref, nm = arg if len(arg) == 3 else (arg[0], arg[1], None)
        t = name_type(nm) if kind in ('ref', 'expr') else None
        if not t and nm in ('id', 'entityID') and len(lib_creates.get(ix.fn[caller][0], ())) == 1:
            t = next(iter(lib_creates[ix.fn[caller][0]]))
        if not t and kind == 'expr':
            ns_, owner_ = gen_owner(nm, ix.fn[caller][0])
            if owner_: t = owner_
        if t: return {t}
        if kind != 'ref': return set()
        cps = [x['id'] for x in ix.fn[caller][1]['parameters']['parameters']]
        if ref in cps:
            out = trace(caller, cps.index(ref), depth + 1, seen)
            if out: return out
            t = used_as(caller, ref)  # nobody upstream names it: fall back to how this function checks it
            return {t} if t else set()
        gf = gen_locals(caller).get(ref)
        if gf is not None:  # regID = LibItem.getByIndex(...): the local is that registry's entity
            ns_, owner_ = owner_of_ns(gf)
            if owner_: trace_notes.append('%s is %s' % (nm, ix.key(gf))); return {owner_}
        al = tuple_alias(caller).get(ref)
        if al is None: return set()
        out = set()
        for a in al: out |= arg_types(caller, a, depth, seen)
        return out

    def trace(fid, pi, depth=0, seen=()):
        """Entity types that callers pass as parameter pi of fid."""
        if depth > 4 or (fid, pi) in seen: return set()
        out = set()
        for caller, args in callers.get(fid, []):
            if pi >= len(args) or not args[pi]: continue
            out |= arg_types(caller, args[pi], depth, seen + ((fid, pi),))
        return out

    def own_of_bare(fid):
        """What a bare `id` means in fid when its library makes more than one kind of thing: the kind this function
        creates (LibRoom.createGate -> ROOM_GATE), else the one kind whose creation reaches it, else the typed kind
        (LibRoom.setExits is for rooms, not gates). Empty when still ambiguous."""
        own = lib_creates.get(ix.fn[fid][0], set()) if fid in ix.fn else set()
        if len(own) <= 1: return set(own)
        mine = {et for et in own if fid in creators.get(et, [])}
        if len(mine) == 1: return mine
        via = {et for et in own if any(fid in reach(c) for c in creators.get(et, []))}
        if len(via) == 1: return via
        typed_own = {et for et in own if et not in untyped}
        return typed_own if len(typed_own) == 1 else set()
    def resolve(fid, argname):
        """Type for an argument name inside fid: by name, else traced through callers if it is a parameter."""
        ns_, owner_ = gen_owner(argname, ix.fn[fid][0] if fid in ix.fn else None)
        if owner_: return {owner_}, False
        gf = gen_locals(fid).get(argname) if fid in ix.fn else None
        if gf is not None:
            ns_, owner_ = owner_of_ns(gf)
            if owner_: return {owner_}, True
        t = name_type(argname)
        if t: return {t}, False
        ps = [x['name'] for x in ix.fn[fid][1]['parameters']['parameters']] if fid in ix.fn else []
        base = re.split(r'[\[.]', argname)[0]
        own = lib_creates.get(ix.fn[fid][0], set()) if fid in ix.fn else set()
        if base in ps:
            t = trace(fid, ps.index(base))
            if not t and base in ('id', 'entityID', 'regID', 'registryID'): t = own_of_bare(fid)  # a bare regID in LibBonus is a BONUS entry
            return t, True
        al = tuple_alias(fid).get(base) if fid in ix.fn else None
        if al is not None:  # (lo, hi) = sortIndices(indexA, indexB): lo and hi are whatever the arguments are
            t = set()
            for a in al: t |= arg_types(fid, a, 0, ())
            return t, True
        if base in ('id', 'entityID', 'regID', 'registryID'):
            o = own_of_bare(fid)   # a bare `id` in LibHarvest is a harvest; in LibRoom.createGate a gate
            if o: return o, True
        return set(), True

    lib_of = lambda fid: ix.fn[fid][0]

    def link_sets(creator):
        """[(comp, value text, function key, conditions)] for link writes reachable from the creator in its own library."""
        out, lib = [], ix.fn[creator][0]
        def walk(sts, fid, conds, seen):
            for st in sts:
                if st[0] == 'access' and st[2] in ('set', 'setBatch') and len(st) > 3 and len(st[3]) >= 2 and re.match(r'^(ID|Id|Index)[A-Z]', cname(st[1])):
                    out.append((cname(st[1]), st[3][-1].strip(), ix.key(fid), tuple(conds)))
                elif st[0] == 'call' and ix.fn[st[1]][0] == lib and st[1] not in seen:
                    walk(ix.events(st[1]), st[1], conds, seen | {st[1]})
                elif st[0] == 'if':
                    walk(st[2], fid, conds + [st[1]], seen); walk(st[3], fid, conds + ['not (%s)' % st[1]], seen)
                elif st[0] == 'loop':
                    walk(st[2], fid, conds + ['for each (%s)' % st[1]], seen)
        walk(ix.events(creator), creator, [], {creator})
        return out

    all_sets = defaultdict(list)   # comp -> [(fid, op)] across the whole codebase
    for z in ix.fn:
        for st in flat_events(ix.events(z)):
            if st[0] == 'access' and (st[2].startswith('set') or st[2].startswith('remove')):
                all_sets[cname(st[1])].append((z, 'remove' if st[2].startswith('remove') else 'set'))
    key_fid = {ix.key(f): f for f in ix.fn}
    lib_creates = defaultdict(set)
    for et_, cs in creators.items():
        for c in cs: lib_creates[ix.fn[c][0]].add(et_)
    stem = lambda lib: re.sub(r'(Registry|Create)$', '', lib)
    model = {}
    # entity ids
    id_of, ns_used = {}, set()
    def layer_of(et):
        rec = ents[et]
        player = rec.get('created_by_players', [])
        return 'hybrid' if 'IsRegistry' in rec['components_at_creation'] and player else 'runtime' if player else 'registry'

    def id_candidates(et, exact):
        libs = {lib_of(c) for c in creators[et]}
        out = []
        for ns, occ in ns_struct.items():
            for ofid, keys in occ:
                if ofid is None: continue
                ol, on = lib_of(ofid), ix.fn[ofid][1]['name'] or ''
                if exact: ok = ol in libs
                else: ok = stem(ol) in {stem(l) for l in libs} or ns.split('.')[0] == et.lower() or ns.split('.')[:2] == ['registry', et.lower()]
                if not ok or not re.match(r'gen\w*ID$', on) or re.search(r'(Anchor|Ptr)$', on): continue
                # a key naming this entity's own id (tradeID) makes it a child record; its own index is fine
                if any(et in resolve(ofid, k)[0] for k in keys if k.lower().endswith('id')): continue
                out.append(ns)
        return sorted(set(out), key=lambda n: (n.count('.'), n))

    claimed = {}
    for exact in (True, False):
        for et in ents:
            if et in id_of: continue
            cands = [n for n in id_candidates(et, exact) if n not in claimed]
            if not cands: continue
            is_reg = lambda n: n.startswith('registry.') or n.endswith('.registry')
            reg, inst = [n for n in cands if is_reg(n)], [n for n in cands if not is_reg(n)]
            roots = {}
            lay = layer_of(et)
            if lay == 'registry':
                roots['registry'] = (reg or inst)[0]  # per-holder records of a registry entry stay sub-entities
            elif lay == 'runtime':
                roots['instance'] = (inst or reg)[0]
            else:
                if reg: roots['registry'] = reg[0]
                if inst: roots['instance'] = inst[0]
            id_of[et] = roots
            for v in roots.values(): claimed[v] = et
    for et in ents: id_of.setdefault(et, {})
    ns_used = set(claimed)

    for et, rec in ents.items():
        by = rec.get('created_by_systems', [])
        e = {'layer': layer_of(et), 'created_by': by, 'created_in': rec['created_in']}
        if et in untyped:   # the contracts never name these; the name is this script's, from the creating library
            e['typed'] = False
            e['name_from'] = 'kami-atlas: no EntityType in the contracts; named by generate.py after ' + ', '.join(sorted(rec['created_in']))
        if id_of[et]:
            e['id'] = {k: '%s(%s)' % (v, ', '.join(ns_struct[v][0][1])) for k, v in id_of[et].items()}
        elif et in untyped:
            e['id'] = {'instance': 'world.getUniqueEntityId() (a counter, no hashed id)'}
        links = {}
        sets = [x for c in creators[et] for x in link_sets(c)]
        comp_users = defaultdict(int)
        for r_ in ents.values():
            for c_ in r_['components_at_creation']: comp_users[c_] += 1
        own_ns = id_of[et].get('instance') or id_of[et].get('registry')  # the runtime id when there is one
        id_keys = ns_struct[own_ns][0][1] if own_ns else []
        id_formula = '%s(%s)' % (own_ns, ', '.join(id_keys)) if own_ns else None
        for comp in sorted({c for c, *_ in sets}):
            rows = [r for r in sets if r[0] == comp]
            if comp in INDEX_TARGET and (INDEX_TARGET[comp] == et or comp == 'IndexAccount'):
                e['index'] = comp; continue
            types, inferred, how, via_keys = set(), False, [], set()
            if comp in INDEX_TARGET:
                types = {INDEX_TARGET[comp]}; how.append('component name: %s points at %s by index' % (comp, INDEX_TARGET[comp]))
            else:
                for c, val, where, conds in rows:
                    del via_anchor[:]
                    t, inf = resolve(key_fid.get(where), val)
                    types |= t; inferred |= inf
                    anchors = sorted(set(via_anchor)); via_keys |= set(anchors)
                    how.append('%s = %s in %s%s%s' % (comp, val, where, ' (traced through callers)' if inf and t else ' (from the name)' if t else ' (not resolved)',
                                                      ('; through anchor keys ' + ', '.join(anchors)) if anchors else ''))
            dev = lambda z: ix.fn[z][2].endswith('__LocalSetupSystem.sol')  # local-only setup, never deployed
            later = [z for z, op in all_sets.get(comp, []) if op == 'set' and not dev(z) and ix.fn[z][0] not in {ix.fn[c][0] for c in creators[et]}]
            removed = [z for z, op in all_sets.get(comp, []) if op == 'remove']
            if comp.startswith('IDOwns'):
                for z in later:
                    for st in flat_events(ix.events(z)):
                        if st[0] == 'access' and cname(st[1]) == comp and st[2] == 'set' and len(st) > 3 and len(st[3]) >= 2:
                            t, inf = resolve(z, st[3][-1].strip())
                            if t - types: how.append('%s = %s in %s, after creation%s' % (comp, st[3][-1].strip(), ix.key(z), ' (traced)' if inf else ''))
                            types |= t; inferred |= inf
            required = any(not conds for *_, conds in rows)
            l = {'to': sorted(types)[0] if len(types) == 1 else sorted(types) if types else None,
                 'type': rel_type(comp), 'stored_on': et, 'required': required}
            if not required:
                l['only_when'] = sorted({' and '.join(conds) for *_, conds in rows})[:3]
            unique = comp_users.get(comp, 0) == 1 or comp.startswith('IDOwns')
            if unique:
                l['changes_after_creation'] = bool(later)
                l['removable'] = bool(removed)
            vals = sorted({val for _, val, _, _ in rows})
            if any(v in id_keys for v in vals):
                rest = [k for k in id_keys if k not in vals]
                l['cardinality'] = 'one %s per %s%s' % (et, '/'.join(sorted(types)) or 'referenced entity', (' per ' + ', '.join(rest)) if rest else '')
            else:
                l['cardinality'] = 'many %s per %s' % (et, '/'.join(sorted(types)) or 'referenced entity')
            down = 'reverse lookup on %s (indexed)' % comp if indexed.get(comp) else 'not indexed: needs a scan or the indexer'
            if any(v in id_keys for v in vals) and id_formula:
                down = 'compute %s, or %s' % (id_formula, down)
            l['navigate'] = {'up': 'read %s on %s' % (comp, et), 'down': down}
            l['derived'] = list(dict.fromkeys(how))[:6]
            if via_keys: l['via'] = {'anchor_keys': sorted(via_keys)}
            if inferred: l['inferred'] = True
            if not types: l['unresolved_from'] = vals
            links[comp] = l
        # keyed references: the id is hashed from another entity's index or id (registry.scavenge("NODE", nodeIndex) -> NODE)
        for which, v in sorted(id_of[et].items()):
            occ = next(((o, k) for o, k in ns_struct[v] if o is not None), None)
            if not occ: continue
            ofid_, keys_ = occ
            by_tgt = {}  # target -> [keys that resolve to it], inferred, evidence; amm.pool(lo, hi) names two ITEMs
            for k in keys_:
                del via_anchor[:]; del trace_notes[:]
                t, inf = resolve(ofid_, k)
                t = {x for x in t if x in ents and x != et}
                if len(t) != 1: continue
                tgt = next(iter(t))
                if any(tgt in ([l['to']] if isinstance(l['to'], str) else l['to'] or []) for l in links.values() if l['type'] != 'keyed'): continue  # a stored pointer already says so
                if any(x in k.lower() for x in ('holder', 'owner')) and any(l['type'] == 'ownership' for l in links.values()): continue  # the ownership pointer is the holder relation, resolved or settled in notes
                ks, inf0, notes0 = by_tgt.get(tgt, ([], False, []))
                by_tgt[tgt] = (ks + [k], inf0 or inf, notes0 + [x for x in dict.fromkeys(trace_notes) if x not in notes0])
            for tgt, (ks, inf, notes) in by_tgt.items():
                rest = [x for x in keys_ if x not in ks]
                # amm.pool(lo, hi): one POOL per pair of ITEMs, not "per ITEM per ITEM"
                per = tgt if len(ks) == 1 else '%s of %ss (%s)' % ('pair' if len(ks) == 2 else 'set of %d' % len(ks), tgt, ', '.join(ks))
                traced = (' (traced through callers%s)' % (': ' + '; '.join(notes[:4]) if notes else '')) if inf else ''
                links['key:' + ','.join(ks)] = {'to': tgt, 'type': 'keyed', 'stored_on': et, 'required': True, 'key': ks[0] if len(ks) == 1 else ks,
                                                'cardinality': 'one %s per %s%s' % (et, per, (' per ' + ', '.join(rest)) if rest else ''),
                                                'navigate': {'up': 'the %s key%s of %s' % (', '.join(ks), 's' if len(ks) > 1 else '', v), 'down': 'compute %s(%s)' % (v, ', '.join(keys_))},
                                                'derived': ['%s key%s %s of %s(%s) resolve%s to %s%s' % (which, 's' if len(ks) > 1 else '', ', '.join(ks), v, ', '.join(keys_), '' if len(ks) > 1 else 's', tgt, traced)],
                                                **({'inferred': True} if inf else {})}
        if links: e['links'] = links
        e['components'] = [c for c in rec['components_at_creation'] if c != 'EntityType']
        model[et] = e

    # owns: invert ownership links (IDOwns*, IdHolder) onto the parent
    for et, e in model.items():
        for comp, l in e.get('links', {}).items():
            if not (comp.startswith('IDOwns') or comp == 'IdHolder'): continue
            for p in ([l['to']] if isinstance(l['to'], str) else l['to'] or []):
                if p in model:
                    model[p].setdefault('owns', [])
                    if et not in model[p]['owns']: model[p]['owns'].append(et)

    # sub-entities: every namespace that is not an entity id
    lib_entity = defaultdict(set)
    for et in ents:
        for c in creators[et]: lib_entity[stem(lib_of(c))].add(et)
    shared = {}
    for ns, occ in sorted(ns_struct.items()):
        if ns in ns_used: continue
        ofid, keys = next(((o, k) for o, k in occ if o is not None), (None, occ[0][1]))
        keyinfo, holders, kinf = {}, set(), {}
        for k in keys:
            t, inf = resolve(ofid, k) if ofid else (set(), True)
            keyinfo[k] = sorted(t)[0] if len(t) == 1 else sorted(t) if t else None
            kinf[k] = inf
            if any(x in k.lower() for x in ('holder', 'acc', 'kami', 'owner')) and t: holders |= t
        owner_lib = stem(lib_of(ofid)) if ofid else None
        lib_ents = sorted(lib_entity.get(owner_lib, []))
        runtime_holders = sorted(h for h in holders if h in model and model[h]['layer'] != 'registry')
        if len(runtime_holders) == 1: parent, how = runtime_holders[0], 'holder key'
        elif len(runtime_holders) > 1: parent, how = None, 'held by several types'
        elif len(lib_ents) == 1: parent, how = lib_ents[0], 'defined in %s' % lib_of(ofid)
        else:
            refs = sorted({v for k, v in keyinfo.items() if isinstance(v, str) and v in model and 'index' not in k.lower()})
            parent, how = (refs[0], 'key') if len(refs) == 1 else (None, 'no single parent')
        sub = {'id': '%s(%s)' % (ns, ', '.join(keys)), 'keys': keyinfo, 'parent_from': how,
               'relation': {'type': 'keyed', 'required': True,
                            'navigate': {'down': 'compute %s(%s) from the parent' % (ns, ', '.join(keys)), 'up': 'from the key values'}}}
        sub.update(ns_kind.get(ns, {}))
        # references the record itself stores (IDType on faction.reputation -> FACTION), and the holder -> target edge
        # they imply, drawn on the map as passing through the record
        holders_ = [parent] if parent else runtime_holders
        by_comp = defaultdict(list)
        for comp, where, val, nsv in sub.pop('pointers', []): by_comp[comp].append((where, val, nsv))
        for comp, entries in sorted(by_comp.items()):
            t, inf, notes, anchors = set(), False, [], set()
            for where, val, nsv in entries:
                fid_ = key_fid.get(where)
                del via_anchor[:]
                if nsv and nsv in ns_kind and ns_kind[nsv].get('kind') == 'anchor key':   # points at a grouping key, not an entity
                    anchors.add(nsv); notes.append('%s = %s in %s (an anchor key)' % (comp, val, where)); continue
                if comp in INDEX_TARGET: t_, inf_, note = {INDEX_TARGET[comp]}, False, 'component name: %s points at %s by index' % (comp, INDEX_TARGET[comp])
                elif nsv and nsv in claimed: t_, inf_, note = {claimed[nsv]}, False, '%s = %s in %s (the id of a %s)' % (comp, val, where, claimed[nsv])
                else:
                    t_, inf_ = resolve(fid_, val) if fid_ is not None else (set(), True)
                    note = '%s = %s in %s%s' % (comp, val, where, ' (traced through callers)' if inf_ and t_ else ' (from the name)' if t_ else ' (not resolved)')
                t |= {x for x in t_ if x in model}; inf |= inf_; notes.append(note)
            if anchors:   # the structural answer wins over a traced guess from another call site
                sub.setdefault('links', {})[comp] = {'to': sorted(anchors)[0] if len(anchors) == 1 else sorted(anchors), 'to_kind': 'anchor key', 'type': rel_type(comp),
                                                     'stored_on': ns, 'required': True, 'derived': list(dict.fromkeys(notes))[:4]}
                continue
            l = {'to': sorted(t)[0] if len(t) == 1 else sorted(t) if t else None, 'type': rel_type(comp), 'stored_on': ns, 'required': True, 'derived': list(dict.fromkeys(notes))[:4]}
            if inf: l['inferred'] = True
            if not t: l['unresolved_from'] = sorted({val for _, val, _ in entries})
            sub.setdefault('links', {})[comp] = l
            note = l['derived'][0]
            rest = [k for k in keys if not any(x in k.lower() for x in ('holder', 'acc', 'kami', 'owner'))]
            if comp == 'IdHolder' or comp.startswith('IDOwns'): continue   # the record's own holder: not a connection to another entity
            for h in holders_:
                for tgt in sorted(t):
                    if tgt == h or h not in model: continue
                    model[h].setdefault('links', {})['%s.%s' % (ns, comp)] = {
                        'to': tgt, 'type': rel_type(comp), 'stored_on': ns, 'via': {'record': ns}, 'required': False,
                        'only_when': ['a %s record exists for the %s' % (ns, h)],
                        'cardinality': 'one %s per %s%s' % (ns, h, (' per ' + ', '.join(rest)) if rest else ''),
                        'navigate': {'up': 'compute %s(%s), read %s' % (ns, ', '.join(keys), comp),
                                     'down': 'reverse lookup on %s (indexed)' % comp if indexed.get(comp) else 'not indexed: needs a scan or the indexer'},
                        'derived': ['%s stores %s; the record is keyed under %s' % (ns, note, h)], **({'inferred': True} if inf else {})}
        via_targets = {l['to'] for l in sub.get('links', {}).values() if isinstance(l.get('to'), str)}
        def via_edge(h, tgt, comp, typ, how, card_rest, inferred=False):
            if tgt == h or h not in model or tgt in via_targets: return
            model[h].setdefault('links', {})['%s.%s' % (ns, comp)] = {
                'to': tgt, 'type': typ, 'stored_on': ns, 'via': {'record': ns}, 'required': False,
                'only_when': ['a %s record exists for the %s' % (ns, h)],
                'cardinality': 'one %s per %s%s' % (ns, h, (' per ' + ', '.join(card_rest)) if card_rest else ''),
                'navigate': {'up': 'compute %s(%s)' % (ns, ', '.join(keys)), 'down': 'from the key values'}, 'derived': [how],
                **({'inferred': True} if inferred else {})}
        # keys naming another entity: scavenge.instance(field, index, holderID) -> index is a NODE
        for k, kv in keyinfo.items():
            if ns in POLYMORPHIC_KEYS or not isinstance(kv, str) or kv not in model or kv in holders_ or any(x in k.lower() for x in ('holder', 'acc', 'owner')): continue
            for h in holders_: via_edge(h, kv, 'key:' + k, 'keyed', 'key %s of %s(%s) resolves to %s%s' % (k, ns, ', '.join(keys), kv, ' (traced through callers)' if kinf.get(k) else ''), [x for x in keys if x != k and x not in ('holderID', 'accID')], kinf.get(k, False))
        # the same keys as a registry entry's id: the record is the holder's instance of that entry
        for v_, et_ in claimed.items():
            occ_ = next(((o, k) for o, k in ns_struct[v_] if o is not None), None)
            if not occ_ or not ofid or stem(lib_of(occ_[0])) != stem(lib_of(ofid)) or et_ in holders_: continue
            ek = [k for k in occ_[1]]
            if ek and set(ek) <= set(keys) and set(ek) != set(keys) and et_ in model:
                for h in holders_: via_edge(h, et_, 'keys', 'registry', 'keys %s of %s match the id %s(%s) of %s' % (', '.join(ek), ns, v_, ', '.join(ek), et_), [k for k in keys if k not in ('holderID', 'accID')])
        if parent:
            model[parent].setdefault('sub_entities', {})[ns] = sub
        else:
            sub['held_by'] = runtime_holders or None
            shared[ns] = {k: v for k, v in sub.items() if v is not None}
    # untyped shapes built on another shape's fields (a room gate is a condition plus pointers)
    def reach(fid, depth=0, seen=None):
        seen = set() if seen is None else seen
        if fid in seen or depth > 3: return seen
        seen.add(fid)
        for st in flat_events(ix.events(fid)):
            if st[0] == 'call': reach(st[1], depth + 1, seen)
        return seen
    for et in untyped:
        mine = {lib_of(c) for c in creators[et]}
        for other in untyped:
            if other == et: continue
            olibs = {lib_of(c) for c in creators[other]}
            if any(lib_of(z) in olibs and ix.summary(z)['writes'] for c in creators[et] for z in reach(c)) and not (mine & olibs):
                model[et]['extends'] = other

    # address-keyed records: components written at uint256(uint160(address))
    for z in ix.fn:
        for st in flat_events(ix.events(z)):
            if st[0] == 'access' and st[2] == 'set' and len(st) > 3 and len(st[3]) >= 2 and st[3][0].startswith('uint256(uint160('):
                inner = st[3][0][len('uint256(uint160('):].rstrip(')')
                name = ADDRESS_KEYED_NAMES.get(cname(st[1]), 'address.' + cname(st[1]))
                t, inf = resolve(z, st[3][1].strip())
                parent = next(iter(t)) if len(t) == 1 else None
                rec_ = {'id': 'address(%s)' % inner, 'kind': 'record', 'fields': [cname(st[1])], 'keys': {inner: 'wallet address'},
                        'parent_from': 'value of %s set in %s' % (cname(st[1]), ix.key(z)),
                        'relation': {'type': 'keyed', 'required': True, 'navigate': {'down': 'read %s at the address' % cname(st[1]), 'up': 'its value'}}}
                if parent: model[parent].setdefault('sub_entities', {})[name] = rec_
                else: shared[name] = rec_

    # columns written after creation: every component write, anywhere a system can reach, whose id argument resolves to
    # the entity type (kamiID -> KAMI, or traced through callers). An indexer's table for the type needs these columns
    # too; the creation list alone misses a kami's trait indexes or an account's name. Recorded with where they are set.
    if reach_fids:
        later, later_in = defaultdict(set), defaultdict(lambda: defaultdict(set))
        for z in reach_fids:
            if z not in ix.fn or ix.fn[z][2].startswith('src/solecs/'): continue
            for st in flat_events(ix.events(z)):
                # a value being set is evidence of a column; a bare remove is not (LibItem.remove clears every component
                # an entry might have, including ones no item ever gets)
                if st[0] != 'access' or not st[2].startswith(('set', 'inc', 'dec', 'push', 'extend', 'transfer')) or len(st) < 4 or not st[3]: continue
                comp = cname(st[1])
                if comp == 'EntityType' or comp.startswith('@') or '[' in comp: continue
                idarg = st[3][0].strip()
                if idarg.startswith('uint256(uint160('): continue   # address-keyed records, handled below
                t, inf = resolve(z, idarg)
                if os.environ.get('ATLAS_DEBUG_COLS') and comp.startswith(os.environ['ATLAS_DEBUG_COLS']):
                    print('  cols: %s writes %s with id %r -> %s' % (ix.key(z), comp, idarg, sorted(t) or 'unresolved'))
                for et in t:
                    if et in model: later[et].add(comp); later_in[et][comp].add(ix.key(z))
        for et, e in model.items():
            extra = sorted(c for c in later.get(et, ()) if c not in e['components'])
            if extra:
                e['components_later'] = extra
                e['components_later_in'] = {c: sorted(later_in[et][c])[:4] for c in extra}

    order = sorted(model, key=lambda k: ({'runtime': 0, 'hybrid': 1, 'registry': 2}[model[k]['layer']], k))
    model_out = {'relationship_types': REL_TYPES, 'entities': {k: model[k] for k in order}, 'shared_sub_entities': shared}
    if singletons: model_out['singletons'] = singletons
    return model_out


def source_rev():
    """The contracts checkout's commit (and a -dirty mark), so every output says exactly what it was built from."""
    try:
        sha = subprocess.run(['git', 'rev-parse', '--short=12', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain', '--', '.'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return (sha or 'unknown') + ('-dirty' if dirty else '')
    except OSError:
        return 'unknown'


def source_label():
    """Where the contracts live, as a path a reader would recognise (the worktree is an implementation detail)."""
    return os.path.relpath(ROOT, os.path.dirname(HERE)) if REF == 'local' else 'kamigotchi/packages/contracts'


def source_ref():
    """origin/<ref> for a worktree build; the checked-out branch for a local one."""
    if REF != 'local': return 'origin/' + REF
    try: return subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip() or 'local'
    except OSError: return 'local'


def source_web():
    """The contracts' GitHub web URL and their path in that repo, so the atlas can link to the exact commit."""
    try:
        url = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        top = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        return None, None
    m = re.match(r'(?:git@github\.com:|https://github\.com/)([^/]+/[^/]+?)(?:\.git)?$', url)
    repo = m.group(1) if m else None
    # the atlas is public and links to commits: point at the public mirror of main (same commits), not the private repo
    if repo == 'Asphodel-OS/kamigotchi': repo = 'Asphodel-OS/kamigotchi-open'
    return ('https://github.com/' + repo if repo else None), (os.path.relpath(ROOT, top) if top else None)


def read_note_section(path, section):
    """One top-level section of notes/domain-model.yaml whose entries are flow mappings (`links:`, `sub_entities:`),
    as {'key': {field: value}}: a small reader, since the generator keeps to the standard library."""
    if not os.path.exists(path): return {}
    out, inside = {}, False
    for line in open(path):
        if re.match(r'^\w', line): inside = line.startswith(section + ':'); continue
        if not inside: continue
        m = re.match(r'^  ([\w.:,]+):\s*\{(.*)\}\s*(#.*)?$', line)
        if not m: continue
        d = {}
        for k, v in re.findall(r'(\w+):\s*("(?:[^"\\]|\\.)*"|[^,}]+)', m.group(2)):
            v = v.strip()
            d[k] = json.loads(v) if v.startswith('"') else (v == 'true' if v in ('true', 'false') else v)
        out[m.group(1)] = d
    return out


def fold_note_links(entities, note_links):
    """Apply the notes' link settlements to the model, so the generated files say what the atlas shows: a `to:` the code
    could not give is set and marked settled_in_notes (what the code gave stays in unresolved_from), notes ride along,
    and pointers the notes say are not a link are dropped. Returns the dropped ones."""
    ignored = []
    for key, o in note_links.items():
        et, comp = key.split('.', 1)
        links = (entities.get(et) or {}).get('links') or {}
        l = links.get(comp)
        if l is None: continue
        if o.get('ignore'):
            ignored.append({'link': key, **({'note': o['note']} if o.get('note') else {})}); del links[comp]; continue
        if o.get('to'):
            ts = [t.strip() for t in str(o['to']).split('|') if t.strip()]
            code = [l['to']] if isinstance(l.get('to'), str) else list(l.get('to') or [])
            if code and sorted(code) != sorted(ts): print('notes override a link the code resolves: %s is %s in code, %s in notes' % (key, '/'.join(sorted(code)), '/'.join(ts)))
            l['to'] = ts[0] if len(ts) == 1 else ts
            l['settled_in_notes'] = True
            if l.get('cardinality'): l['cardinality'] = l['cardinality'].replace('referenced entity', '/'.join(ts))
        if o.get('note'): l['note'] = o['note']
    return ignored


def diff_model(old, new):
    """Changed leaves between two domain models, as (+ - ~, path, before, after), ignoring the source stamp."""
    def flat(d, p=''):
        if isinstance(d, dict): return {k2: v2 for k, v in d.items() for k2, v2 in flat(v, p + '/' + str(k)).items()}
        return {p: json.dumps(d, sort_keys=True)}
    a, b = flat(old), flat(new)
    return [(('+' if k not in a else '-' if k not in b else '~'), k, a.get(k), b.get(k))
            for k in sorted(set(a) | set(b)) if not k.startswith('/source') and a.get(k) != b.get(k)]


def main():
    # one run at a time: dev.py regenerates on every notes change, and a manual run alongside it would interleave
    # writes to generated/ and viz/data/. The second run waits for the first instead.
    os.makedirs(os.path.join(HERE, '.cache'), exist_ok=True)
    lock = open(os.path.join(HERE, '.cache', 'generate.lock'), 'w')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print('another generate.py is running; waiting for it to finish…', flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
    if '--no-build' not in sys.argv: build()
    ix = Index(load_units())
    deploy = json.load(open(os.path.join(ROOT, 'deploy.json')))
    dcomp = {c['comp']: c for c in deploy['components']}
    dsys = {s['name']: s for s in deploy['systems']}
    protected = set(deploy.get('protectedComponents', []))

    # systems
    systems, comp_w, comp_r, reach_roots, sys_entry_fids = {}, defaultdict(set), defaultdict(set), set(), defaultdict(list)
    player_entry_fids = defaultdict(list)
    for path in sorted(p for p in ix.units if p.startswith('src/systems/') and p.endswith('System.sol')):
        name = os.path.basename(path)[:-len('System.sol')]
        raw = ix.src[path].decode()
        contract = next((n for n in ix.units[path]['nodes'] if n['nodeType'] == 'ContractDefinition'), None)
        if not contract: continue
        fns = {f['name']: f for f in contract['nodes'] if f['nodeType'] == 'FunctionDefinition' and f['visibility'] in ('public', 'external') and f['kind'] == 'function'}
        wrappers = {n for n, f in fns.items() if n != 'execute' and any(
            x.get('nodeType') == 'Identifier' and x.get('name') in ('execute', 'executeTyped') for x in walk(f.get('body') or {}))}
        entries = [n for n in fns if n not in wrappers] or list(fns)
        idm = re.search(r'keccak256\("(system\.[^"]+)"\)', raw)
        typed = fns.get('executeTyped')
        args = ', '.join('%s %s' % (p['typeDescriptions']['typeString'].replace('contract ', ''), p['name'])
                         for p in (typed or fns.get('execute', {'parameters': {'parameters': []}}))['parameters']['parameters'])
        allr, allw, rv, traces, keys = set(), set(), [], {}, defaultdict(set)
        own = {f['id'] for f in contract['nodes'] if f['nodeType'] == 'FunctionDefinition'}
        internal, todo = [], []
        for e in entries:
            s = ix.summary(fns[e]['id'])
            allr |= s['reads']; allw |= s['writes']; rv += [x for x in s['reverts'] if x not in rv]
            for k, v in s['keys'].items(): keys[k] |= v
            traces[e] = steps(ix, fns[e]['id'])
            sys_entry_fids[name].append(fns[e]['id'])
            mods = {m['modifierName'].get('name') for m in fns[e].get('modifiers', []) if m.get('modifierName')}
            if not name.startswith('_') and not (mods & ADMIN_MODIFIERS):
                player_entry_fids[name].append(fns[e]['id'])
            todo += called(ix, fns[e]['id'])
        while todo:  # the system contract's own helpers (e.g. _collect) live in its file; library calls go to functions/
            x = todo.pop(0)
            if x in own and x not in internal and ix.fn[x][1]['name'] not in fns:
                internal.append(x); todo += called(ix, x)
            elif x not in own:
                reach_roots.add(x)
        allw = {cname(c) for c in allw}
        allr = {cname(c) for c in allr} - allw
        d = dsys.get(name + 'System', {})
        wa = d.get('writeAccess', [])
        for c in allw: comp_w[c].add(name)
        for c in allr: comp_r[c].add(name)
        rec = {
            'id': idm.group(1) if idm else None,
            'file': path,
            'comment': header_comment(raw),
            'args': args or None,
            'entry_points': entries,
            'also': sorted(wrappers) or None,
            'deployed': bool(d),
            'skip_env': d.get('skipEnv'),
            'writes': sorted(allw),
            'reads': sorted(allr),
            'write_access': '*' if wa == ['*'] else None,  # otherwise only the differences below
        }
        for k in ('events', 'stats', 'config', 'bonuses', 'flags', 'data'):
            if keys.get(k): rec[k] = sorted(keys[k])
        disabled = [ev[0][1] for e in entries for ev in [ix.events(fns[e]['id'])] if ev and ev[0][0] == 'revert']
        if disabled and len(disabled) == len(entries): rec['disabled'] = disabled[0]  # every entry point reverts before doing anything
        if wa and wa != ['*']:
            missing = sorted(c for c in allw if c not in wa and '[' not in c and c not in protected)
            unused = sorted(c for c in wa if c not in allw)
            if missing:
                rec['writes_without_access'] = missing
                rec['writes_without_access_paths'] = {c: write_path(ix, sys_entry_fids[name], c) for c in missing}
            if unused: rec['access_not_written'] = unused
        rec = {k: v for k, v in rec.items() if v not in (None, [], False) or k == 'deployed'}
        rec['steps'] = traces if len(traces) > 1 else next(iter(traces.values()), [])
        if internal: rec['internal'] = {ix.key(x): steps(ix, x) for x in internal}
        systems[name] = rec

    # components
    comps = {}
    for path in sorted(p for p in ix.units if re.match(r'src/components/\w+Component\.sol$', p)):
        name = os.path.basename(path)[:-len('Component.sol')]
        raw = ix.src[path].decode()
        idm = re.search(r'keccak256\("([^"]+)"\)', raw)
        base = re.search(r'contract\s+\w+\s+is\s+(\w+)', raw)
        dc = dcomp.get(name, {})
        comps[name] = {k: v for k, v in {
            'id': idm.group(1) if idm else None,
            'client_name': dc.get('name') if dc.get('name') != name else None,
            'type': dc.get('type') or (base.group(1) if base else None),
            'indexed': dc.get('indexed') or None,
            'protected': (name in protected) or None,
            'comment': header_comment(raw),
            'written_by': sorted(comp_w.get(name, [])),
            'read_by': sorted(comp_r.get(name, [])),
        }.items() if v not in (None, [])}

    # entity types: where LibEntityType.set("X") is called, and what that creator writes
    entities = defaultdict(lambda: {'created_in': set(), 'components_at_creation': set(), 'checked_in': set()})
    for fid, (c, f, path) in ix.fn.items():
        for st in flat_events(ix.events(fid)):
            if st[0] != 'call' or not st[2]: continue
            callee = ix.name(st[1])
            if callee in ('LibEntityType.set', 'LibEntityType.checkAndSet'):
                e = entities[st[2][0]]
                e['created_in'].add(ix.name(fid))
                e['components_at_creation'] |= {x for x in ix.summary(fid)['writes'] if not x.startswith('@')}
            elif callee.startswith('LibEntityType.') and callee != 'LibEntityType.remove':
                entities[st[2][0]]['checked_in'].add(ix.name(fid))
    # untyped shapes: library functions that take an id from the World's counter and write components, with no EntityType
    untyped = set()
    for fid, (c, f, path) in ix.fn.items():
        if not path.startswith('src/libraries/'): continue
        evs = list(flat_events(ix.events(fid)))
        if not any(st[0] == 'call' and ix.name(st[1]).endswith('.getUniqueEntityId') for st in evs): continue
        if any(st[0] == 'call' and ix.name(st[1]).startswith('LibEntityType.') and 'set' in ix.name(st[1]).lower() for st in evs): continue
        writes = {x for x in ix.summary(fid)['writes'] if not x.startswith('@')}
        if not writes: continue
        name = SHAPE_NAMES.get(ix.name(fid)) or SHAPE_NAMES.get(c) or re.sub(r'^Lib', '', c).upper()
        entities[name]['created_in'].add(ix.name(fid))
        entities[name]['components_at_creation'] |= writes
        untyped.add(name)

    # singletons: entities at a fixed id, uint256 constant X = uint256(keccak256("name"))
    singletons = {}
    for path, raw in ix.src.items():
        if not path.startswith(('src/libraries/', 'src/systems/')): continue
        for m in re.finditer(rb'uint256\s+(?:internal\s+)?constant\s+(\w+)\s*=\s*uint256\(\s*keccak256\(\s*"([a-z][\w.]*)"\s*\)\s*\)', raw):
            const, lit = m.group(1).decode(), m.group(2).decode()
            if lit.startswith(('system.', 'component.')): continue
            name = SINGLETON_NAMES.get(lit, lit.upper().replace('.', '_'))
            used = sorted({os.path.basename(p_)[:-4] for p_, r_ in ix.src.items() if p_.startswith(('src/libraries/', 'src/systems/'))
                           and re.search(rb'\b' + const.encode() + rb'\b', r_)})
            singletons[name] = {'id': 'keccak256("%s")' % lit, 'constant': const, 'defined_in': path, 'used_in': used}

    ents = {k: {kk: sorted(vv) for kk, vv in v.items()} for k, v in sorted(entities.items())}
    creators = {k: [f for f in ix.fn if ix.name(f) in v['created_in']] for k, v in ents.items()}

    # per-system reachable functions (for formulas' used_by)
    closure_cache = {}
    def closure(fid):
        if fid in closure_cache: return closure_cache[fid]
        seen_, todo_ = set(), [fid]
        while todo_:
            z = todo_.pop()
            if z in seen_: continue
            seen_.add(z); todo_ += called(ix, z)
        closure_cache[fid] = seen_
        return seen_
    sys_reach = {n: set().union(*[closure(f) for f in fids]) for n, fids in sys_entry_fids.items()}
    player_reach = {n: set().union(*[closure(f) for f in fids]) for n, fids in player_entry_fids.items()}

    # entities: who creates them, and what their link components (ID*/Id*/Index*) are set from
    LINK = re.compile(r'^(ID|Id|Index)[A-Z]')
    for et, rec in ents.items():
        by = sorted({n for n, r in sys_reach.items() if any(c in r for c in creators[et])
                     and 'production' not in (systems[n].get('skip_env') or [])})  # leave out local-only setup
        player = [n for n in by if any(c in player_reach.get(n, ()) for c in creators[et])]  # through a non-admin entry point
        rec['made_by'] = 'player actions' if player and len(player) == len(by) else 'admin/registry setup' if not player else 'both'
        rec['created_by_systems'] = by
        rec['created_by_players'] = player
        links = defaultdict(list)
        for c in creators[et]:
            for z in sorted(closure(c), key=ix.key):
                if ix.fn[z][0] != ix.fn[c][0]: continue  # the entity's own library: helpers elsewhere make other entities
                for st in flat_events(ix.events(z)):
                    if st[0] == 'access' and st[2] in ('set', 'setBatch') and LINK.match(cname(st[1])) and len(st) > 3 and len(st[3]) >= 2:
                        v = '%s  (in %s)' % (st[3][-1], ix.key(z))
                        if v not in links[cname(st[1])]: links[cname(st[1])].append(v)
        if links: rec['links_set_at_creation'] = dict(sorted(links.items()))

    # hashed ID namespaces: keccak256(abi.encodePacked("quest.instance", ...)) and the like; untyped entity kinds included
    namespaces, ns_struct = defaultdict(set), defaultdict(list)
    for path in ix.units:
        if not path.startswith(('src/libraries/', 'src/systems/')): continue
        fids = [(f['src'], ix.key(fid)) for fid, (c, f, p_) in ix.fn.items() if p_ == path]
        raw = ix.src[path]
        for m in re.finditer(rb'keccak256\(\s*abi\.encode(?:Packed)?\(', raw):
            args = split_args(raw, m.end())
            lits = [a for a in args if re.fullmatch(r'"[a-zA-Z][a-zA-Z0-9._]*"', a)]
            if not lits: continue  # randomness, not an id
            ns = lits[0].strip('"')
            keys = [a for a in args if a != lits[0]]
            owner = next((k for src_, k in fids if int(src_.split(':')[0]) <= m.start() < sum(map(int, src_.split(':')[:2]))), None)
            namespaces[ns].add('%s(%s)  in %s' % (ns, ', '.join(keys), owner or os.path.basename(path)[:-4]))
            ofid = next((fid for fid, (c_, f_, p_) in ix.fn.items() if p_ == path and int(f_['src'].split(':')[0]) <= m.start() < sum(map(int, f_['src'].split(':')[:2]))), None)
            ns_struct[ns].append((ofid, keys))
    id_ns = {k: sorted(v) for k, v in sorted(namespaces.items())}
    ns_kind, gen_ns = classify_namespaces(ix, ns_struct)

    # untyped shapes reachable from no deployed system are dead code: leave them out, but say so in the output
    dropped = {}
    for et in list(untyped):
        if not any(any(c in r for c in creators[et]) for n, r in sys_reach.items() if not n.startswith('__')):
            dropped[et] = 'untyped shape made in %s; no deployed system reaches it%s' % (', '.join(sorted(ents[et]['created_in'])), ('; ' + DEPRECATED[et]) if et in DEPRECATED else '')
            untyped.discard(et); ents.pop(et); creators.pop(et)
    repo, repo_path = source_web()
    model_meta = {'contracts': source_label(), 'ref': source_ref(), 'commit': source_rev(), 'repo': repo, 'repo_path': repo_path,
                  'left_out': dropped}
    # every library function reachable from a system (the function table, and the columns pass in derive_domain)
    reach, todo = set(), list(reach_roots)
    while todo:
        x = todo.pop()
        if x in reach: continue
        reach.add(x); todo += called(ix, x)
    model = derive_domain(ix, ents, creators, ns_struct, systems, {c['comp']: bool(c.get('indexed')) for c in deploy['components']},
                          untyped, singletons, ns_kind, gen_ns, reach_fids=reach)

    # function table: written once per library
    tables = defaultdict(dict)
    for x in sorted(reach, key=ix.key):
        c, f, path = ix.fn[x]
        if path.startswith('src/solecs/') or ix.contract_kind.get(c) == 'interface': continue
        sm = ix.summary(x)
        ps = [p_ for p_ in f['parameters']['parameters'] if short_type(p_) not in AMBIENT]
        sig = '(%s) %s' % (', '.join('%s %s' % (short_type(p_), p_['name']) for p_ in ps),
                           ' '.join(v for v in (f['visibility'], f['stateMutability']) if v not in ('nonpayable',)))
        ent = {'sig': sig}
        own_w = {st[1] for st in flat_events(ix.events(x)) if st[0] == 'access' and st[2].startswith(WRITE_PREFIXES)}
        if sm['writes'] - own_w: ent['writes'] = sorted(map(cname, sm['writes']))  # incl. callees' writes
        if sm['keys'].get('events'): ent['events'] = sorted(sm['keys']['events'])
        st = steps(ix, x)
        if st: ent['steps'] = st
        tables[c][ix.key(x)] = ent

    # formulas: the calc* functions' code and inputs
    formulas = {}
    for x in sorted((x for x in ix.fn if (ix.fn[x][1]['name'] or '').startswith('calc') and ix.fn[x][1].get('body')), key=ix.key):
        sm = ix.summary(x)
        ent = {'file': ix.fn[x][2]}
        for k in ('stats', 'config', 'bonuses'):
            if sm['keys'].get(k): ent[k] = sorted(sm['keys'][k])
        ent['used_by'] = sorted(n for n, r in sys_reach.items() if x in r) or None
        ent['code'] = code_of(ix, x)
        formulas[ix.key(x)] = {k: v for k, v in ent.items() if v}

    # write
    os.makedirs(GEN, exist_ok=True)
    for sub in ('systems', 'functions'):
        d_ = os.path.join(GEN, sub)
        for f in os.listdir(d_) if os.path.isdir(d_) else []: os.remove(os.path.join(d_, f))
    src_note = 'the compiler AST of %s (%s) at %s' % (source_label(), source_ref(), source_rev())
    for name, rec in systems.items():
        write(os.path.join(GEN, 'systems', name + '.yaml'), src_note, {name: rec})
    for c, tbl in tables.items():
        write(os.path.join(GEN, 'functions', c + '.yaml'), src_note, tbl)
    write(os.path.join(GEN, 'formulas.yaml'), src_note, formulas)
    write(os.path.join(GEN, 'components.yaml'), src_note, comps)
    write(os.path.join(GEN, 'entities.yaml'), src_note, {'entity_types': ents, 'id_namespaces': id_ns})
    ignored_links = fold_note_links(model['entities'], read_note_section(os.path.join(NOTES, 'domain-model.yaml'), 'links'))
    for ns_, o in read_note_section(os.path.join(NOTES, 'domain-model.yaml'), 'sub_entities').items():  # held_by: who holds a shared record, when the code can't say
        if o.get('held_by') and ns_ in model.get('shared_sub_entities', {}):
            model['shared_sub_entities'][ns_]['held_by'] = [h.strip() for h in str(o['held_by']).split('|') if h.strip()]
            model['shared_sub_entities'][ns_]['held_by_from_notes'] = True
    model = {'source': model_meta, **model, **({'links_ignored_in_notes': ignored_links} if ignored_links else {})}
    if '--diff' in sys.argv:  # what this run changed against a committed output (default HEAD) before overwriting it
        i = sys.argv.index('--diff'); ref = sys.argv[i + 1] if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith('-') else 'HEAD'
        try:
            old = json.loads(subprocess.run(['git', 'show', '%s:generated/domain-model.json' % ref], cwd=HERE, capture_output=True, text=True, check=True).stdout)
            changes = diff_model(old, model)
            print('diff against %s: %d changed value%s' % (ref, len(changes), '' if len(changes) == 1 else 's'))
            for sign, k, a_, b_ in changes: print('  %s %s: %s -> %s' % (sign, k, (a_ or '')[:100], (b_ or '')[:200]))
        except subprocess.CalledProcessError as ex: print('diff: cannot read %s:generated/domain-model.json (%s)' % (ref, ex.stderr.strip()))
    write(os.path.join(GEN, 'domain-model.yaml'), src_note, model)
    with open(os.path.join(GEN, 'domain-model.json'), 'w') as f_: json.dump(model, f_, indent=1)
    spec = write_spec(ix, model, ents, comps, systems, id_ns, src_note)

    # data for the visualizer (viz/): the model, a compact per-system summary, and the notes as written
    vd = os.path.join(HERE, 'viz', 'data')
    os.makedirs(vd, exist_ok=True)
    with open(os.path.join(vd, 'domain-model.json'), 'w') as f_: json.dump(model, f_)
    with open(os.path.join(vd, 'systems.json'), 'w') as f_:
        json.dump({n: {k: r.get(k) for k in ('comment', 'args', 'writes', 'reads', 'events', 'config', 'deployed', 'disabled', 'writes_without_access', 'writes_without_access_paths') if r.get(k) is not None}
                   for n, r in systems.items()}, f_)
    with open(os.path.join(vd, 'components.json'), 'w') as f_:
        json.dump({n: {k: c.get(k) for k in ('type', 'comment') if c.get(k)} for n, c in comps.items()}, f_)
    if os.path.exists(os.path.join(NOTES, 'domain-model.yaml')):
        with open(os.path.join(NOTES, 'domain-model.yaml')) as a_, open(os.path.join(vd, 'notes.yaml'), 'w') as b_: b_.write(a_.read())
    lay = os.path.join(NOTES, 'layout.json')   # hand-tuned overview positions, exported from the atlas's Settings
    if os.path.exists(lay):
        with open(lay) as a_, open(os.path.join(vd, 'layout.json'), 'w') as b_: b_.write(a_.read())

    # coverage report (notes are only checked for presence, never edited)
    noted_sys = {f[:-5] for f in os.listdir(os.path.join(NOTES, 'systems'))} if os.path.isdir(os.path.join(NOTES, 'systems')) else set()
    noted_comp = set(re.findall(r'^(\w+):', open(os.path.join(NOTES, 'components.yaml')).read(), re.M)) if os.path.exists(os.path.join(NOTES, 'components.yaml')) else set()
    unresolved = sum(1 for r in systems.values() for c in r.get('writes', []) + r.get('reads', []) if '[' in c)
    print('components %d · systems %d · entity types %d · functions in table %d (%d libraries) · formulas %d' %
          (len(comps), len(systems), len(ents), len(reach), len(tables), len(formulas)))
    print('notes: %d/%d systems, %d/%d components' % (len(noted_sys & set(systems)), len(systems), len(noted_comp & set(comps)), len(comps)))
    print('unresolved component refs (Stat[by name]/Component[by id]): %d' % unresolved)
    dm = os.path.join(NOTES, 'domain-model.yaml')
    txt = open(dm).read() if os.path.exists(dm) else ''
    no_what = [t for t in model['entities'] if not re.search(r'^  %s:.*what:|^  %s:\n    what:' % (t, t), txt, re.M)]
    open_links = ['%s.%s' % (t, c) for t, e in model['entities'].items() for c, l in (e.get('links') or {}).items()
                  if not l.get('to') and ('%s.%s:' % (t, c)) not in txt]
    n_links = sum(len(e.get('links') or {}) for e in model['entities'].values())
    print('domain model: %d entities (%d without a description in notes), %d links (%d unresolved and not settled in notes)%s' % (
        len(model['entities']), len(no_what), n_links, len(open_links), (': ' + ', '.join(no_what + open_links)) if no_what or open_links else ''))
    drift = {n: r['writes_without_access'] for n, r in systems.items() if 'writes_without_access' in r}
    if drift: print('writes without deploy.json access:', drift)
    return 0


if __name__ == '__main__':
    sys.exit(main())
