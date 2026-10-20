#!/usr/bin/env python3
"""Generate the domain schema for Kamigaze from the recognition spec, as one goose migration.

    python3 schema.py [--spec generated/recognition.json] [--out <migration.sql>]

What it makes, all under a `domain` schema in PostgreSQL:
  domain.component          the components by on-chain id, with name and value type (so nothing depends on ecs.components.key)
  domain.kind               every entity type, record kind and untyped shape the spec knows, with its table name
  domain.entity             what each entity is: kind, parent, how it was recognised, first and last block
  domain.system             the systems by on-chain id, with name (ecs.systems has no names)
  domain.record_spec        per record kind: id encoding, key order, types, parents, literals, patterns, fields
  domain.shape_spec         per untyped shape: component signature and creating systems
  domain.singleton          fixed ids (keccak of a constant) that are keys of records
  domain.<type>             one table per entity type: id, block_number, one typed column per component
                            (creation columns and the ones written later), Stat as four columns, Coord as three
  domain.rec_<namespace>    one table per record kind: id, parent, the keys, the fields
  domain.refresh_<table>()  recomputes the rows of the given ids from ecs.state (the populator calls it per block range)
  domain.outbox             what changed, per block and transaction: kind, id, columns; the feed for subscriptions
  domain.bootstrap()        fills the entity-type tables from ecs.state (current values); records are filled by the
                            per-transaction populator, which has the context their keys need
Decoding helpers (domain.*) are self-contained plpgsql: uint, bigint and integer (range-guarded), signed bigint, timestamptz,
int32, bool, string, address, Stat, Coord, uint arrays. Column types: INTEGER_UINT, BIGINT_UINT, TIMESTAMP_UINT, VALUE_TYPES, WEI_KINDS.
The migration is a pure function of the spec; its header names the contracts commit the spec came from.
Standard library only.
"""
import argparse, datetime, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import keccak


RESERVED = {'all', 'and', 'any', 'as', 'asc', 'both', 'case', 'check', 'column', 'create', 'default', 'desc', 'else', 'end', 'false', 'for', 'from',
            'grant', 'group', 'in', 'into', 'is', 'limit', 'not', 'null', 'offset', 'on', 'only', 'or', 'order', 'primary', 'references', 'select',
            'some', 'table', 'then', 'to', 'true', 'union', 'user', 'values', 'when', 'where', 'with'}   # SQL reserved words: suffixed with _


def snake(name):
    s = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', '_', name).lower()
    s = re.sub(r'[^a-z0-9_]', '_', s)
    return s + '_' if s in RESERVED else s


def table_name(kind, is_record):
    return ('rec_' + snake(kind.replace('.', '_'))) if is_record else snake(kind)


POINTER = re.compile(r'^(ID|Id)[A-Z]')


# Column types, decided per component with Acheron on 2026-10-19 from where each one is written and read (kami-atlas
# README: Domain schema). One type per component wherever it lands; Value is the one exception, typed per kind.
INTEGER_UINT = {'Level', 'SkillPoint', 'Rarity', 'Cost', 'Max', 'Reroll', 'BlockReveal', 'Time'}   # bounded by design; Time is
                                                                                                    # seconds (a duration or an instant)
BIGINT_UINT = {'Experience', 'Rate'}                                                                # lifetime accumulator; fixed-point rates
TIMESTAMP_UINT = {'TimeStart', 'TimeLast', 'TimeLastAction', 'TimeEnd', 'TimeNext', 'TimeReset'}    # always block.timestamp-like instants
# Value is overloaded: a count or amount on most kinds (bigint), but signed on bonuses, packed on allocations and configs, a
# hash on room gates, a count or a component id on conditions, and an ERC-20 amount (wei) on the kami market, pools and
# the token portal. Measured on the world-creation slice; kinds not listed get bigint.
VALUE_TYPES = {
    'bonus': ('bigint', 'domain.to_int_bigint(v)'),          # int256 cast to uint256 by LibBonus: negatives
    'allocation': ('bytea', 'domain.id32(v)'),               # packed: two 128-bit halves
    'condition': ('numeric', 'domain.to_uint(v)'),           # a count (ITEM, LEVEL) or a component id (COMPLETE_COMP)
    'room_gate': ('bytea', 'domain.id32(v)'),                # a 32-byte hash
    'is.config': ('bytea', 'domain.id32(v)'),                # anything: number, bytes32 string, address, timestamp
}
WEI_KINDS = {'kami_listing', 'kami_offer', 'kami_collection_offer', 'pool', 'pool_share', 'token_receipt'}   # Value, Tax: token amounts


