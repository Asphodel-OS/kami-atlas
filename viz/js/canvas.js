// The SVG scaffold: markers, zoom and pan, fit, arrow-key geometry, the legend, and edge geometry shared by both views.
import { esc, LAYERS, LAYER_NAME, REL_NAME, REL_GLOSS, REL_COMP, LAYER_GLOSS } from './util.js';

export function install(A) {
  const { wrap, cfg, cardOf, SUB_BY_NS, subTip } = A;
  // ---------------- svg scaffold ----------------
  const svg = d3.select('#svg');
  const defs = svg.append('defs');
  for (const t of Object.keys(REL_NAME)) defs.append('marker').attr('id', 'm-' + t).attr('viewBox', '0 0 10 10')
    .attr('refX', 9).attr('refY', 5).attr('markerWidth', 7).attr('markerHeight', 7).attr('orient', 'auto-start-reverse')
    .append('path').attr('d', 'M0 0L10 5L0 10z').style('fill', `var(--r-${t})`);
  // holder-end markers: crow's foot = many, bar = one, bar + dot = one per key (marker-start, so they sit at the holder)
  for (const t of Object.keys(REL_NAME)) for (const [k, d] of [['many', 'M0,5L10,0.5M0,5L10,5M0,5L10,9.5'], ['one', 'M8,0.5L8,9.5'], ['key', 'M8,0.5L8,9.5']]) {
    const m = defs.append('marker').attr('id', `c-${k}-${t}`).attr('viewBox', '0 0 12 10').attr('refX', 10).attr('refY', 5).attr('markerWidth', 12).attr('markerHeight', 10)
      .attr('markerUnits', 'userSpaceOnUse').attr('orient', 'auto-start-reverse');
    m.append('path').attr('d', d).attr('fill', 'none').style('stroke', `var(--r-${t})`).attr('stroke-width', 1.5);
    if (k === 'key') m.append('circle').attr('cx', 3).attr('cy', 5).attr('r', 2).style('fill', `var(--r-${t})`);
  }
  const root = svg.append('g');
  const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const ms = d => reduceMotion ? 0 : d;
  // Click and drag pans; scroll (wheel, trackpad or pinch) zooms around the pointer.
  const zoom = d3.zoom().scaleExtent([0.15, 4])
    .filter(ev => ev.type === 'wheel' || (!A.grabbing && (!ev.ctrlKey || ev.type === 'touchstart') && !ev.button))
    .wheelDelta(ev => -ev.deltaY * (ev.deltaMode === 1 ? 0.05 : ev.deltaMode ? 1 : 0.002) * (ev.ctrlKey ? 6 : 1))
    .on('zoom', ev => {
      root.attr('transform', ev.transform);
      svg.classed('zoomed-out', ev.transform.k < 0.6).classed('far', ev.transform.k < 0.35);
    });
  svg.call(zoom).on('dblclick.zoom', null);
  const zoomBy = f => svg.transition().duration(ms(200)).call(zoom.scaleBy, f);
  // arrow-key navigation: each view installs its own handler; the camera follows the keyboard selection
  function ensureVisible(x, y) {
    const t = d3.zoomTransform(svg.node()), [sx, sy] = t.apply([x, y]);
    const wide = window.matchMedia('(min-width: 901px)').matches;
    const right = W() - (!cfg.collapsed && wide ? document.getElementById('side').offsetWidth + 24 : 0) - 50;
    if (sx > 50 && sx < right && sy > 50 && sy < H() - 50) return;
    svg.transition().duration(ms(300)).call(zoom.transform, d3.zoomIdentity.translate((50 + right) / 2 - t.k * x, H() / 2 - t.k * y).scale(t.k));
  }
  // direction vectors for the arrow keys, and the best candidate in that direction (nearest, favouring straight ahead)
  const DIRS = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1] };
  function pickToward(from, dir, cands) {
    let best = null, bs = Infinity;
    for (const c of cands) {
      const dx = c.x - from.x, dy = c.y - from.y, d = Math.hypot(dx, dy); if (!d) continue;
      const cos = (dx * dir[0] + dy * dir[1]) / d; if (cos < 0.35) continue;
      const sc = d * (1 + 2.5 * (1 - cos)); if (sc < bs) { bs = sc; best = c; }
    }
    return best;
  }
  const W = () => wrap.clientWidth, H = () => wrap.clientHeight;
  function fit(dur = 450) {
    const b = (A.fitTarget || root).node().getBBox(); if (!b.width) return;
    const lg = document.getElementById('legend'), side_ = document.getElementById('side');
    const lw = (lg.hidden || cfg.legendCollapsed ? 0 : lg.offsetWidth) + 24;  // keep clear of the legend overlay
    const wide = window.matchMedia('(min-width: 901px)').matches;
    const rw = !cfg.collapsed && wide ? side_.offsetWidth + 24 : 0;  // and of the sidebar
    const aw = Math.max(200, W() - lw - rw);
    const s = Math.min(1.6, 0.92 / Math.max((b.width + 60) / aw, (b.height + 70) / H()));
    svg.transition().duration(ms(dur)).call(zoom.transform, d3.zoomIdentity.translate(lw + aw / 2 - s * (b.x + b.width / 2), H() / 2 - s * (b.y + b.height / 2)).scale(s));
  }
  // view: 'map' (the overview) or the name of the entity open in the drill-in view
  function legend(types, view) {
    const sw = inner => `<svg viewBox="0 0 28 10" aria-hidden="true">${inner}</svg>`;
    const line = (stroke, dash) => sw(`<line x1="1" y1="5" x2="21" y2="5" stroke="${stroke}" stroke-width="2"${dash ? ' stroke-dasharray="4 3"' : ''}/><path d="M20 1.5L27 5L20 8.5z" fill="${stroke}"/>`);
    const dot = (fill, stroke, dash, r = 3.6) => sw(`<circle cx="10" cy="5" r="${r}" fill="${fill}" stroke="${stroke}" stroke-width="1.5"${dash ? ' stroke-dasharray="2 1.5"' : ''}/>`);
    const box = dash => sw(`<rect x="3" y="1.5" width="16" height="7" rx="2" fill="none" stroke="var(--r-keyed)" stroke-width="1.3"${dash ? ' stroke-dasharray="3 2"' : ''}/>`);
    const row = (icon, name, gloss, hl) => `<span class="lg-row"${hl ? ` data-hl="${hl}"` : ''}>${icon}<b>${esc(name)}</b>${gloss ? `<i>${esc(gloss)}</i>` : ''}</span>`;
    const sub = t => `<span class="lg-sub">${esc(t)}</span>`;
    const isMap = view === 'map';
    const tint = l => `color-mix(in srgb, var(--${l}) 22%, var(--panel))`;
    document.getElementById('legend-body').innerHTML =
      '<span class="lg-title">Entities</span>'
      + sub(isMap ? 'colour = layer' : 'card outline = layer')
      + LAYERS.map(l => row(dot(tint(l), `var(--${l})`), LAYER_NAME[l], LAYER_GLOSS[l], 'layer:' + l)).join('')
      + (isMap ? sub('outline')
        + row(dot('none', 'var(--muted)'), 'Typed', 'has an EntityType', 'shape:typed')
        + row(dot('none', 'var(--muted)', true), 'Untyped shape', 'no EntityType', 'shape:untyped')
        + row(sw('<path d="M10,1L14,5L10,9L6,5Z" fill="none" stroke="var(--muted)" stroke-width="1.3"/>'), 'Singleton', 'one entity, fixed id', 'shape:singleton')
        + (A.cfg && A.cfg.dataLogs ? row(sw('<rect x="3" y="1.5" width="14" height="7" rx="2" fill="none" stroke="var(--muted)" stroke-width="1.3" stroke-dasharray="3 2"/>'), 'Data logs', 'counters kept per holder', 'shape:datalog') : '')
        + row(sw(`<circle cx="6" cy="5" r="2.2" fill="none" stroke="var(--muted)" stroke-width="1.2"/><circle cx="17" cy="5" r="4" fill="none" stroke="var(--muted)" stroke-width="1.2"/>`), 'Size', 'more sub-entities') : '')
      + '<span class="lg-title">References</span>'
      + sub('A → B: A stores B’s id or index')
      + sub('colour = relationship type')
      + types.map(t => row(line(`var(--r-${t})`), REL_NAME[t], REL_GLOSS[t] + (REL_COMP[t] ? ` · ${REL_COMP[t]}` : ''), 'rel:' + t)).join('')
      + sub('line')
      + row(line('var(--muted)'), 'Required', 'set whenever it’s created', 'req:required')
      + row(line('var(--muted)', true), 'Optional', 'set only under a condition', 'req:optional')
      + sub('passes through')
      + row(sw('<line x1="1" y1="5" x2="27" y2="5" stroke="var(--muted)" stroke-width="1.5"/><rect x="9" y="1.5" width="10" height="7" rx="2" fill="var(--panel)" stroke="var(--muted)" stroke-width="1.2"/>'), 'Record', 'stored under the holder', 'via:record')
      + row(sw('<line x1="1" y1="5" x2="27" y2="5" stroke="var(--muted)" stroke-width="1.5"/><circle cx="14" cy="5" r="3.8" fill="var(--panel)" stroke="var(--muted)" stroke-width="1.2" stroke-dasharray="2 1.5"/>'), 'Anchor key', 'grouped under the target', 'via:anchor')
      + sub('holder end = how many')
      + row(sw('<path d="M12,5L2,0.5M12,5L2,5M12,5L2,9.5" fill="none" stroke="var(--muted)" stroke-width="1.4"/><line x1="12" y1="5" x2="27" y2="5" stroke="var(--muted)" stroke-width="1.5"/>'), 'Many', 'per target', 'card:many')
      + row(sw('<line x1="4" y1="0.5" x2="4" y2="9.5" stroke="var(--muted)" stroke-width="1.4"/><line x1="1" y1="5" x2="27" y2="5" stroke="var(--muted)" stroke-width="1.5"/>'), 'One', 'per target', 'card:one')
      + row(sw('<line x1="4" y1="0.5" x2="4" y2="9.5" stroke="var(--muted)" stroke-width="1.4"/><circle cx="10" cy="5" r="2" fill="var(--muted)"/><line x1="1" y1="5" x2="27" y2="5" stroke="var(--muted)" stroke-width="1.5"/>'), 'One per key', 'per target, per key value', 'card:key')
      + sub('edge label = the component that stores it')
      + (!isMap && view ? '<span class="lg-title">Fields & records</span>'
        + row(sw('<circle cx="10" cy="5" r="3" fill="var(--ink)"/>'), 'Own field', `only ${view} has it`, 'field:own')
        + row(sw('<circle cx="10" cy="5" r="3" fill="none" stroke="var(--muted)" stroke-width="1.3"/>'), 'Shared field', 'other entities have it too', 'field:shared')
        + row(box(false), 'Record', 'sub-entity with stored fields', 'sub:record')
        + row(box(true), 'Anchor key', 'groups records that point at it', 'sub:anchor') : '');
  }

  // hovering a legend row dims every tagged graph element (data-k) that lacks the row's key
  const legendHL = k => root.selectAll('[data-k]').classed('lg-off', function () { return !!k && !this.getAttribute('data-k').split(' ').includes(k); });
  const lgBody = document.getElementById('legend-body');
  lgBody.addEventListener('mouseover', ev => { const r = ev.target.closest('.lg-row[data-hl]'); legendHL(r ? r.dataset.hl : null); });
  lgBody.addEventListener('mouseleave', () => legendHL(null));
  const relKeys = r => ['rel:' + r.l.type, 'req:' + (r.l.required ? 'required' : 'optional')].concat(r.via ? ['via:' + r.via.kind] : [], cardOf(r.l) ? ['card:' + cardOf(r.l)] : []);
  const viaTip = (r, holder) => { const x = SUB_BY_NS[r.via.ns[0]]; return x ? subTip(x, holder) : `<h4>${esc(r.via.ns.join(', '))}</h4>`; };
  // a small glyph on the edge for what it passes through: a record (box) or an anchor key (dashed circle)
  const viaGlyph = (g, r) => { const v = g.append('g').attr('class', 'via'); if (r.via.kind === 'record') v.append('rect').attr('x', -7).attr('y', -4.5).attr('width', 14).attr('height', 9).attr('rx', 2); else v.append('circle').attr('r', 5); return v; };

  function edgePath(sx, sy, tx, ty, rT, bend, rS = 0) {
    const dx = tx - sx, dy = ty - sy, len = Math.hypot(dx, dy) || 1;
    const ex = tx - dx / len * rT, ey = ty - dy / len * rT;
    sx += dx / len * rS; sy += dy / len * rS;
    const mx = (sx + ex) / 2 - dy / len * bend, my = (sy + ey) / 2 + dx / len * bend;
    return { d: `M${sx},${sy}Q${mx},${my} ${ex},${ey}`, lx: (sx + 2 * mx + ex) / 4, ly: (sy + 2 * my + ey) / 4 };
  }

  Object.assign(A, { svg, defs, root, reduceMotion, ms, zoom, zoomBy, ensureVisible, DIRS, pickToward, W, H, fit, legend, legendHL, relKeys, viaTip, viaGlyph, edgePath });
}
