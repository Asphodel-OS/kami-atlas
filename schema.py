#!/usr/bin/env python3
"""Generate the domain schema for Kamigaze from the recognition spec, as one goose migration.

    python3 schema.py [--spec generated/recognition.json] [--out <migration.sql>]

What it makes, all under a `domain` schema in PostgreSQL:
  domain.component          the components by on-chain id, with name and value type (so nothing depends on ecs.components.key)
  domain.kind               every entity type, record kind and untyped shape the spec knows, with its table name
  domain.entity             what each entity is: kind, parent, how it was recognised, first and last block
  domain.<type>             one table per entity type: id, block_number, one typed column per component
                            (creation columns and the ones written later), Stat as four columns, Coord as three
  domain.rec_<namespace>    one table per record kind: id, parent, the keys, the fields
  domain.outbox             what changed, per block and transaction: kind, id, columns; the feed for subscriptions
  domain.bootstrap()        fills the entity-type tables from ecs.state (current values); records are filled by the
                            per-transaction populator, which has the context their keys need
Decoding helpers (domain.*) are self-contained plpgsql: uint, int32, bool, string, address, Stat, Coord, uint arrays.
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


def columns_for(comp, ctype):
    """(column name, sql type, decoding expression over value v) list for one component."""
    c = snake(comp)
    if ctype == 'Stat':
        return [(c + '_' + part, 'integer', 'domain.stat_part(v, %d)' % i) for i, part in enumerate(('base', 'shift', 'boost', 'sync'))]
    if ctype == 'Coord':
        return [(c + '_' + part, 'integer', 'domain.coord_part(v, %d)' % i) for i, part in enumerate(('x', 'y', 'z'))]
    if ctype == 'uint256':
        if POINTER.match(comp): return [(c, 'bytea', 'domain.id32(v)')]   # an entity id: keep the 32 bytes for joins
        return [(c, 'numeric', 'domain.to_uint(v)')]
    if ctype in ('uint32', 'uint8', 'uint16', 'uint64'): return [(c, 'bigint', 'domain.to_uint(v)::bigint')]
    if ctype in ('int32', 'int8', 'int16', 'int64'): return [(c, 'integer' if ctype != 'int64' else 'bigint', 'domain.to_int32(v)' if ctype == 'int32' else 'domain.to_uint(v)::bigint')]
    if ctype == 'bool': return [(c, 'boolean', 'domain.to_bool(v)')]
    if ctype == 'string': return [(c, 'text', 'domain.to_string(v)')]
    if ctype == 'address': return [(c, 'bytea', 'domain.to_address(v)')]
    if ctype in ('uint32[]', 'uint256[]', 'int32[]'): return [(c, 'numeric[]', 'domain.to_uint_array(v)')]
    if ctype == 'string[]': return [(c, 'text[]', 'domain.to_string_array(v)')]
    return [(c, 'bytea', 'v')]   # unknown: keep the raw bytes


def key_column(key, ktype, parents):
    k = 'key_' + snake(key).rstrip('_')   # has.flag(id, flagType): the key "id" would collide with the row id
    if ktype == 'uint256': return (k, 'bytea' if parents or key.lower().endswith('id') else 'numeric')
    if ktype and ktype.startswith('uint'): return (k, 'bigint')
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
CREATE OR REPLACE FUNCTION domain.to_int32(v bytea) RETURNS integer LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT (('x' || encode(substring(domain.id32(v) from 29 for 4), 'hex'))::bit(32))::integer $$;
-- +goose StatementEnd
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION domain.to_bool(v bytea) RETURNS boolean LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT domain.to_uint(v) <> 0 $$;
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
  IF length(v) >= 64 AND domain.to_uint(substring(v from 1 for 32)) = 32 THEN
    n := domain.to_uint(substring(v from 33 for 32))::int;
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
  n := domain.to_uint(substring(v from 33 for 32))::int;
  FOR i IN 0..n - 1 LOOP out := out || domain.to_uint(substring(v from 65 + i * 32 for 32)); END LOOP;
  RETURN out;
END $$;
-- +goose StatementEnd
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION domain.to_string_array(v bytea) RETURNS text[] LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE n int; i int; off int; len int; out text[] := '{}';
BEGIN
  IF length(v) < 64 THEN RETURN out; END IF;
  n := domain.to_uint(substring(v from 33 for 32))::int;
  FOR i IN 0..n - 1 LOOP
    off := domain.to_uint(substring(v from 65 + i * 32 for 32))::int;   -- relative to the array's data start (byte 33)
    len := domain.to_uint(substring(v from 33 + off + 1 for 32))::int;
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


def generate(spec_path, out_path):
    """Write the migration for the spec at spec_path to out_path; returns a one-line summary."""
    spec = json.load(open(spec_path))
    comps = spec['components']; ents = spec['entities']
    records = {}
    for t, e in ents.items():
        for ns, r in e.get('records', {}).items(): records[ns] = dict(r, parent_type=t)
    for ns, r in spec['shared_records'].items(): records[ns] = dict(r, parent_type=None)
    out = []
    w = out.append
    w('-- +goose Up')
    w('-- GENERATED by kami/kami-atlas/schema.py from the recognition spec of %s at %s. Do not edit.' % (spec['source'].get('contracts'), spec['source'].get('commit')))
    w('-- One table per entity type and per record kind, typed columns per component, a classification table, and an')
    w('-- outbox of changes for subscriptions. See kami-atlas README: Domain schema.')
    w('CREATE SCHEMA IF NOT EXISTS domain;')
    w(HELPERS.strip('\n'))
    # components by id
    w('CREATE TABLE IF NOT EXISTS domain.component (id bytea PRIMARY KEY, name text NOT NULL UNIQUE, value_type text NOT NULL, indexed boolean NOT NULL DEFAULT false);')
    rows = ["('\\x%s', '%s', '%s', %s)" % (keccak(c['id'].encode()).hex(), n, c.get('type') or 'bytes', 'true' if c.get('indexed') else 'false') for n, c in sorted(comps.items()) if c.get('id')]
    w('INSERT INTO domain.component (id, name, value_type, indexed) VALUES\n  %s\nON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, value_type = EXCLUDED.value_type, indexed = EXCLUDED.indexed;' % ',\n  '.join(rows))
    for n, c in spec.get('framework_components', {}).items():
        w("INSERT INTO domain.component (id, name, value_type) VALUES ('\\x%s', '%s', 'framework') ON CONFLICT (id) DO NOTHING;" % (keccak(c['id'].encode()).hex(), 'framework.' + n.lower()))
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
    # classification
    w('''CREATE TABLE IF NOT EXISTS domain.entity (
  id bytea PRIMARY KEY,
  kind text NOT NULL REFERENCES domain.kind(name),
  parent bytea,
  recognised_by text NOT NULL,          -- entity_type | record_id | signature | calldata | bootstrap
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
    table_cols = {}
    for t, e in ents.items():
        cols = []
        for comp in e['components'] + e['components_later']:
            if comp == 'EntityType' or comp not in comps: continue
            cols += [(col, typ, expr, comp) for col, typ, expr in columns_for(comp, comps[comp].get('type'))]
        table_cols[t] = cols
        w('CREATE TABLE IF NOT EXISTS domain.%s (\n  id bytea PRIMARY KEY,\n  block_number integer NOT NULL,\n  %s\n);' % (
            table_name(t, False), ',\n  '.join('%s %s' % (col, typ) for col, typ, _, _ in cols)))
        for col, typ, _, comp in cols:
            if typ == 'bytea' and POINTER.match(comp): w('CREATE INDEX IF NOT EXISTS idx_domain_%s_%s ON domain.%s(%s);' % (table_name(t, False), col, table_name(t, False), col))
    # record tables
    for ns, r in records.items():
        tn = table_name(ns, True)
        keys = r.get('key_order') or list(r['keys'])
        kcols = [key_column(k, (r.get('key_types') or {}).get(k), r['keys'].get(k)) for k in keys]
        fcols = []
        for f in r['fields']:
            if f in comps: fcols += [(col, typ, expr, f) for col, typ, expr in columns_for(f, comps[f].get('type'))]
        seen = set(); kcols = [kc for kc in kcols if not (kc[0] in seen or seen.add(kc[0]))]
        w('CREATE TABLE IF NOT EXISTS domain.%s (\n  id bytea PRIMARY KEY,\n  block_number integer NOT NULL,\n  parent bytea,\n  %s%s\n);' % (
            tn, ',\n  '.join('%s %s' % kc for kc in kcols), (',\n  ' + ',\n  '.join('%s %s' % (col, typ) for col, typ, _, _ in fcols)) if fcols else ''))
        w('CREATE INDEX IF NOT EXISTS idx_domain_%s_parent ON domain.%s(parent);' % (tn, tn))
    # bootstrap: entity-type tables from current state
    et_id = keccak(comps['EntityType']['id'].encode()).hex()
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
        cols = table_cols[t]
        if not cols: continue
        selects = []
        for col, typ, expr, comp in cols:
            cid = keccak(comps[comp]['id'].encode()).hex()
            # one row per component value: pick it out of the group (works for every type, unlike max)
            picked = "(array_agg(s.value) FILTER (WHERE c.id = '\\x%s'))[1]" % cid   # the raw value, then decoded (an array column can't be picked after decoding)
            selects.append("%s AS %s" % (re.sub(r'\bv\b', lambda m: picked, expr), col))
        w('''  INSERT INTO domain.%s (id, block_number, %s)
  SELECT domain.id32(e.id), max(s.block_number), %s
  FROM ecs.state s JOIN ecs.entities e ON e.idx = s.entity_idx JOIN ecs.components c ON c.idx = s.component_idx
  WHERE NOT s.removed AND domain.id32(e.id) IN (SELECT id FROM domain.entity WHERE kind = '%s')
  GROUP BY e.id
  ON CONFLICT (id) DO UPDATE SET block_number = EXCLUDED.block_number, %s;''' % (
            table_name(t, False), ', '.join(col for col, _, _, _ in cols), ',\n    '.join(selects), t,
            ', '.join('%s = EXCLUDED.%s' % (col, col) for col, _, _, _ in cols)))
    w('END $$;')
    w('-- +goose StatementEnd')
    w('')
    w('-- +goose Down')
    w('DROP SCHEMA IF EXISTS domain CASCADE;')
    with open(out_path, 'w') as f: f.write('\n'.join(out) + '\n')
    return 'wrote %s: %d entity tables, %d record tables, %d components' % (out_path, len(table_cols), len(records), len(rows))


if __name__ == '__main__':
    main()