def columns_for(comp, ctype, owner=None):
    """(column name, sql type, decoding expression over value v) list for one component of one entity type or record
    kind (owner: the spec's name, None when the owner does not matter)."""
    c = snake(comp)
    if ctype == 'Stat':
        return [(c + '_' + part, 'integer', 'domain.stat_part(v, %d)' % i) for i, part in enumerate(('base', 'shift', 'boost', 'sync'))]
    if ctype == 'Coord':
        return [(c + '_' + part, 'integer', 'domain.coord_part(v, %d)' % i) for i, part in enumerate(('x', 'y', 'z'))]
    if ctype == 'uint256':
        if POINTER.match(comp): return [(c, 'bytea', 'domain.id32(v)')]   # an entity id: keep the 32 bytes for joins
        kind = owner if owner is None or '.' in owner else snake(owner)   # record namespace as is, entity type as its table name
        if comp == 'Value' and kind in VALUE_TYPES: return [(c,) + VALUE_TYPES[kind]]
        if kind in WEI_KINDS and comp in ('Value', 'Tax'): return [(c, 'numeric', 'domain.to_uint(v)')]
        if comp == 'Value' or comp in BIGINT_UINT: return [(c, 'bigint', 'domain.to_bigint(v)')]
        if comp in INTEGER_UINT: return [(c, 'integer', 'domain.to_integer(v)')]
        if comp in TIMESTAMP_UINT: return [(c, 'timestamptz', 'domain.to_timestamp(v)')]
        return [(c, 'numeric', 'domain.to_uint(v)')]
    if ctype in ('uint32', 'uint8', 'uint16'): return [(c, 'integer', 'domain.to_integer(v)')]
    if ctype == 'uint64': return [(c, 'bigint', 'domain.to_bigint(v)')]
    if ctype in ('int32', 'int8', 'int16', 'int64'): return [(c, 'integer' if ctype != 'int64' else 'bigint', 'domain.to_int32(v)' if ctype == 'int32' else 'domain.to_uint(v)::bigint')]
    if ctype == 'bool': return [(c, 'boolean', 'domain.to_bool(v)')]
    if ctype == 'string': return [(c, 'text', 'domain.to_string(v)')]
    if ctype == 'address': return [(c, 'bytea', 'domain.to_address(v)')]
    if ctype in ('uint32[]', 'uint8[]', 'uint16[]'): return [(c, 'integer[]', 'domain.to_uint_array(v)::integer[]')]
    if ctype in ('uint256[]', 'int32[]'): return [(c, 'numeric[]', 'domain.to_uint_array(v)')]
    if ctype == 'string[]': return [(c, 'text[]', 'domain.to_string_array(v)')]
    return [(c, 'bytea', 'v')]   # unknown: keep the raw bytes


def key_column(key, ktype, parents):
    k = 'key_' + snake(key).rstrip('_')   # has.flag(id, flagType): the key "id" would collide with the row id
    if ktype == 'uint256': return (k, 'bytea' if parents or key.lower().endswith('id') else 'integer')   # epochs, expiries
    if ktype and ktype.startswith('uint'): return (k, 'integer')
    if ktype == 'string': return (k, 'text')
    if ktype == 'address': return (k, 'bytea')
    return (k, 'bytea')


