// The drill-in view: one entity’s fields, what it references, what references it, and its sub-entities.
import { arr, trunc, LAYER_NAME } from './util.js';

export function install(A) {
  const { E, NS, comps, cfg, st, tip, wrap, root, ms, fit, ensureVisible, legend, relKeys, viaTip, viaGlyph, edgePath, cardOf, uniqueFields, relsOf, subsOf, subKindLine, show, hide, placeTip, entityTip, relTip, subTip, navPush, focusPanel } = A;
  function card(g, x, y, w, lines, opts) {
    const c = g.append('g').attr('class', 'card' + (opts.center ? ' center' : '') + (opts.go ? ' link' : '')).attr('transform', `translate(${x},${y})`).attr('data-k', opts.k || null);
    const h = 30 + lines.length * 16 + (opts.meta ? 16 : 0) + 6;
    c.append('rect').attr('class', 'box').attr('x', -w / 2).attr('y', 0).attr('width', w).attr('height', h).attr('rx', 6)
      .style('stroke', opts.stroke || null).style('fill', opts.fill || null);
    c.append('text').attr('class', 'title').attr('x', -w / 2 + 12).attr('y', 21).text(opts.title);
    let yy = 21;
    if (opts.meta) { yy += 16; c.append('text').attr('class', 'meta').attr('x', -w / 2 + 12).attr('y', yy).text(opts.meta); }
    lines.forEach(ln => { yy += 16; c.append('text').attr('class', 'field' + (ln.shared ? ' shared' : '')).attr('x', -w / 2 + 12).attr('y', yy).attr('data-k', ln.k || null).text(ln.text); });
    if (opts.go) c.attr('tabindex', 0).attr('role', 'button').attr('aria-label', 'Open ' + opts.go)
      .on('click', ev => { ev.stopPropagation(); focusView(opts.go); })
      .on('keydown', ev => { if (ev.key === 'Enter') focusView(opts.go); });
    if (opts.tip) c.on('mouseenter mousemove', ev => show(opts.tip(), ev)).on('mouseleave', hide);
    return { g: c, x, y, w, h };
  }

  function focusView(n, sub) {
    if (!E[n]) return;
    navPush('focus:' + n);
    st.view = 'focus'; st.sel = n; st.sub = sub; hide(); A.showTab('details');
    if (A.sim) A.sim.stop();
    root.selectAll('*').remove(); A.fitTarget = null;
    document.getElementById('crumb').hidden = false; wrap.classList.add('focus');
    document.getElementById('crumb-name').textContent = n;
    const e = E[n], ro = relsOf(n);
    const viaOk = r => cfg.viaEdges || !(r.via && r.via.kind === 'record');
    const out = ro.out.filter(r => st.rel.has(r.l.type) && viaOk(r)), inn = ro.in.filter(r => st.rel.has(r.l.type) && viaOk(r));
    const subs = st.rel.has('keyed') ? subsOf(n) : [];
    const eg = root.append('g'), cg = root.append('g');
    const own = new Set(uniqueFields(n));
    const idLine = Object.entries(e.id || {}).map(([k, v]) => `${k} id: ${v}`);
    const fields = (e.components || []).map(c => ({ text: (own.has(c) ? '● ' : '○ ') + c + ((comps[c] || {}).type ? ' : ' + comps[c].type : ''), shared: !own.has(c), k: own.has(c) ? 'field:own' : 'field:shared' }));
    const subK = x => 'rel:keyed req:required sub:' + (x.s.kind === 'anchor key' ? 'anchor' : 'record');
    const centerK = new Set(['layer:' + e.layer, 'field:own', 'field:shared', ...out.concat(inn).flatMap(relKeys), ...subs.map(subK).join(' ').split(' ').filter(Boolean)]);
    const center = card(cg, 0, 0, 300, fields, { center: true, title: n + (e.extends ? ' (extends ' + e.extends + ')' : ''), meta: trunc(idLine[0] || (e.index ? 'index: ' + e.index : 'id: not hashed'), 44),
      stroke: `var(--${e.layer})`, tip: () => entityTip(n), k: [...centerK].join(' ') });
    const cx = 0, cyMid = center.h / 2;

    // one card per neighbouring entity; several references to the same one share the card, one line and one edge each
    const colX = 420, gapY = 18, lineH = 16;
    const groupBy = (rs, key) => { const m = new Map(); rs.forEach(r => { const k = key(r); if (!m.has(k)) m.set(k, []); m.get(k).push(r); }); return [...m].map(([other, rs]) => ({ other, rs })); };
    const cardLines = grp => grp.rs.length > 1 ? grp.rs.map(r => ({ text: trunc(r.label + (r.l.required ? '' : ' (optional)'), 24), k: relKeys(r).join(' ') })) : [];
    const groupH = grp => 30 + cardLines(grp).length * lineH + lineH + 6;   // mirrors card(): title, layer line, one line per reference
    const place = (groups, x, label) => {
      if (!groups.length) return [];
      cg.append('text').attr('class', 'col-label').attr('x', x).attr('y', -34).attr('text-anchor', 'middle').text(label);
      const total = groups.reduce((s, g) => s + groupH(g), 0) + gapY * (groups.length - 1);
      let y = cyMid - total / 2;
      return groups.map(g => { const p = { g, x, y }; y += groupH(g) + gapY; return p; });
    };
    const outs = place(groupBy(out, r => r.to || 'unresolved'), colX, 'Stores a reference to');
    const ins = place(groupBy(inn, r => r.from), -colX, 'Referenced by');
    const colsBottom = Math.max(cyMid, ...outs.concat(ins).map(p => p.y + groupH(p.g)));
    const nav = { out: [], in: [], sub: [] };
    const drawRel = (p, dir) => {
      const { other, rs } = p.g, isEnt = !!E[other], lines = cardLines(p.g);
      const keys = [...new Set(rs.flatMap(relKeys).concat(isEnt ? ['layer:' + E[other].layer] : []))];
      const c = card(cg, p.x, p.y, 200, lines, { title: other, meta: isEnt ? LAYER_NAME[E[other].layer] + (rs.length > 1 ? ' · ' + rs.length + ' references' : '') : (rs[0].to ? 'not a game entity' : 'target not resolved'),
        go: isEnt ? other : null, stroke: isEnt ? `var(--${E[other].layer})` : 'var(--line)', tip: isEnt ? () => entityTip(other) : null, k: keys.join(' ') });
      nav[dir].push({ c, go: isEnt ? other : null, tip: isEnt ? () => entityTip(other) : () => relTip(rs[0]) });
      rs.forEach((r, i) => {
        const ey = lines.length ? p.y + 21 + lineH * (i + 2) - 5 : p.y + c.h / 2;   // at the reference's own line, or the card's middle
        const oy = lines.length ? (i - (rs.length - 1) / 2) * lineH : 0;              // parallel edges leave the centre card spread the same way
        const [sx, sy, tx, ty] = dir === 'out' ? [cx + 150, cyMid + oy, p.x - 100, ey] : [p.x + 100, ey, cx - 150, cyMid + oy];
        const path = edgePath(sx, sy, tx, ty, 2, 0);
        const g = eg.append('g').attr('class', 'edge ' + (r.l.required ? 'required' : 'optional'))
          .attr('data-k', relKeys(r).concat(isEnt && E[other].layer === e.layer ? ['layer:' + e.layer] : []).join(' '));
        g.append('path').attr('class', 'line').attr('d', path.d).style('stroke', `var(--r-${r.l.type})`).attr('marker-end', `url(#m-${r.l.type})`)
          .attr('marker-start', cardOf(r.l) ? `url(#c-${cardOf(r.l)}-${r.l.type})` : null);
        g.append('path').attr('class', 'hit').attr('d', path.d);
        g.append('text').attr('class', 'lbl').attr('x', path.lx).attr('y', path.ly - (r.via ? 11 : 5)).attr('text-anchor', 'middle')
          .style('fill', `var(--r-${r.l.type})`).text(r.label + (r.l.required ? '' : ' (optional)'));
        g.on('mouseenter mousemove', ev => show(relTip(r), ev)).on('mouseleave', hide);
        if (r.via) viaGlyph(g, r).attr('transform', `translate(${path.lx},${path.ly})`)
          .on('mouseenter mousemove', ev => { ev.stopPropagation(); show(viaTip(r, r.from), ev); });
      });
    };
    outs.forEach(p => drawRel(p, 'out'));
    ins.forEach(p => drawRel(p, 'in'));

    // sub-entities below, keyed from the parent
    if (subs.length) {
      const per = 3, w = 250, gapX = 24, colBottom = colsBottom;
      const top = Math.max(center.h, colBottom) + 110;   // below the reference columns, which can run past the centre card
      cg.append('text').attr('class', 'col-label').attr('x', 0).attr('y', top - 40).attr('text-anchor', 'middle').text('Sub-entities, keyed by this entity');
      const subLines = x => {
        const keyLine = x.s.keys ? 'keys: ' + Object.keys(x.s.keys).join(', ') : '';
        const whatLine = (NS[x.ns] || {}).what || '';
        const linkLines = Object.entries(x.s.links || {}).map(([c, l]) => ({ text: trunc(c + ' → ' + (arr(l.to).join('/') || '?') + (l.to_kind ? ' (' + l.to_kind + ')' : ''), 36) }));
        return [{ text: trunc(subKindLine(x), 36) }, whatLine && { text: trunc(whatLine, 36), shared: true }, keyLine && { text: trunc(keyLine, 36), shared: true }, ...linkLines].filter(Boolean);
      };
      const cardH = x => 30 + subLines(x).length * 16 + (x.shared ? 16 : 0) + 6;   // mirrors card()
      const rowY = [top];
      for (let r = 0; r * per < subs.length; r++) rowY.push(rowY[r] + Math.max(...subs.slice(r * per, r * per + per).map(cardH)) + 22);
      subs.forEach((x, i) => {
        const col = i % per, row = Math.floor(i / per);
        const cols = Math.min(per, subs.length - row * per);
        const px = (col - (cols - 1) / 2) * (w + gapX);
        const lines = subLines(x);
        const py = rowY[row];
        const c = card(cg, px, py, w, lines, { title: x.ns, meta: x.shared ? 'shared with ' + arr(x.s.held_by).filter(h => h !== n).join(', ') : '', stroke: `var(--r-keyed)`, tip: () => subTip(x, n), k: subK(x) });
        nav.sub.push({ c, tip: () => subTip(x, n) });
        if (x.s.kind === 'anchor key') c.g.select('rect').style('stroke-dasharray', '5 4');
        if (row === 0) {
          const path = edgePath(0, center.h, px, py, 2, 0);
          const g = eg.append('g').attr('class', 'edge required').attr('data-k', subK(x));
          g.append('path').attr('class', 'line').attr('d', path.d).style('stroke', 'var(--r-keyed)').attr('marker-end', 'url(#m-keyed)');
          g.append('path').attr('class', 'hit').attr('d', path.d);
          g.on('mouseenter mousemove', ev => show(subTip(x, n), ev)).on('mouseleave', hide);
        }
        if (sub === x.ns) c.g.select('rect').style('stroke-width', 3);
      });
    }
    const usedTypes = [...new Set(out.concat(inn).map(r => r.l.type))];
    legend([...new Set(usedTypes.concat(subs.length ? ['keyed'] : []))], n);
    // arrows: ←/→ move between "Referenced by", this entity and "Stores a reference to"; ↑/↓ within a column;
    // ↓ from this entity reaches the sub-entities (rows of 3). Enter opens the selected entity.
    let fsel = null;
    const mid = xs => Math.floor((xs.length - 1) / 2);
    const fshow = () => {
      cg.selectAll('.card').classed('kbd', false); hide();
      const it = fsel && nav[fsel.col][fsel.i]; if (!it) return;
      it.c.g.classed('kbd', true);
      ensureVisible(it.c.x, it.c.y + it.c.h / 2);
      setTimeout(() => { const r = it.c.g.node().getBoundingClientRect(); tip.innerHTML = it.tip(); tip.hidden = false; placeTip({ clientX: r.right, clientY: r.top }); }, ms(320));
    };
    A.arrowNav = {
      key(k) {
        const col = fsel && fsel.col, i = fsel ? fsel.i : 0, L = col ? nav[col].length : 0;
        if (k === 'ArrowRight') fsel = col === 'sub' ? { col, i: Math.min(i + 1, L - 1) } : col === 'in' ? null : !col && nav.out.length ? { col: 'out', i: mid(nav.out) } : fsel;
        else if (k === 'ArrowLeft') fsel = col === 'sub' ? { col, i: Math.max(i - 1, 0) } : col === 'out' ? null : !col && nav.in.length ? { col: 'in', i: mid(nav.in) } : fsel;
        else if (k === 'ArrowDown') fsel = col === 'sub' ? { col, i: Math.min(i + 3, L - 1) } : col ? { col, i: Math.min(i + 1, L - 1) } : nav.sub.length ? { col: 'sub', i: 0 } : fsel;
        else if (k === 'ArrowUp') fsel = col === 'sub' ? (i < 3 ? null : { col, i: i - 3 }) : col ? { col, i: Math.max(i - 1, 0) } : fsel;
        fshow();
      },
      enter() { const it = fsel && nav[fsel.col][fsel.i]; if (it && it.go) { focusView(it.go); return true; } return false; },
    };
    focusPanel(n, sub);
    try { history.replaceState(null, '', '#' + n); } catch (e) {}
    setTimeout(() => fit(350), 30);
  }

  Object.assign(A, { card, focusView });
}
