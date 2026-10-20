// Loads viz/data/*.json (written by generate.py) and the notes, then derives the facts every view reads:
// relationships with notes applied, what passes through what, sub-entities, who creates and changes each entity.
import { arr } from './util.js';

export async function loadData() {
  const [model, systems, comps] = await Promise.all(['domain-model.json', 'systems.json', 'components.json']
    .map(f => fetch('data/' + f).then(r => { if (!r.ok) throw new Error(f + ' ' + r.status); return r.json(); })));
  let datalogs = { logs: {}, families: {}, notes: {} };
  try { const r = await fetch('data/datalogs.json'); if (r.ok) datalogs = await r.json(); } catch (e) {}   // the counters LibData keeps (generated/data-logs.yaml)
  let notes = {}, layout = {};
  try { notes = jsyaml.load(await (await fetch('data/notes.yaml')).text()) || {}; } catch (e) { notes = {}; }
  try { const r = await fetch('data/layout.json'); if (r.ok) layout = await r.json(); } catch (e) { layout = {}; }   // notes/layout.json: hand-tuned overview positions
  return { model, systems, comps, notes, layout, datalogs };
}

export function install(A) {
  const { model, systems, notes } = A;
  const E = model.entities, NE = notes.entities || {}, NL = notes.links || {}, NS = notes.sub_entities || {};
  const RT = model.relationship_types || {};
  const SG = model.singletons || {};

  // ---------------- facts ----------------
  const compUse = {};
  for (const [n, e] of Object.entries(E)) for (const c of e.components || []) (compUse[c] = compUse[c] || []).push(n);
  const uniqueFields = n => (E[n].components || []).filter(c => compUse[c].length === 1);
  const changedBy = n => { const own = uniqueFields(n); return Object.keys(systems).filter(s => !s.startsWith('__') && (systems[s].writes || []).some(c => own.includes(c))).sort(); };

  // every relationship, with notes applied
  const rels = [];
  for (const [n, e] of Object.entries(E)) for (const [comp, l] of Object.entries(e.links || {})) {
    const o = NL[n + '.' + comp];
    if (o && o.ignore) continue;
    const to = (o && o.to ? arr(o.to).flatMap(t => String(t).split(/\s*\|\s*/)) : arr(l.to)).filter(Boolean);
    const base = { from: n, comp, l, note: (o && o.note) || l.note, fromNotes: !!(o && o.to) || !!l.settled_in_notes, unresolved: !to.length };
    if (!to.length) rels.push({ ...base, to: null });
    for (const t of to) rels.push({ ...base, to: t });
  }
  // what an edge passes through: a record stored under the holder, or anchor keys the target groups things under
  const viaOf = r => r.l.via && r.l.via.record ? { kind: 'record', ns: [r.l.via.record] } : r.l.via && arr(r.l.via.anchor_keys).length ? { kind: 'anchor', ns: arr(r.l.via.anchor_keys) } : null;
  for (const r of rels) { const v = viaOf(r); r.via = v; r.label = v && v.kind === 'record' ? v.ns[0] : r.comp.startsWith('key:') ? 'key: ' + r.comp.slice(4).split(',').join(', ') : r.comp; r.field = v && v.kind === 'record' ? r.comp.slice(v.ns[0].length + 1) : r.comp; }
  // holder-end multiplicity, from the cardinality sentence: many / one / one per key
  const cardOf = l => /^one .+ per .+ per |^one .+ per (pair|set of \d+) of /.test(l.cardinality || '') ? 'key' : /^one /.test(l.cardinality || '') ? 'one' : /^many /.test(l.cardinality || '') ? 'many' : null;
  const SUB_BY_NS = {};  // namespace -> {ns, s, shared} for tooltips on passthrough glyphs
  for (const [n, e] of Object.entries(E)) for (const [ns, s] of Object.entries(e.sub_entities || {})) SUB_BY_NS[ns] = { ns, s, shared: false, parent: n };
  for (const [ns, s] of Object.entries(model.shared_sub_entities || {})) SUB_BY_NS[ns] = { ns, s, shared: true, parent: arr(s.held_by).join('/') || 'any holder' };
  const anchorUsers = {};  // anchor key -> [{from, comp}] of records that point at it
  for (const r of rels) if (r.via && r.via.kind === 'anchor') for (const ns of r.via.ns) {
    anchorUsers[ns] = anchorUsers[ns] || [];
    if (!anchorUsers[ns].some(u => u.from === r.from && u.comp === r.comp)) anchorUsers[ns].push({ from: r.from, comp: r.comp });
  }
  const subKindLine = x => x.s.kind === 'anchor key'
    ? 'anchor key' + (anchorUsers[x.ns] ? ' ← ' + anchorUsers[x.ns].map(u => u.from + '.' + u.comp).join(', ') : arr(x.s.pointed_at_by).length ? ' ← ' + arr(x.s.pointed_at_by).join(', ') : '')
    : 'record' + (x.s.shape ? ' (a ' + x.s.shape + ')' : '') + (arr(x.s.fields).length ? ': ' + arr(x.s.fields).join(', ') : '');
  const relsOf = n => ({ out: rels.filter(r => r.from === n), in: rels.filter(r => r.to === n && r.from !== n) });
  const subsOf = n => {
    const own = Object.entries(E[n].sub_entities || {}).map(([ns, s]) => ({ ns, s, shared: false }));
    const shared = Object.entries(model.shared_sub_entities || {}).filter(([, s]) => arr(s.held_by).includes(n)).map(([ns, s]) => ({ ns, s, shared: true }));
    return own.concat(shared);
  };

  Object.assign(A, { E, NE, NL, NS, RT, SG, compUse, uniqueFields, changedBy, rels, viaOf, cardOf, SUB_BY_NS, anchorUsers, subKindLine, relsOf, subsOf });
}