HELPERS = r'''
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION domain.id32(v bytea) RETURNS bytea LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT CASE WHEN length(v) >= 32 THEN substring(v from length(v) - 31 for 32)
              ELSE decode(repeat('00', 32 - length(v)), 'hex') || v END $$;
-- +goose StatementEnd
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION domain.to_uint(v bytea) RETURNS numeric LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE r numeric := 0; i int; b bytea := domain.id32(v);
BEGIN
  FOR i IN 0..31 LOOP r := r * 256 + get_byte(b, i); END LOOP;
  RETURN r;
END $$;
-- +goose StatementEnd
-- +goose StatementBegin
-- a uint256 that is a small number on chain; a value past bigint is NULL and a WARNING in the log, never a failed block
CREATE OR REPLACE FUNCTION domain.to_bigint(v bytea) RETURNS bigint LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE n numeric := domain.to_uint(v);
BEGIN
  IF n > 9223372036854775807 THEN
    RAISE WARNING 'domain.to_bigint: value out of range: 0x%', encode(v, 'hex');
    RETURN NULL;
  END IF;
  RETURN n::bigint;
END $$;
-- +goose StatementEnd
-- +goose StatementBegin
-- a uint256 (or uint32) that is bounded by design; same guard as to_bigint
CREATE OR REPLACE FUNCTION domain.to_integer(v bytea) RETURNS integer LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE n numeric := domain.to_uint(v);
BEGIN
  IF n > 2147483647 THEN
    RAISE WARNING 'domain.to_integer: value out of range: 0x%', encode(v, 'hex');
    RETURN NULL;
  END IF;
  RETURN n::integer;
END $$;
-- +goose StatementEnd
-- +goose StatementBegin
-- a uint256 holding block.timestamp (seconds); guarded well past any real date
CREATE OR REPLACE FUNCTION domain.to_timestamp(v bytea) RETURNS timestamptz LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE n numeric := domain.to_uint(v);
BEGIN
  IF n > 9223372036854 THEN
    RAISE WARNING 'domain.to_timestamp: value out of range: 0x%', encode(v, 'hex');
    RETURN NULL;
  END IF;
  RETURN to_timestamp(n::double precision);
END $$;
-- +goose StatementEnd
-- +goose StatementBegin
-- an int256 stored through a uint256 component (two's complement); same range guard
CREATE OR REPLACE FUNCTION domain.to_int_bigint(v bytea) RETURNS bigint LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE n numeric := domain.to_uint(v);
BEGIN
  IF n >= 57896044618658097711785492504343953926634992332820282019728792003956564819968 THEN   -- 2^255: negative
    n := n - 115792089237316195423570985008687907853269984665640564039457584007913129639936;    -- 2^256
  END IF;
  IF n > 9223372036854775807 OR n < -9223372036854775808 THEN
    RAISE WARNING 'domain.to_int_bigint: value out of range: 0x%', encode(v, 'hex');
    RETURN NULL;
  END IF;
  RETURN n::bigint;
END $$;
-- +goose StatementEnd
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION domain.to_int32(v bytea) RETURNS integer LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT (('x' || encode(substring(domain.id32(v) from 29 for 4), 'hex'))::bit(32))::integer $$;
-- +goose StatementEnd
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION domain.to_bool(v bytea) RETURNS boolean LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT domain.id32(v) <> '\x0000000000000000000000000000000000000000000000000000000000000000'::bytea $$;
-- +goose StatementEnd
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION domain.to_address(v bytea) RETURNS bytea LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT substring(domain.id32(v) from 13 for 20) $$;
-- +goose StatementEnd
-- +goose StatementBegin
-- abi.encode(string): offset word, length word, then the bytes; a bare short value is taken as raw text
CREATE OR REPLACE FUNCTION domain.to_string(v bytea) RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE n int;
BEGIN
  IF length(v) >= 64 AND substring(v from 1 for 32) = '\x0000000000000000000000000000000000000000000000000000000000000020'::bytea THEN
    n := domain.to_integer(substring(v from 33 for 32));
    RETURN convert_from(substring(v from 65 for n), 'UTF8');
  END IF;
  RETURN convert_from(v, 'UTF8');
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END $$;
-- +goose StatementEnd
-- +goose StatementBegin
-- a Stat packs four int32 values into 64-bit slots: base << 192, shift << 128, boost << 64, sync
CREATE OR REPLACE FUNCTION domain.stat_part(v bytea, part int) RETURNS integer LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT (('x' || encode(substring(domain.id32(v) from 5 + part * 8 for 4), 'hex'))::bit(32))::integer $$;
-- +goose StatementEnd
-- +goose StatementBegin
-- a Coord packs x, y, z the same way, in the three low slots
CREATE OR REPLACE FUNCTION domain.coord_part(v bytea, part int) RETURNS integer LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT (('x' || encode(substring(domain.id32(v) from 13 + part * 8 for 4), 'hex'))::bit(32))::integer $$;
-- +goose StatementEnd
-- +goose StatementBegin
-- abi.encode(uintN[]): offset word, length word, one word per item
CREATE OR REPLACE FUNCTION domain.to_uint_array(v bytea) RETURNS numeric[] LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE n int; i int; out numeric[] := '{}';
BEGIN
  IF length(v) < 64 THEN RETURN out; END IF;
  n := domain.to_integer(substring(v from 33 for 32));
  FOR i IN 0..n - 1 LOOP out := out || domain.to_uint(substring(v from 65 + i * 32 for 32)); END LOOP;
  RETURN out;
END $$;
-- +goose StatementEnd
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION domain.to_string_array(v bytea) RETURNS text[] LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE n int; i int; off int; len int; out text[] := '{}';
BEGIN
  IF length(v) < 64 THEN RETURN out; END IF;
  n := domain.to_integer(substring(v from 33 for 32));
  FOR i IN 0..n - 1 LOOP
    off := domain.to_integer(substring(v from 65 + i * 32 for 32));   -- relative to the array's data start (byte 33)
    len := domain.to_integer(substring(v from 33 + off + 1 for 32));
    out := out || convert_from(substring(v from 33 + off + 33 for len), 'UTF8');
  END LOOP;
  RETURN out;
END $$;
-- +goose StatementEnd
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument('--spec', default=os.path.join(here, 'generated', 'recognition.json'))
    ap.add_argument('--out', default=os.path.join(here, 'generated', 'domain_schema.sql'))
    a = ap.parse_args()
    print(generate(a.spec, a.out))


def trimmed_hex(h):
    """Kamigaze stores ids as the minimal big-endian bytes (no leading zero bytes); match that for literals."""
    return bytes.fromhex(h).lstrip(b'\0').hex()


def sql_text_array(items):
    return "ARRAY[%s]::text[]" % ', '.join("'%s'" % i.replace("'", "''") for i in items) if items else "'{}'::text[]"


def sql_json(obj):
    return "'%s'::jsonb" % json.dumps(obj, sort_keys=True).replace("'", "''")


def generate(spec_path, out_path):
    """Write the migration for the spec at spec_path to out_path; returns a one-line summary."""
    spec = json.load(open(spec_path))
    comps = spec['components']; ents = spec['entities']
    records = {}
    for t, e in ents.items():
        for ns, r in e.get('records', {}).items(): records[ns] = dict(r, parent_type=t)
    for ns, r in spec['shared_records'].items(): records[ns] = dict(r, parent_type=None)
    comp_id = lambda name: trimmed_hex(keccak(comps[name]['id'].encode()).hex())
    out = []
    w = out.append
    w('-- +goose Up')
    w('-- GENERATED by kami/kami-atlas/schema.py from the recognition spec of %s at %s. Do not edit.' % (spec['source'].get('contracts'), spec['source'].get('commit')))
    w('-- One table per entity type and per record kind, typed columns per component, a classification table, the spec')
    w('-- tables the populator reads (records, shapes, singletons, systems), one refresh function per table, and an')
    w('-- outbox of changes for subscriptions. See kami-atlas README: Domain schema.')
    w('CREATE SCHEMA IF NOT EXISTS domain;')
    w(HELPERS.strip('\n'))
    # components by id, with the columns each one lands in
    table_cols = {}
    comp_columns = {}
    for n, c in comps.items():
        comp_columns[n] = [col for col, _, _ in columns_for(n, c.get('type'))] if n != 'EntityType' else []
    w("CREATE TABLE IF NOT EXISTS domain.component (id bytea PRIMARY KEY, name text NOT NULL UNIQUE, value_type text NOT NULL, indexed boolean NOT NULL DEFAULT false, columns text[] NOT NULL DEFAULT '{}');")
    rows = ["('\\x%s', '%s', '%s', %s, %s)" % (comp_id(n), n, c.get('type') or 'bytes', 'true' if c.get('indexed') else 'false', sql_text_array(comp_columns[n])) for n, c in sorted(comps.items()) if c.get('id')]
    w('INSERT INTO domain.component (id, name, value_type, indexed, columns) VALUES\n  %s\nON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, value_type = EXCLUDED.value_type, indexed = EXCLUDED.indexed, columns = EXCLUDED.columns;' % ',\n  '.join(rows))
    for n, c in spec.get('framework_components', {}).items():
        w("INSERT INTO domain.component (id, name, value_type) VALUES ('\\x%s', '%s', 'framework') ON CONFLICT (id) DO NOTHING;" % (trimmed_hex(keccak(c['id'].encode()).hex()), 'framework.' + n.lower()))
    # systems by id (ecs.systems carries only the id and the address; the name lets the populator see who wrote a transaction)
    w('CREATE TABLE IF NOT EXISTS domain.system (id bytea PRIMARY KEY, name text NOT NULL, deployed boolean NOT NULL DEFAULT true);')
    srows = ["('\\x%s', '%s', %s)" % (trimmed_hex(keccak(s['id'].encode()).hex()), n, 'true' if s.get('deployed') else 'false') for n, s in sorted(spec.get('systems', {}).items()) if s.get('id')]
    if srows:
        w('INSERT INTO domain.system (id, name, deployed) VALUES\n  %s\nON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, deployed = EXCLUDED.deployed;' % ',\n  '.join(srows))
    # kinds
    w('CREATE TABLE IF NOT EXISTS domain.kind (name text PRIMARY KEY, kind text NOT NULL, table_name text NOT NULL, entity_type text, layer text, parent_types text[], id_namespace text);')
    krows = []
    for t, e in ents.items():
        ns = next(iter(e.get('id', {}).values()), None)
        krows.append("('%s', '%s', '%s', %s, '%s', NULL, %s)" % (t, 'entity' if e.get('entity_type') else 'shape', table_name(t, False), ("'%s'" % t) if e.get('entity_type') else 'NULL', e['layer'], ("'%s'" % ns.split('(')[0]) if ns and '(' in ns else 'NULL'))
    for ns, r in records.items():
        parents = sorted({p for v in r['keys'].values() for p in (v if isinstance(v, list) else [v] if v else []) if p in ents} | ({r['parent_type']} if r['parent_type'] else set()) | set(r.get('held_by') or []))
        krows.append("('%s', 'record', '%s', NULL, NULL, %s, '%s')" % (ns, table_name(ns, True), ("ARRAY[%s]::text[]" % ', '.join("'%s'" % p for p in parents)) if parents else 'NULL', ns))
    w('INSERT INTO domain.kind (name, kind, table_name, entity_type, layer, parent_types, id_namespace) VALUES\n  %s\nON CONFLICT (name) DO UPDATE SET kind = EXCLUDED.kind, table_name = EXCLUDED.table_name, entity_type = EXCLUDED.entity_type, layer = EXCLUDED.layer, parent_types = EXCLUDED.parent_types, id_namespace = EXCLUDED.id_namespace;' % ',\n  '.join(krows))
    # record specs: everything the populator needs to recompute a record id from a transaction's context
    w('''CREATE TABLE IF NOT EXISTS domain.record_spec (
  ns text PRIMARY KEY REFERENCES domain.kind(name),
  table_name text NOT NULL,
  parent_type text,
  encoding text NOT NULL,               -- packed | abi | address
  key_order text[] NOT NULL,
  key_types text[] NOT NULL,
  key_columns text[] NOT NULL,
  key_parents jsonb NOT NULL,           -- key -> entity types whose ids it takes ([] when any id or a plain number)
  key_values jsonb NOT NULL,            -- key -> string literals the code uses
  key_patterns jsonb NOT NULL,          -- key -> concatenation patterns, * for a filled part
  fields text[] NOT NULL                -- component names a record of this kind carries
);''')
    rrows = []
    for ns, r in records.items():
        keys = r.get('key_order') or list(r['keys'])
        ktypes = r.get('key_types') or {}
        kcols = [key_column(k, ktypes.get(k), r['keys'].get(k))[0] for k in keys]
        seen = set(); kcols = [kc for kc in kcols if not (kc in seen or seen.add(kc))]
        if str(r.get('id', '')).startswith('address('): enc = 'address'
        elif 'Packed' in r.get('encoding', 'Packed'): enc = 'packed'
        else: enc = 'abi'
        parents = {k: ([p for p in (v if isinstance(v, list) else [v]) if p in ents] if v else []) for k, v in r['keys'].items()}
        rrows.append("('%s', '%s', %s, '%s', %s, %s, %s, %s, %s, %s, %s)" % (
            ns, table_name(ns, True), ("'%s'" % r['parent_type']) if r['parent_type'] else 'NULL', enc,
            sql_text_array(keys), sql_text_array([ktypes.get(k) or '' for k in keys]), sql_text_array(kcols),
            sql_json(parents), sql_json(r.get('key_values') or {}), sql_json(r.get('key_patterns') or {}), sql_text_array(r['fields'])))
    w('INSERT INTO domain.record_spec (ns, table_name, parent_type, encoding, key_order, key_types, key_columns, key_parents, key_values, key_patterns, fields) VALUES\n  %s\nON CONFLICT (ns) DO UPDATE SET table_name = EXCLUDED.table_name, parent_type = EXCLUDED.parent_type, encoding = EXCLUDED.encoding, key_order = EXCLUDED.key_order, key_types = EXCLUDED.key_types, key_columns = EXCLUDED.key_columns, key_parents = EXCLUDED.key_parents, key_values = EXCLUDED.key_values, key_patterns = EXCLUDED.key_patterns, fields = EXCLUDED.fields;' % ',\n  '.join(rrows))
    # untyped shapes: recognised by the components written together (and the creating system when it is known)
    w('CREATE TABLE IF NOT EXISTS domain.shape_spec (name text PRIMARY KEY REFERENCES domain.kind(name), signature text[] NOT NULL, created_by text[] NOT NULL);')
    shrows = ["('%s', %s, %s)" % (t, sql_text_array(e['recognize']['signature']), sql_text_array(e['recognize'].get('created_by') or [])) for t, e in ents.items() if e.get('recognize')]
    if shrows:
        w('INSERT INTO domain.shape_spec (name, signature, created_by) VALUES\n  %s\nON CONFLICT (name) DO UPDATE SET signature = EXCLUDED.signature, created_by = EXCLUDED.created_by;' % ',\n  '.join(shrows))
    # singletons: fixed ids that are keys of records
    w('CREATE TABLE IF NOT EXISTS domain.singleton (name text PRIMARY KEY, id bytea NOT NULL);')
    sgrows = []
    for name, g in spec.get('singletons', {}).items():
        m = re.fullmatch(r'keccak256\("([^"]+)"\)', g.get('id', ''))
        if m: sgrows.append("('%s', '\\x%s')" % (name, keccak(m.group(1).encode()).hex()))
    if sgrows:
        w('INSERT INTO domain.singleton (name, id) VALUES\n  %s\nON CONFLICT (name) DO UPDATE SET id = EXCLUDED.id;' % ',\n  '.join(sgrows))
    # classification
    w('''CREATE TABLE IF NOT EXISTS domain.entity (
  id bytea PRIMARY KEY,
  kind text NOT NULL REFERENCES domain.kind(name),
  parent bytea,
  recognised_by text NOT NULL,          -- entity_type | record_id | address | signature | calldata | bootstrap
  created_block integer,
  created_tx integer,
  created_by_system text,
  updated_block integer
);
CREATE INDEX IF NOT EXISTS idx_domain_entity_kind ON domain.entity(kind);
CREATE INDEX IF NOT EXISTS idx_domain_entity_parent ON domain.entity(parent);''')
    # outbox
    w('''CREATE TABLE IF NOT EXISTS domain.outbox (
  seq bigserial PRIMARY KEY,
  block_number integer NOT NULL,
  transaction_idx integer,
  kind text NOT NULL,
  id bytea NOT NULL,
  op text NOT NULL,                     -- insert | update | delete
  changed text[]                        -- columns that changed, NULL on insert and delete
);
CREATE INDEX IF NOT EXISTS idx_domain_outbox_block ON domain.outbox(block_number);
CREATE INDEX IF NOT EXISTS idx_domain_outbox_kind_id ON domain.outbox(kind, id);''')
    # entity-type tables
    for t, e in ents.items():
        cols = []
        for comp in e['components'] + e['components_later']:
            if comp == 'EntityType' or comp not in comps: continue
            cols += [(col, typ, expr, comp) for col, typ, expr in columns_for(comp, comps[comp].get('type'), t)]
        table_cols[t] = cols
        w('CREATE TABLE IF NOT EXISTS domain.%s (\n  id bytea PRIMARY KEY,\n  block_number integer NOT NULL,\n  %s\n);' % (
            table_name(t, False), ',\n  '.join('%s %s' % (col, typ) for col, typ, _, _ in cols)))
        for col, typ, _, comp in cols:
            if typ == 'bytea' and POINTER.match(comp): w('CREATE INDEX IF NOT EXISTS idx_domain_%s_%s ON domain.%s(%s);' % (table_name(t, False), col, table_name(t, False), col))
    # record tables
    rec_cols = {}
    for ns, r in records.items():
        tn = table_name(ns, True)
        keys = r.get('key_order') or list(r['keys'])
        kcols = [key_column(k, (r.get('key_types') or {}).get(k), r['keys'].get(k)) for k in keys]
        fcols = []
        for f in r['fields']:
            if f in comps: fcols += [(col, typ, expr, f) for col, typ, expr in columns_for(f, comps[f].get('type'), ns)]
        seen = set(); kcols = [kc for kc in kcols if not (kc[0] in seen or seen.add(kc[0]))]
        rec_cols[ns] = fcols
        w('CREATE TABLE IF NOT EXISTS domain.%s (\n  id bytea PRIMARY KEY,\n  block_number integer NOT NULL,\n  parent bytea,\n  %s%s\n);' % (
            tn, ',\n  '.join('%s %s' % kc for kc in kcols), (',\n  ' + ',\n  '.join('%s %s' % (col, typ) for col, typ, _, _ in fcols)) if fcols else ''))
        w('CREATE INDEX IF NOT EXISTS idx_domain_%s_parent ON domain.%s(parent);' % (tn, tn))

    def picks(cols):
        """one select expression per column: the raw value of its component picked out of the entity's state rows, decoded"""
        selects = []
        for col, typ, expr, comp in cols:
            picked = "(array_agg(s.value) FILTER (WHERE c.id = '\\x%s'))[1]" % comp_id(comp)   # the raw value, then decoded (an array column can't be picked after decoding)
            selects.append("%s AS %s" % (re.sub(r'\bv\b', lambda m: picked, expr), col))
        return selects

    # refresh functions: recompute the rows of the given entities from current state (the populator calls them for what a
    # block range touched; bootstrap calls them for everything of a kind)
    from_state = "FROM ecs.state s JOIN ecs.entities e ON e.idx = s.entity_idx JOIN ecs.components c ON c.idx = s.component_idx\n  WHERE NOT s.removed AND e.idx = ANY(SELECT idx FROM ecs.entities WHERE id = ANY(SELECT ltrim(x, '\\x00'::bytea) FROM unnest(ids) x))\n  GROUP BY e.id"
    for t, e in ents.items():
        cols = table_cols[t]
        tn = table_name(t, False)
        w('-- +goose StatementBegin')
        if cols:
            w('''CREATE OR REPLACE FUNCTION domain.refresh_%s(ids bytea[]) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO domain.%s (id, block_number, %s)
  SELECT domain.id32(e.id), max(s.block_number), %s
  %s
  ON CONFLICT (id) DO UPDATE SET block_number = EXCLUDED.block_number, %s;
