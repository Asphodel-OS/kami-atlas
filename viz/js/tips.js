// Tooltips: the HTML for entities, singletons, relationships and sub-entities, and where to put it.
import { esc, arr, list, REL_NAME, LAYER_NAME } from './util.js';

export function install(A) {
  const { E, NE, SG, RT, NS, st, changedBy, subsOf, relsOf, subKindLine } = A;
  // ---------------- tooltip ----------------
  const tip = document.getElementById('tip'), wrap = document.getElementById('map');
  function placeTip(ev) {
    const r = wrap.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    let x = ev.clientX - r.left + 14, y = ev.clientY - r.top + 14;
    if (x + w > r.width - 8) x = Math.max(8, ev.clientX - r.left - w - 14);
    if (y + h > r.height - 8) y = Math.max(8, r.height - h - 8);
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }
  function entityTip(n) {
    const e = E[n], nt = NE[n] || {}, cb = changedBy(n);
    return `<h4>${esc(n)}</h4><span class="pill" style="background:var(--${e.layer})">${esc(LAYER_NAME[e.layer])}</span>
      ${nt.what ? `<div style="margin-top:6px">${esc(nt.what)}</div>` : ''}
      <div class="row"><b>Created by</b><span>${list(arr(e.created_by))}</span></div>
      <div class="row"><b>Changed by</b><span>${cb.length ? list(cb) : '<span class="muted">no field unique to it, so not isolable</span>'}</span></div>
      <div class="row"><b>Structure</b><span>${(e.components || []).length} fields · ${subsOf(n).length} sub-entities · ${relsOf(n).out.length} out, ${relsOf(n).in.length} in</span></div>
      <div class="muted small" style="margin-top:6px">Click to open its structure.</div>`;
  }
  function singletonTip(n) {
    const g = SG[n] || {};
    return `<h4>${esc(n)}</h4><span class="pill" style="background:var(--muted)">Singleton</span>
      <div style="margin-top:6px">One entity at a fixed id, not a type: there is only ever one.</div>
      <div class="row"><b>Id</b><span><code>${esc(g.id || '')}</code> (constant <code>${esc(g.constant || '')}</code>)</span></div>
      <div class="row"><b>Used in</b><span>${list(arr(g.used_in))}</span></div>`;
  }
  function datalogTip(fam) {
    const DL = A.datalogs || { logs: {}, families: {}, notes: {} }, names = (DL.families[fam] || []).filter(x => !x.includes('*'));
    const note = ((DL.notes || {}).families || {})[fam] || {};
    return `<h4>${esc(fam)}</h4><span class="pill" style="background:var(--muted)">Data logs</span>
      ${note.what ? `<div style="margin-top:6px">${esc(note.what)}</div>` : ''}
      <div class="row"><b>Counters</b><span>${list(names, 6)}</span></div>
      <div class="muted small" style="margin-top:6px">Click to list them with their holders, indexes and writers.</div>`;
  }
  function systemTip(n, role) {
    return `<h4>${esc(n)}</h4><div><b>${esc(role)}</b> by <code>${esc(st.system)}</code></div>${entityTip(n).replace(/^<h4>.*?<\/h4>/, '')}`;
  }
  function relTip(r) {
    const l = r.l;
    const dir = `<code>${esc(r.from)}</code> → <code>${esc(r.to || '?')}</code>`;
    return `<h4>${esc(r.label)}${r.via && r.via.kind === 'record' ? ` <span class="muted">· ${esc(r.field)}</span>` : ''}</h4>
      <div>${dir} · ${esc(REL_NAME[l.type] || l.type)} · ${l.required ? 'required' : 'optional'}</div>
      ${r.via ? `<div class="row"><b>Through</b><span>${r.via.kind === 'record' ? 'the record ' : 'the anchor key' + (r.via.ns.length > 1 ? 's ' : ' ')}${r.via.ns.map(x => '<code>' + esc(x) + '</code>').join(', ')}</span></div>` : ''}
      <div class="muted small" style="margin-top:2px">${esc(RT[l.type] || '')}</div>
      <div class="row"><b>Stored on</b><span><code>${esc(l.stored_on)}</code>, as the value of <code>${esc(r.comp)}</code></span></div>
      ${!l.required && l.only_when ? `<div class="row"><b>Only when</b><span>${l.only_when.map(c => '<code>' + esc(c) + '</code>').join('<br>')}</span></div>` : ''}
      <div class="row"><b>Cardinality</b><span>${esc(l.cardinality || '')}</span></div>
      ${'changes_after_creation' in l ? `<div class="row"><b>Lifecycle</b><span>${l.changes_after_creation ? 'can change after creation' : 'fixed at creation'}${l.removable ? '; can be removed' : ''}</span></div>` : ''}
      <div class="row"><b>Navigate</b><span>up: ${esc(l.navigate?.up || '')}<br>down: ${esc(l.navigate?.down || '')}</span></div>
      <div class="row"><b>Derived</b><span><ul>${arr(l.derived).map(d => '<li><code>' + esc(d) + '</code></li>').join('')}</ul>
        ${r.fromNotes ? `<div>Target set in notes: ${esc(r.note || '')}</div>` : l.inferred ? '<div class="muted">Target inferred by tracing variable names through callers.</div>' : ''}
        ${r.unresolved ? '<div style="color:var(--warn)">Target not resolved from the code.</div>' : ''}</span></div>`;
  }
  function subTip(x, parent) {
    const s = x.s, keys = s.keys ? Object.entries(s.keys).map(([k, v]) => esc(k) + (v ? ' → ' + arr(v).map(esc).join('/') : '')).join(', ') : '';
    return `<h4>${esc(x.ns)}</h4>${(NS[x.ns] || {}).what ? `<div>${esc(NS[x.ns].what)}</div>` : ''}
      <div class="row"><b>Kind</b><span>${esc(subKindLine(x))}</span></div>
      <div class="row"><b>Id</b><span><code>${esc(String(s.id || x.ns).startsWith('address(') ? s.id : 'keccak256(' + (s.id || x.ns) + ')')}</code></span></div>
      ${keys ? `<div class="row"><b>Keys</b><span>${keys}</span></div>` : ''}
      <div class="row"><b>Relation</b><span>${esc(REL_NAME.keyed)} of <code>${esc(parent)}</code>${x.shared ? ' (shared by ' + arr(s.held_by).map(esc).join(', ') + ')' : ''}; required by construction</span></div>
      ${s.links ? `<div class="row"><b>Points at</b><span>${Object.entries(s.links).map(([c, l]) => `<code>${esc(c)}</code> → ${arr(l.to).map(t => E[t] ? A.entBtn(t) : '<code>' + esc(t) + '</code>' + (l.to_kind ? ' (' + esc(l.to_kind) + ')' : '')).join('/') || '<span class="muted">unresolved</span>'}`).join('<br>')}</span></div>` : ''}
      <div class="row"><b>Navigate</b><span>${esc(s.relation?.navigate?.down || 'compute the id from the parent')}</span></div>
      <div class="row"><b>Placed by</b><span>${esc(s.parent_from || '')}</span></div>`;
  }
  const show = (html, ev) => { tip.innerHTML = html; tip.hidden = false; placeTip(ev); };
  const hide = () => { tip.hidden = true; };

  Object.assign(A, { tip, wrap, placeTip, show, hide, entityTip, singletonTip, datalogTip, systemTip, relTip, subTip });
}
