// Search, sidebar tabs and lists, keyboard shortcuts, settings, and the first view.
import { esc, arr, LAYERS, LAYER_NAME, LAYER_GLOSS, REL_NAME } from './util.js';

export function install(A) {
  const { E, NE, model, systems, rels, compUse, relsOf, subsOf, cfg, st, DEFAULTS, saveSettings, panel, svg, zoomBy, fit, hide, DIRS, navBack, overviewView, focusView } = A;
  // ---------------- search ----------------
  const q = document.getElementById('q'), res = document.getElementById('results');
  const index = [];
  for (const [n, e] of Object.entries(E)) {
    index.push({ label: n, kind: LAYER_NAME[e.layer].toLowerCase(), go: n });
    for (const ns of Object.keys(e.sub_entities || {})) index.push({ label: ns, kind: 'sub-entity of ' + n, go: n, sub: ns });
    for (const s of arr(e.created_by)) index.push({ label: s, kind: 'creates ' + n, go: n });
  }
  for (const [ns, s] of Object.entries(model.shared_sub_entities || {})) for (const h of arr(s.held_by)) if (E[h]) index.push({ label: ns, kind: 'shared, on ' + h, go: h, sub: ns });
  for (const n of Object.keys(systems)) if (!n.startsWith('__')) index.push({ label: n, kind: 'system', system: n });
  for (const c of Object.keys(compUse)) index.push({ label: c, kind: compUse[c].length === 1 ? 'field of ' + compUse[c][0] : 'field on ' + compUse[c].length + ' entities', go: compUse[c][0] });
  let hits = [], hi = 0;
  const renderHits = () => { res.hidden = !hits.length; res.innerHTML = hits.map((h, i) => `<button class="${i === hi ? 'on' : ''}" data-i="${i}">${esc(h.label)}<em>${esc(h.kind)}</em></button>`).join(''); };
  q.addEventListener('input', () => {
    const t = q.value.trim().toLowerCase(); hi = 0;
    hits = t ? index.filter(x => x.label.toLowerCase().includes(t)).sort((a, b) => a.label.toLowerCase().indexOf(t) - b.label.toLowerCase().indexOf(t)).slice(0, 12) : [];
    renderHits();
  });
  q.addEventListener('keydown', ev => {
    if (ev.key === 'ArrowDown') { hi = Math.min(hi + 1, hits.length - 1); renderHits(); ev.preventDefault(); }
    if (ev.key === 'ArrowUp') { hi = Math.max(hi - 1, 0); renderHits(); ev.preventDefault(); }
    if (ev.key === 'Enter' && hits[hi]) pick(hits[hi]);
    if (ev.key === 'Escape') { hits = []; renderHits(); }
  });
  res.addEventListener('click', ev => { const b = ev.target.closest('[data-i]'); if (b) pick(hits[+b.dataset.i]); });
  const pick = h => { hits = []; renderHits(); q.value = ''; if (h.system) { showTab('details'); overviewView(h.system); } else focusView(h.go, h.sub); };
  document.addEventListener('click', ev => { if (!ev.target.closest('.search')) { hits = []; renderHits(); } });
  document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && (st.view === 'focus' || st.system) && document.activeElement !== q) overviewView(); });

  // ---------------- controls ----------------
  const press = (id, on) => document.getElementById(id).setAttribute('aria-pressed', on ? 'true' : 'false');
  for (const l of LAYERS) document.getElementById('f-' + l).addEventListener('click', () => {
    if (st.layers.has(l) && st.layers.size > 1) st.layers.delete(l); else st.layers.add(l);
    LAYERS.forEach(x => press('f-' + x, st.layers.has(x)));
    saveSettings(); overviewView(st.system);
  });
  // side panel: tabs and collapse
  const side = document.querySelector('.main');
  const TABS = { details: panel, entities: document.getElementById('entities'), systems: document.getElementById('systems'), settings: document.getElementById('settings') };
  function showTab(t) {
    for (const [k, el] of Object.entries(TABS)) { document.getElementById('tab-' + k).setAttribute('aria-selected', k === t); el.hidden = k !== t; }
    if (t === 'systems') renderSystems();
    if (t === 'entities') renderEntities();
  }
  for (const k of Object.keys(TABS)) document.getElementById('tab-' + k).addEventListener('click', () => showTab(k));
  // systems tab: every system, grouped by the leading word of its name; click to see what it touches
  const sysQ = document.getElementById('sys-q'), sysList = document.getElementById('sys-list');
  const createdBy = {};
  for (const [n, e] of Object.entries(E)) for (const sy of arr(e.created_by)) (createdBy[sy] = createdBy[sy] || []).push(n);
  function renderSystems() {
    const q = sysQ.value.trim().toLowerCase(), groups = {};
    for (const n of Object.keys(systems).sort()) {
      const sy = systems[n], cr = createdBy[n] || [];
      if (q && ![n, sy.comment || '', ...cr, ...arr(sy.writes)].some(x => x.toLowerCase().includes(q))) continue;
      const g = (n.match(/^[A-Z][a-z]+/) || [n])[0]; (groups[g] = groups[g] || []).push(n);
    }
    // one-system groups read better together
    for (const g of Object.keys(groups)) if (groups[g].length === 1 && !q) { (groups.Other = groups.Other || []).push(...groups[g]); delete groups[g]; }
    if (groups.Other) { const o = groups.Other.sort(); delete groups.Other; groups.Other = o; }
    const item = n => { const sy = systems[n], cr = createdBy[n] || [];
      return `<button class="sys-item${st.system === n ? ' on' : ''}" data-sys="${esc(n)}"><code>${esc(n)}</code>${sy.disabled ? ' <span class="tag warn">disabled</span>' : ''}`
        + (sy.comment ? `<span class="muted small">${esc(sy.comment.replace(/^@notice\s*/, ''))}</span>` : '')
        + `<span class="muted small">${cr.length ? 'creates ' + esc(cr.join(', ')) + ' · ' : ''}writes ${arr(sy.writes).length} fields</span></button>`; };
    const names = Object.keys(groups);
    sysList.innerHTML = names.length ? names.map(g => `<section class="sys-group"><h3>${esc(g)} (${groups[g].length})</h3>${groups[g].map(item).join('')}</section>`).join('')
      : '<p class="muted small">No system matches.</p>';
  }
  sysQ.addEventListener('input', renderSystems);
  // entities tab: every entity by layer; hover or ↑/↓ points it out on the map, click or Enter opens it
  const entQ = document.getElementById('ent-q'), entList = document.getElementById('ent-list');
  let entOn = null;
  function renderEntities() {
    const q = entQ.value.trim().toLowerCase();
    const match = n => !q || [n, (NE[n] || {}).what || '', ...arr(E[n].components)].some(x => x.toLowerCase().includes(q));
    const item = n => { const e = E[n], ro = relsOf(n), ns = subsOf(n).length;
      return `<button class="ent-item${entOn === n || st.sel === n ? ' on' : ''}" data-ent="${esc(n)}"><code><span class="dot" style="border-color:var(--${e.layer});${e.typed === false ? 'border-style:dashed' : ''}"></span>${esc(n)}</code>`
        + ((NE[n] || {}).what ? `<span class="muted small">${esc(NE[n].what)}</span>` : '')
        + `<span class="muted small">${arr(e.components).length} fields${ns ? ` · ${ns} sub-entities` : ''} · ${ro.out.length} out, ${ro.in.length} in${e.typed === false ? ' · untyped' : ''}</span></button>`; };
    const groups = LAYERS.map(l => [l, Object.keys(E).filter(n => E[n].layer === l && match(n)).sort()]).filter(([, xs]) => xs.length);
    entList.innerHTML = groups.length ? groups.map(([l, xs]) => `<section class="ent-group"><h3>${esc(LAYER_NAME[l])} (${xs.length})</h3><p class="muted small" style="margin:0 0 4px">${esc(LAYER_GLOSS[l])}</p>${xs.map(item).join('')}</section>`).join('')
      : '<p class="muted small">No entity matches.</p>';
  }
  const pointAt = n => { if (A.arrowNav && A.arrowNav.select) A.arrowNav.select(n); };
  entQ.addEventListener('input', renderEntities);
  entList.addEventListener('click', ev => { const b = ev.target.closest('[data-ent]'); if (b) { entOn = null; focusView(b.dataset.ent); } });
  entList.addEventListener('mouseover', ev => { const b = ev.target.closest('[data-ent]'); if (b) pointAt(b.dataset.ent); });
  entList.addEventListener('mouseleave', () => pointAt(entOn));
  function stepEntity(delta) {
    const items = [...entList.querySelectorAll('.ent-item')]; if (!items.length) return;
    const at = items.findIndex(b => b.dataset.ent === (entOn || st.sel));
    entOn = items[at < 0 ? (delta > 0 ? 0 : items.length - 1) : Math.max(0, Math.min(items.length - 1, at + delta))].dataset.ent;
    renderEntities(); pointAt(entOn);
    const on = entList.querySelector('.ent-item.on'); if (on) on.scrollIntoView({ block: 'nearest' });
  }
  function stepSystem(delta) {
    const items = [...sysList.querySelectorAll('.sys-item')]; if (!items.length) return;
    const at = items.findIndex(b => b.dataset.sys === st.system);
    const next = items[at < 0 ? (delta > 0 ? 0 : items.length - 1) : Math.max(0, Math.min(items.length - 1, at + delta))].dataset.sys;
    if (next === st.system) return;
    const replace = !!st.system && st.view === 'overview';   // stepping through the list is one Back step, not one per system
    A.navRestoring = replace; try { overviewView(next); } finally { A.navRestoring = false; }
    renderSystems();
    const on = sysList.querySelector('.sys-item.on'); if (on) on.scrollIntoView({ block: 'nearest' });
  }
  document.addEventListener('keydown', ev => {
    if (ev.metaKey || ev.ctrlKey || ev.altKey || (ev.target.closest && ev.target.closest('input, textarea, select'))) return;
    if (DIRS[ev.key]) {
      const vert = ev.key === 'ArrowUp' || ev.key === 'ArrowDown', d_ = ev.key === 'ArrowDown' ? 1 : -1;
      if (vert && !cfg.collapsed && !TABS.systems.hidden) stepSystem(d_);
      else if (vert && !cfg.collapsed && !TABS.entities.hidden) stepEntity(d_);
      else if (A.arrowNav) A.arrowNav.key(ev.key);
      ev.preventDefault();
    } else if (ev.key === 'Enter' && !(ev.target.closest && ev.target.closest('a, [role="button"], button:not([role="tab"])'))) {
      if (!TABS.entities.hidden && !cfg.collapsed && entOn) { const n = entOn; entOn = null; focusView(n); ev.preventDefault(); }
      else if (A.arrowNav && A.arrowNav.enter()) ev.preventDefault();
    }
  });
  document.getElementById('navback').addEventListener('click', navBack);
  document.addEventListener('keydown', ev => { if (ev.key === 'Backspace' && !(ev.target.closest && ev.target.closest('input, textarea'))) { navBack(); ev.preventDefault(); } });
  sysList.addEventListener('click', ev => { const b = ev.target.closest('[data-sys]'); if (b) { overviewView(b.dataset.sys); showTab('details'); } });
  const hoverSys = n => { if (A.arrowNav && A.arrowNav.system) A.arrowNav.system(n); };
  sysList.addEventListener('mouseover', ev => { const b = ev.target.closest('[data-sys]'); hoverSys(b ? b.dataset.sys : null); });
  sysList.addEventListener('mouseleave', () => hoverSys(null));
  const handle = document.getElementById('sidehandle');
  function setCollapsed(c, instant) {
    cfg.collapsed = c; saveSettings();
    side.classList.toggle('collapsed', c);
    handle.setAttribute('aria-expanded', String(!c));
    handle.title = c ? 'Show the sidebar (])' : 'Hide the sidebar (])';
    document.getElementById('side').inert = c;  // collapsed panel can't take focus
    fit(instant ? 0 : 320);                      // the map re-centres in step with the slide
  }
  handle.addEventListener('click', () => setCollapsed(!cfg.collapsed));
  const lgEl = document.getElementById('legend'), lgBtn = document.getElementById('lg-toggle');
  function setLegendCollapsed(c, instant) {
    cfg.legendCollapsed = c; saveSettings();
    lgEl.classList.toggle('collapsed', c);
    lgBtn.setAttribute('aria-expanded', String(!c));
    lgBtn.title = c ? 'Show the legend (L)' : 'Collapse the legend (L)';
    fit(instant ? 0 : 320);
  }
  lgBtn.addEventListener('click', () => setLegendCollapsed(!cfg.legendCollapsed));
  document.addEventListener('keydown', ev => {
    if ((ev.key === 'l' || ev.key === 'L') && !ev.metaKey && !ev.ctrlKey && !(ev.target.closest && ev.target.closest('input'))) { setLegendCollapsed(!cfg.legendCollapsed); ev.preventDefault(); }
  });
  document.addEventListener('keydown', ev => {
    if (ev.key === ']' && !(ev.target.closest && ev.target.closest('input'))) { setCollapsed(!cfg.collapsed); ev.preventDefault(); }
  });

  // settings
  const relChips = document.getElementById('set-rels');
  relChips.innerHTML = Object.keys(REL_NAME).map(t => `<button class="chip" data-rel="${t}" aria-pressed="${st.rel.has(t)}"><span class="dot" style="background:var(--r-${t})"></span>${esc(REL_NAME[t])}</button>`).join('');
  const redraw = () => { if (st.view === 'focus' && st.sel) focusView(st.sel); else overviewView(st.system); };
  relChips.addEventListener('click', ev => {
    const b = ev.target.closest('[data-rel]'); if (!b) return;
    const t = b.dataset.rel;
    if (st.rel.has(t)) st.rel.delete(t); else st.rel.add(t);
    b.setAttribute('aria-pressed', st.rel.has(t)); saveSettings(); redraw();
  });
  function applyLabels() {
    svg.classed('lbl-off', cfg.edgeLabels === 'off').classed('lbl-on', cfg.edgeLabels === 'on').classed('names-off', !cfg.names);
    document.querySelectorAll('#set-lbl button').forEach(b => b.setAttribute('aria-pressed', b.dataset.v === cfg.edgeLabels));
    document.getElementById('set-names').checked = cfg.names;
    document.getElementById('set-via').checked = cfg.viaEdges;
  }
  document.getElementById('set-via').addEventListener('change', ev => { cfg.viaEdges = ev.target.checked; saveSettings(); redraw(); });
  document.getElementById('set-lbl').addEventListener('click', ev => { const b = ev.target.closest('[data-v]'); if (!b) return; cfg.edgeLabels = b.dataset.v; saveSettings(); applyLabels(); });
  document.getElementById('set-names').addEventListener('change', ev => { cfg.names = ev.target.checked; saveSettings(); applyLabels(); });
  const ranges = [['set-spacing', 'out-spacing', 'spacing', v => v.toFixed(1) + '×'], ['set-link', 'out-link', 'linkLen', v => String(v)]];
  for (const [id, outId, key, fmt] of ranges) {
    const el = document.getElementById(id), out = document.getElementById(outId);
    el.value = cfg[key]; out.textContent = fmt(+cfg[key]);
    el.addEventListener('input', () => { out.textContent = fmt(+el.value); });
    el.addEventListener('change', () => { cfg[key] = +el.value; saveSettings(); if (st.view === 'overview') overviewView(st.system); });
  }
  const live = [['set-lstr', 'out-lstr', 'lensStrength', v => v.toFixed(1)], ['set-lrad', 'out-lrad', 'lensRadius', v => v + 'px'],
    ['set-lfol', 'out-lfol', 'lensFollow', v => v.toFixed(2)], ['set-pstr', 'out-pstr', 'pushStrength', v => v.toFixed(1)], ['set-sdist', 'out-sdist', 'snapDist', v => v + 'px']];
  function applyCursor() {
    for (const [id, outId, key, fmt] of live) { document.getElementById(id).value = cfg[key]; document.getElementById(outId).textContent = fmt(+cfg[key]); }
    document.querySelectorAll('#set-lens button').forEach(b => b.setAttribute('aria-pressed', b.dataset.v === cfg.lensMode));
    document.getElementById('set-push').checked = cfg.pushOn; document.getElementById('set-snap').checked = cfg.snapOn;
  }
  for (const [id, outId, key, fmt] of live) {
    const el = document.getElementById(id);
    el.addEventListener('input', () => { cfg[key] = +el.value; document.getElementById(outId).textContent = fmt(+el.value); });
    el.addEventListener('change', saveSettings);
  }
  document.getElementById('set-lens').addEventListener('click', ev => { const b = ev.target.closest('[data-v]'); if (!b) return; cfg.lensMode = b.dataset.v; saveSettings(); applyCursor(); });
  document.getElementById('set-push').addEventListener('change', ev => { cfg.pushOn = ev.target.checked; saveSettings(); });
  document.getElementById('set-snap').addEventListener('change', ev => { cfg.snapOn = ev.target.checked; saveSettings(); });
  document.getElementById('set-reheat').addEventListener('click', () => { if (A.sim && st.view === 'overview') A.sim.alpha(0.8).restart(); });
  document.getElementById('set-unpin').addEventListener('click', () => {
    if (st.view !== 'overview') return;
    d3.selectAll('#svg .node').each(d => { if (d) { d.userPinned = false; d.fx = null; d.fy = null; } }).classed('pinned', false);
    if (A.sim) A.sim.alpha(0.3).restart();
  });
  function applyOverlays() {
    document.getElementById('legend').hidden = !cfg.legend; document.getElementById('hint').hidden = !cfg.hint;
    document.getElementById('set-legend').checked = cfg.legend; document.getElementById('set-hint').checked = cfg.hint;
  }
  document.getElementById('set-legend').addEventListener('change', ev => { cfg.legend = ev.target.checked; saveSettings(); applyOverlays(); });
  document.getElementById('set-hint').addEventListener('change', ev => { cfg.hint = ev.target.checked; saveSettings(); applyOverlays(); });
  document.getElementById('set-reset').addEventListener('click', () => {
    Object.assign(cfg, DEFAULTS); st.layers = new Set(LAYERS); st.rel = new Set(DEFAULTS.rel);
    LAYERS.forEach(x => press('f-' + x, true));
    relChips.querySelectorAll('[data-rel]').forEach(b => b.setAttribute('aria-pressed', 'true'));
    ranges.forEach(([id, outId, key, fmt]) => { document.getElementById(id).value = cfg[key]; document.getElementById(outId).textContent = fmt(+cfg[key]); });
    saveSettings(); applyLabels(); applyOverlays(); applyCursor(); setLegendCollapsed(false, true); setCollapsed(false); redraw();
  });
  LAYERS.forEach(x => press('f-' + x, st.layers.has(x)));
  applyLabels(); applyOverlays(); applyCursor();
  if (cfg.collapsed) setCollapsed(true, true);
  if (cfg.legendCollapsed) setLegendCollapsed(true, true);

  document.getElementById('back').addEventListener('click', () => overviewView());
  document.getElementById('fit').addEventListener('click', () => fit());
  document.getElementById('zin').addEventListener('click', () => zoomBy(1.35));
  document.getElementById('zout').addEventListener('click', () => zoomBy(1 / 1.35));
  document.addEventListener('keydown', ev => {
    if (ev.target.closest && ev.target.closest('input')) return;
    if (ev.key === '+' || ev.key === '=') { zoomBy(1.35); ev.preventDefault(); }
    else if (ev.key === '-' || ev.key === '_') { zoomBy(1 / 1.35); ev.preventDefault(); }
    else if (ev.key === '0') { fit(); ev.preventDefault(); }
    else if (ev.key === '/') { q.focus(); ev.preventDefault(); }
  });
  svg.on('click', ev => { if (ev.target === svg.node()) hide(); });
  window.addEventListener('resize', () => setTimeout(() => fit(0), 100));

  Object.assign(A, { showTab, renderSystems, renderEntities });   // before the first view runs: focusView reaches showTab through A

  // ---------------- go ----------------
  const src = model.source || {}, sha = String(src.commit || '').replace(/-dirty$/, ''), dirty = /-dirty$/.test(src.commit || '');
  const commitLink = src.repo && sha && sha !== 'unknown'
    ? `${src.ref ? ' on ' + esc(String(src.ref).replace(/^origin\//, '')) : ''} at <a href="${esc(src.repo)}/tree/${esc(sha)}${src.repo_path ? '/' + esc(src.repo_path) : ''}" target="_blank" rel="noopener" title="${esc(src.repo.replace('https://github.com/', ''))} @ ${esc(sha)}"><code>${esc(sha.slice(0, 7))}</code></a>`
      + (dirty ? ' <span title="built from a checkout with uncommitted contract changes">+ local changes</span>' : '')
    : '';
  document.getElementById('sourceline').innerHTML = `Kamigotchi World · ${Object.keys(E).length} entity types · ${rels.length} relationships · ${Object.keys(systems).length} systems · generated from the contracts${commitLink}`;
  const start = decodeURIComponent((location.hash || '').slice(1));
  if (E[start]) focusView(start); else if (start.startsWith('sys.') && systems[start.slice(4)]) overviewView(start.slice(4)); else overviewView();


}
