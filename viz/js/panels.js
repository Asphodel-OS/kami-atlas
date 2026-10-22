// The Details panel: the overview text, one system, one entity.
import { esc, arr, LAYERS, LAYER_NAME, REL_NAME } from './util.js';

export function install(A) {
  const { E, NE, NS, RT, SG, model, systems, comps, notes, panel, rels, compUse, changedBy, relsOf, subsOf, subKindLine } = A;
  const entBtn = n => E[n] ? `<button class="ent" data-go="${esc(n)}">${esc(n)}</button>` : `<code>${esc(n)}</code>`;
  function overviewPanel() {
    const counts = LAYERS.map(l => [l, Object.values(E).filter(e => e.layer === l).length]);
    const nSub = Object.values(E).reduce((a, e) => a + Object.keys(e.sub_entities || {}).length, 0) + Object.keys(model.shared_sub_entities || {}).length;
    panel.innerHTML = `<h2>Kamigotchi’s entities</h2>
      <p class="what">Every entity the contracts create, generated from the compiler’s view of the code, with descriptions and corrections from the team’s notes. Hover an entity for who creates and changes it; click it for its fields, sub-entities and relationships. Hover an edge to see how the relationship was derived. Search a system to see everything it touches.</p>
      <section class="block"><h3>In this model</h3><div class="kv">
        ${counts.map(([l, c]) => `<span class="k">${esc(LAYER_NAME[l])}</span><span>${c} entity types</span>`).join('')}
        <span class="k">Untyped shapes</span><span>${Object.keys(E).filter(n => E[n].typed === false).join(', ') || 'none'} <span class="muted">(dashed)</span></span>
        <span class="k">Relationships</span><span>${rels.length} stored references</span>
        <span class="k">Sub-entities</span><span>${nSub} records keyed under a parent</span>
      </div></section>
      <section class="block"><h3>Relationship types</h3><ul class="plain">${Object.keys(REL_NAME).map(t =>
        `<li><span class="swatch" style="background:var(--r-${t})"></span> <b>${esc(REL_NAME[t])}</b> <span class="muted">${rels.filter(r => r.l.type === t).length || ''}</span><div class="muted small">${esc(RT[t] || '')}</div></li>`).join('')}</ul>
        <p class="muted small" style="margin-top:8px">Arrows run from the entity that stores the reference to the one it references. Solid lines are set every time the entity is created; dashed lines only under a condition.</p></section>
      <section class="block"><h3>Start with</h3><div class="chips">${['ACCOUNT', 'KAMI', 'HARVEST', 'NODE', 'ITEM', 'QUEST', 'CONDITION'].filter(x => E[x]).map(entBtn).join(' ')}</div></section>
      ${Object.keys(DL.families).length ? `<section class="block"><h3>Data logs (${Object.keys(DL.families).length} families)</h3><div class="chips">${Object.keys(DL.families).map(dlBtn).join(' ')}</div>
        <p class="muted small" style="margin:6px 0 0">Counters the game keeps per account (or for the world) at <code>is.data(holder, index, type)</code>: ${Object.keys(DL.logs).filter(x => !x.includes('*')).length} names${DL.scopes ? ` — ${['account', 'world', 'both', 'other'].filter(k => DL.scopes[k]).map(k => `${DL.scopes[k].length} ${k}`).join(', ')}` : ''}. Settings → “Data logs on the map” draws them beside their holders.</p></section>` : ''}
      <section class="block"><h3>Or a system</h3><div class="chips">${['HarvestStart', 'HarvestLiquidate', 'KamiGachaMint', 'QuestComplete', 'Craft'].filter(x => systems[x]).map(sysBtn).join(' ')}</div></section>
      ${Object.keys(SG).length ? `<section class="block"><h3>Singletons (${Object.keys(SG).length})</h3><ul class="plain">${Object.entries(SG).map(([n, g]) =>
        `<li><code>${esc(n)}</code> <span class="muted small">${esc(g.id)} · used in ${arr(g.used_in).map(esc).join(', ')}</span></li>`).join('')}</ul>
        <p class="muted small" style="margin:6px 0 0">One entity at a fixed id. Those an entity points at appear on the map as diamonds.</p></section>` : ''}
      ${arr(notes.open_questions).length ? `<section class="block"><h3>Open questions</h3><ul class="plain">${arr(notes.open_questions).map(q => '<li class="muted small">' + esc(q) + '</li>').join('')}</ul></section>` : ''}`;
  }
  const sysBtn = n => `<button class="sysbtn" data-sys="${esc(n)}" title="Show what ${esc(n)} touches">${esc(n)}</button>`;
  const DL = A.datalogs || { logs: {}, families: {}, notes: {} };
  const dlBtn = f => `<button class="ent dl" data-dl="${esc(f)}">${esc(f)}</button>`;
  const dlNamed = n => `<button class="ent dl" data-dl="${esc((DL.logs[n] || {}).family || '')}" title="${esc((DL.logs[n] || {}).family || '')} data logs"><code>${esc(n)}</code></button>`;
  const dlHeldBy = n => Object.entries(DL.families).filter(([f, names]) => names.some(x => arr((DL.logs[x] || {}).holder).includes(n))).map(([f]) => f);
  const dlWrittenBy = sys => Object.keys(DL.logs).filter(x => !x.includes('*') && arr(DL.logs[x].written_by).includes(sys));
  function dataPanel(fam) {
    const names = DL.families[fam] || [], note = ((DL.notes || {}).families || {})[fam] || {}, LN = (DL.notes || {}).logs || {};
    const holders = [...new Set(names.flatMap(x => arr(DL.logs[x].holder)))], writers = [...new Set(names.flatMap(x => arr(DL.logs[x].written_by)))].sort();
    const kindBtn = h => E[h] ? entBtn(h) : `<code>${esc(h)}</code>`;
    const row = x => { const l = DL.logs[x] || {}; return `<li class="dl-row"><div><code class="dl-name">${esc(x)}</code>${l.pattern ? ` <span class="tag">pattern</span>` : ''}${arr(l.op).length ? ` <span class="tag">${esc(arr(l.op).join(', '))}</span>` : ''}${l.scope ? ` <span class="tag" title="world: holder 0 only · account: per account · both: the same name at both levels">${esc(l.scope)}</span>` : ''}${LN[x] && LN[x].what ? `<div class="small">${esc(LN[x].what)}</div>` : ''}
      ${l.pattern && arr(l.values).length ? `<div class="small muted">= ${arr(l.values).map(v => '<code>' + esc(v) + '</code>').join(' ')}</div>` : ''}</div>
      <div class="kv small">
        <span class="k">holder</span><span>${arr(l.holder).map(kindBtn).join(', ') || '<span class="muted">—</span>'}</span>
        <span class="k">index</span><span>${arr(l.index).map(kindBtn).join(', ') || '<span class="muted">—</span>'}</span>
        <span class="k">written by</span><span class="chips">${arr(l.written_by).map(sysBtn).join(' ') || '<span class="muted">no deployed system</span>'}</span>
        ${arr(l.written_in).length ? `<span class="k">in</span><span>${arr(l.written_in).map(f => '<code>' + esc(f) + '</code>').join(', ')}</span>` : ''}
        ${arr(l.read_by).length ? `<span class="k">read by</span><span class="chips">${arr(l.read_by).map(sysBtn).join(' ')}</span>` : ''}
      </div></li>`; };
    panel.innerHTML = `<span class="pill" style="background:var(--muted)">Data logs</span>
      <h2>${esc(fam)}</h2>
      ${note.what ? `<p class="what">${esc(note.what)}</p>` : '<p class="what muted">No description in notes yet (notes/data-logs.yaml).</p>'}
      <section class="block"><div class="kv">
        <span class="k">kept at</span><code>keccak256(abi.encodePacked("is.data", holderID, uint32 index, type))</code>
        <span class="k">holders</span><span>${holders.map(kindBtn).join(', ')}</span>
        <span class="k">written by</span><span class="chips">${writers.map(sysBtn).join(' ') || '<span class="muted">no deployed system</span>'}</span>
        <span class="k">in the indexer</span><span>table <code>domain.rec_is_data</code>, keys <code>key_holder_id</code>, <code>key_index</code>, <code>key_type</code></span>
      </div></section>
      <section class="block"><h3>Counters (${names.filter(x => !x.includes('*')).length})</h3><ul class="plain dl-list">${names.map(row).join('')}</ul></section>`;
    panel.scrollTop = 0;
  }
  function dataView(fam) {
    if (!DL.families[fam]) return;
    A.navPush('data:' + fam);
    A.st.view = 'data'; A.st.sel = fam; A.st.system = null;
    if (A.showTab) A.showTab('details');
    dataPanel(fam);
  }
  const GH = 'https://github.com/Asphodel-OS/kami-atlas/blob/main/generated/systems/';
  function systemPanel(n, role) {
    const sy = systems[n] || {};
    const by = r => Object.keys(role).filter(k => role[k] === r).sort();
    const sec = (title, xs) => xs.length ? `<section class="block"><h3>${title} (${xs.length})</h3><div class="chips">${xs.map(entBtn).join(' ')}</div></section>` : '';
    const wwa = sy.writes_without_access_paths || {};
    panel.innerHTML = `<span class="pill" style="background:var(--muted)">System</span>${sy.disabled ? ' <span class="pill" style="background:var(--warn,#a33)">Disabled</span>' : ''}
      <h2>${esc(n)}</h2>
      ${sy.comment ? `<p class="what">${esc(sy.comment)}</p>` : ''}
      ${sy.disabled ? `<p class="small">Every entry point reverts before doing anything: <code>${esc(sy.disabled)}</code> What follows is what it does once re-enabled.</p>` : ''}
      <section class="block"><div class="kv">
        ${sy.args ? `<span class="k">args</span><code>${esc(sy.args)}</code>` : ''}
        ${arr(sy.events).length ? `<span class="k">events</span><span>${arr(sy.events).map(x => '<code>' + esc(x) + '</code>').join(', ')}</span>` : ''}
        ${arr(sy.config).length ? `<span class="k">config</span><span>${arr(sy.config).map(x => '<code>' + esc(x) + '</code>').join(', ')}</span>` : ''}
        <span class="k">full detail</span><a href="${GH}${encodeURIComponent(n)}.yaml" target="_blank" rel="noopener">generated/systems/${esc(n)}.yaml</a>
      </div></section>
      ${sec('Creates', by('creates'))}${sec('Changes', by('changes'))}${sec('Reads', by('reads'))}
      ${dlWrittenBy(n).length ? `<section class="block"><h3>Data logs written (${dlWrittenBy(n).length})</h3><div class="chips">${dlWrittenBy(n).map(dlNamed).join(' ')}</div></section>` : ''}
      <section class="block"><h3>Writes (${arr(sy.writes).length} components)</h3><div class="chips">${arr(sy.writes).map(c => `<span class="cmp ${compUse[c] && compUse[c].length === 1 ? 'own' : ''}">${esc(c)}</span>`).join('')}</div>
        <p class="muted small" style="margin:6px 0 0">An entity counts as changed or read when the system touches a field only that entity has; shared fields like Value can't be attributed.</p></section>
      ${arr(sy.writes_without_access).length ? `<section class="block"><h3>Writes without a deploy grant (${arr(sy.writes_without_access).length})</h3>
        <p class="muted small" style="margin:0 0 6px">Reachable writes to components this system is not granted in deploy.json. A write that actually runs reverts; whether it runs depends on the branch and the data.</p>
        <ul class="plain">${arr(sy.writes_without_access).map(c => `<li><span class="cmp">${esc(c)}</span> <span class="muted small">${wwa[c] ? esc(wwa[c].slice(1).join(' → ')) : 'path not found'}</span></li>`).join('')}</ul></section>` : ''}`;
    panel.scrollTop = 0;
  }
  function relRow(r, dir) {
    const l = r.l, other = dir === 'out' ? r.to : r.from;
    return `<div class="rel"><div class="head"><span class="swatch" style="background:var(--r-${l.type})"></span>
      <code>${esc(r.label)}</code>${r.via && r.via.kind === 'record' ? ` <span class="muted small">· ${esc(r.field)}</span>` : ''} ${dir === 'out' ? '→' : '←'} ${other ? entBtn(other) : '<span class="tag warn">unresolved</span>'}
      <span class="tag">${esc(REL_NAME[l.type])}</span><span class="tag">${l.required ? 'required' : 'optional'}</span>${r.via ? `<span class="tag">via ${r.via.kind === 'record' ? 'record' : 'anchor key'}</span>` : ''}
      ${r.fromNotes ? '<span class="tag">from notes</span>' : l.inferred ? '<span class="tag">inferred</span>' : ''}</div>
      <div class="muted small">${esc(l.cardinality || '')}${'changes_after_creation' in l ? ' · ' + (l.changes_after_creation ? 'can change after creation' : 'fixed at creation') : ''}${l.removable ? ' · removable' : ''}</div>
      <div class="muted small">down: ${esc(l.navigate?.down || '')}</div>
      ${r.note ? `<div class="small">${esc(r.note)}</div>` : ''}</div>`;
  }
  function focusPanel(n, sub) {
    const e = E[n], nt = NE[n] || {}, { out, in: inn } = relsOf(n), subs = subsOf(n), cb = changedBy(n);
    panel.innerHTML = `<span class="pill" style="background:var(--${e.layer})">${esc(LAYER_NAME[e.layer])}</span>
      <h2>${esc(n)}</h2>
      ${nt.what ? `<p class="what">${esc(nt.what)}</p>` : '<p class="what muted">No description in notes yet.</p>'}
      ${e.extends ? `<p class="small">Built on ${entBtn(e.extends)}: it has the same fields plus its own.</p>` : ''}
      ${arr(nt.confirmed).length ? `<div class="confirmed"><b>Confirmed</b><ul class="plain">${arr(nt.confirmed).map(c => '<li>' + esc(c) + '</li>').join('')}</ul></div>` : ''}
      <section class="block"><h3>Identity</h3><div class="kv">
        ${Object.entries(e.id || {}).map(([k, v]) => `<span class="k">${esc(k)} id</span><code>keccak256(${esc(v)})</code>`).join('') || '<span class="k">id</span><span class="muted">not from a hashed namespace</span>'}
        ${e.index ? `<span class="k">index</span><code>${esc(e.index)}</code>` : ''}
        <span class="k">named</span><span>${e.typed === false ? esc(String(e.name_from || '').replace(/^kami-atlas: /, '')).replace(/generate\.py/, 'the generator') : `from the contracts: the EntityType value <code>${esc(n)}</code>, set in ${arr(e.created_in).map(x => '<code>' + esc(x) + '</code>').join(', ')}`}</span>
        <span class="k">created in</span><span>${arr(e.created_in).map(x => '<code>' + esc(x) + '</code>').join(', ')}</span>
        <span class="k">created by</span><span class="chips">${arr(e.created_by).map(sysBtn).join(' ') || '<span class="muted">none</span>'}</span>
        <span class="k">changed by</span><span class="chips">${cb.map(sysBtn).join(' ') || '<span class="muted">not isolable</span>'}</span>
      </div></section>
      ${out.length ? `<section class="block"><h3>Stores a reference to (${out.length})</h3>${out.map(r => relRow(r, 'out')).join('')}</section>` : ''}
      ${inn.length ? `<section class="block"><h3>Referenced by (${inn.length})</h3>${inn.map(r => relRow(r, 'in')).join('')}</section>` : ''}
      ${dlHeldBy(n).length ? `<section class="block"><h3>Data logs held (${dlHeldBy(n).length} families)</h3><div class="chips">${dlHeldBy(n).map(dlBtn).join(' ')}</div>
        <p class="muted small" style="margin:6px 0 0">Counters kept at <code>is.data(${esc(n.toLowerCase())}ID, index, type)</code>; not fields of the entity.</p></section>` : ''}
      ${subs.length ? `<section class="block"><h3>Sub-entities (${subs.length})</h3><ul class="plain">${subs.map(x => `<li><code>${esc(x.ns)}</code> <span class="tag">${esc(x.s.kind || 'record')}</span>${x.s.shape ? ` <span class="tag">${esc(x.s.shape)}</span>` : ''}${x.shared ? ' <span class="tag">shared</span>' : ''}<div class="muted small">${esc(subKindLine(x))}</div>${(NS[x.ns] || {}).what ? `<div class="muted small">${esc(NS[x.ns].what)}</div>` : ''}</li>`).join('')}</ul></section>` : ''}
      <section class="block"><h3>Fields set at creation (${(e.components || []).length})</h3><div class="chips">${arr(e.components).map(c =>
        `<span class="cmp ${compUse[c].length === 1 ? 'own' : ''}" title="${esc((comps[c] || {}).type || '')}">${esc(c)}</span>`).join('')}</div>
        <p class="muted small" style="margin:6px 0 0">Outlined: no other entity type uses it.</p></section>`;
    panel.scrollTop = 0;
  }
  panel.addEventListener('click', ev => {
    const s_ = ev.target.closest('[data-sys]'); if (s_) { A.showTab('details'); A.overviewView(s_.dataset.sys); return; }
    const dl = ev.target.closest('[data-dl]'); if (dl) { dataView(dl.dataset.dl); return; }
    const b = ev.target.closest('[data-go]'); if (b) A.focusView(b.dataset.go);
  });

  Object.assign(A, { entBtn, sysBtn, overviewPanel, systemPanel, relRow, focusPanel, dataPanel, dataView, DL });
}