END $$;''' % (
                tn, tn, ', '.join(col for col, _, _, _ in cols), ',\n    '.join(picks(cols)), from_state,
                ', '.join('%s = EXCLUDED.%s' % (col, col) for col, _, _, _ in cols)))
        else:
            w('''CREATE OR REPLACE FUNCTION domain.refresh_%s(ids bytea[]) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO domain.%s (id, block_number)
  SELECT domain.id32(e.id), max(s.block_number)
  %s
  ON CONFLICT (id) DO UPDATE SET block_number = EXCLUDED.block_number;
END $$;''' % (tn, tn, from_state))
        w('-- +goose StatementEnd')
    for ns, r in records.items():
        tn = table_name(ns, True); fcols = rec_cols[ns]
        w('-- +goose StatementBegin')
        # a record row is created by the populator with its keys; refresh only brings its fields up to date
        w('''CREATE OR REPLACE FUNCTION domain.refresh_%s(ids bytea[]) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  UPDATE domain.%s r SET block_number = x.block_number%s
  FROM (SELECT domain.id32(e.id) AS id, max(s.block_number) AS block_number%s
  %s) x
  WHERE r.id = x.id;
END $$;''' % (
            tn, tn, ''.join(', %s = x.%s' % (col, col) for col, _, _, _ in fcols), (', ' + ', '.join(picks(fcols))) if fcols else '', from_state))
        w('-- +goose StatementEnd')
    # bootstrap: entity-type tables from current state
    et_id = comp_id('EntityType')
    w('-- +goose StatementBegin')
    w('CREATE OR REPLACE PROCEDURE domain.bootstrap() LANGUAGE plpgsql AS $$')
    w('BEGIN')
    w('  -- typed entities: EntityType names the kind; every column decoded from the current value of its component')
    w('''  INSERT INTO domain.entity (id, kind, recognised_by, created_block, updated_block)
  SELECT domain.id32(e.id), domain.to_string(s.value), 'bootstrap', s.block_number, s.block_number
  FROM ecs.state s JOIN ecs.entities e ON e.idx = s.entity_idx JOIN ecs.components c ON c.idx = s.component_idx
  WHERE c.id = '\\x%s' AND NOT s.removed AND domain.to_string(s.value) IN (SELECT name FROM domain.kind WHERE kind = 'entity')
  ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, updated_block = GREATEST(domain.entity.updated_block, EXCLUDED.updated_block);''' % et_id)
    for t, e in ents.items():
        if not e.get('entity_type'): continue
        w("  PERFORM domain.refresh_%s(ARRAY(SELECT id FROM domain.entity WHERE kind = '%s'));" % (table_name(t, False), t))
    w('END $$;')
    w('-- +goose StatementEnd')
    w('')
    w('-- +goose Down')
    w('DROP SCHEMA IF EXISTS domain CASCADE;')
    with open(out_path, 'w') as f: f.write('\n'.join(out) + '\n')
    return 'wrote %s: %d entity tables, %d record tables, %d components, %d systems' % (out_path, len(table_cols), len(records), len(rows), len(srows))


if __name__ == '__main__':
    main()
