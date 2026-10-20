// The overview: every entity in three layer bands, with the cursor lens, repulsion and snap.
import { arr } from './util.js';

export function install(A) {
  const { E, rels, SG, systems, layout, cfg, st, panel, tip, wrap, svg, root, zoom, ms, reduceMotion, W, H, fit, ensureVisible, DIRS, pickToward, legend, relKeys, viaTip, viaGlyph, edgePath, cardOf, uniqueFields, subsOf, datalogTip, show, hide, placeTip, entityTip, singletonTip, systemTip, relTip, navPush, focusView, focusPanel, systemPanel, overviewPanel } = A;
  const BAND = 320, bandY = { runtime: 0, hybrid: 1, registry: 2 };
  const radius = n => E[n] ? 12 + Math.sqrt(subsOf(n).length) * 3.2 : 12;

  function overviewView(system, pos) {
    navPush('overview:' + (system && systems[system] ? system : ''));
    st.view = 'overview'; st.sel = null; st.system = system && systems[system] ? system : null; hide();
    document.getElementById('crumb').hidden = !st.system; wrap.classList.toggle('focus', !!st.system);
    document.getElementById('crumb-name').textContent = st.system ? 'System: ' + st.system : '';
    root.selectAll('*').remove();
    const ids = Object.keys(E).filter(n => st.layers.has(E[n].layer));
    const nodes = ids.map(id => ({ id, layer: E[id].layer }));
    // singletons that an entity points at (the gacha pool) join the map beside their referrers
    for (const r of rels) if (r.to && SG[r.to] && ids.includes(r.from) && !nodes.some(n => n.id === r.to)) nodes.push({ id: r.to, layer: E[r.from].layer, singleton: true });
    // data-log families beside their holders (Settings → Data logs on the map; off by default)
    const DL = A.datalogs || { logs: {}, families: {} }, dlLinks = [];
    if (cfg.dataLogs) for (const [fam, names] of Object.entries(DL.families)) {
      const holders = new Set(names.flatMap(x => arr((DL.logs[x] || {}).holder)));
      const targets = [...holders].filter(h => nodes.some(n => n.id === h));
      if (!targets.length && !holders.has('world')) continue;
      const id = 'dl:' + fam;
      nodes.push({ id, layer: targets.length ? E[targets[0]].layer : 'runtime', datalog: true, fam, world: holders.has('world') });
      for (const t of targets) dlLinks.push({ source: id, target: t });
    }
    const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
    const er = rels.filter(r => r.to && byId[r.from] && byId[r.to] && r.from !== r.to && st.rel.has(r.l.type) && (cfg.viaEdges || !(r.via && r.via.kind === 'record')));
    const pairCount = {};
    er.forEach(r => { const k = [r.from, r.to].sort().join('|'); r._i = pairCount[k] = (pairCount[k] || 0) + 1; });
    const links = er.map(r => ({ source: r.from, target: r.to, r }));

    const bands = root.append('g');
    const content = root.append('g'); A.fitTarget = null;  // bands are sized to the nodes, so fit everything
    const eg = content.append('g'), ng = content.append('g');
    const dledge = eg.selectAll('g.datalog').data(dlLinks).join('g').attr('class', 'edge datalog');
    dledge.append('path').attr('class', 'line');
    const edge = eg.selectAll('g:not(.datalog)').data(links).join('g').attr('class', d => 'edge ' + (d.r.l.required ? 'required' : 'optional'));
    edge.append('path').attr('class', 'line').style('stroke', d => `var(--r-${d.r.l.type})`).attr('marker-end', d => `url(#m-${d.r.l.type})`)
      .attr('marker-start', d => cardOf(d.r.l) ? `url(#c-${cardOf(d.r.l)}-${d.r.l.type})` : null);
    edge.append('path').attr('class', 'hit');
    edge.append('text').attr('class', 'lbl').attr('text-anchor', 'middle').style('fill', d => `var(--r-${d.r.l.type})`).text(d => d.r.label);
    edge.filter(d => d.r.via).each(function (d) {
      viaGlyph(d3.select(this), d.r)
        .on('mouseenter', ev => { if (typeof focusId !== 'undefined' && (focusId || drag)) return; ev.stopPropagation(); show(viaTip(d.r, d.r.from), ev); })
        .on('mouseleave', ev => { if (typeof focusId !== 'undefined' && (focusId || drag)) return; show(relTip(d.r), ev); });
    });
    edge.on('mouseenter', (ev, d) => { show(relTip(d.r), ev); edge.classed('faded', e => e !== d); node.classed('faded', n => n.id !== d.source.id && n.id !== d.target.id); })
      .on('mousemove', ev => placeTip(ev))
      .on('mouseleave', () => { hide(); edge.classed('faded', false); node.classed('faded', false); applySystem(); });

    const node = ng.selectAll('g').data(nodes).join('g')
      .attr('class', d => 'node' + (d.singleton ? ' singleton' : d.datalog ? ' datalog' : E[d.id] && E[d.id].typed === false ? ' untyped' : ''))
      .attr('tabindex', 0).attr('role', 'button').attr('aria-label', d => d.id);
    node.filter(d => d.datalog).append('rect').attr('x', -11).attr('y', -9).attr('width', 22).attr('height', 18).attr('rx', 3);
    node.filter(d => !d.singleton && !d.datalog).append('circle').attr('r', d => radius(d.id))
      .style('fill', d => `color-mix(in srgb, var(--${d.layer}) 22%, var(--panel))`).style('stroke', d => `var(--${d.layer})`);
    node.filter(d => d.singleton).append('path').attr('d', 'M0,-12L12,0L0,12L-12,0Z')
      .style('fill', 'var(--panel)').style('stroke', 'var(--muted)');
    node.append('text').attr('class', 'name').attr('text-anchor', 'middle').attr('dy', d => radius(d.id) + 14).text(d => d.datalog ? d.fam : d.id);
    const nk = Object.fromEntries(nodes.map(d => [d.id, new Set(d.singleton ? ['shape:singleton'] : d.datalog ? ['shape:datalog'] : ['layer:' + d.layer, E[d.id].typed === false ? 'shape:untyped' : 'shape:typed'])]));
    links.forEach(l => relKeys(l.r).forEach(k => { nk[l.r.from].add(k); nk[l.r.to].add(k); }));
    node.attr('data-k', d => [...nk[d.id]].join(' '));
    edge.attr('data-k', d => { const a = byId[d.r.from], b = byId[d.r.to]; return relKeys(d.r).concat(!a.singleton && !b.singleton && a.layer === b.layer ? ['layer:' + a.layer] : []).join(' '); });
    const rolesOf = name => {
      const r = {}, sy = systems[name] || {}, w = new Set(sy.writes || []), rd = new Set(sy.reads || []);
      for (const n of ids) {
        const own = uniqueFields(n);
        if (arr(E[n].created_by).includes(name)) r[n] = 'creates';
        else if (own.some(c => w.has(c))) r[n] = 'changes';
        else if (own.some(c => rd.has(c))) r[n] = 'reads';
      }
      return r;
    };
    const role = st.system ? rolesOf(st.system) : {};
    // fade what a system doesn't touch and label what it does: creates / changes / reads above each node
    const showRoles = r => {
      node.selectAll('.badge').remove();
      node.classed('faded', d => !r[d.id]);
      edge.classed('faded', d => !(r[d.source.id] && r[d.target.id]));
      node.filter(d => r[d.id]).append('text').attr('class', 'badge').attr('text-anchor', 'middle')
        .attr('dy', d => -radius(d.id) - 6).style('fill', d => r[d.id] === 'reads' ? 'var(--muted)' : 'var(--ink)').text(d => r[d.id]);
    };
    const applySystem = () => { if (st.system) showRoles(role); else node.selectAll('.badge').remove(); };
    const near = id => { const s = new Set([id]); links.forEach(l => { if (l.source.id === id) s.add(l.target.id); if (l.target.id === id) s.add(l.source.id); }); return s; };
    const highlight = id => {
      if (!id) { node.classed('faded', false); edge.classed('faded', false); applySystem(); return; }
      const s = near(id);
      node.classed('faded', d => !s.has(d.id));
      edge.classed('faded', d => d.source.id !== id && d.target.id !== id);
    };
    const flyTo = d => {
      hide(); highlight(null);
      const k = 1.8;
      svg.transition().duration(ms(320)).call(zoom.transform, d3.zoomIdentity.translate(W() / 2 - k * d.x, H() / 2 - k * d.y).scale(k))
        .on('end', () => focusView(d.id));
      if (reduceMotion) focusView(d.id);
    };
    const tipFor = d => d.singleton ? singletonTip(d.id) : d.datalog ? datalogTip(d.fam) : st.system && role[d.id] ? systemTip(d.id, role[d.id]) : entityTip(d.id);
    const open = d => d.datalog ? A.dataView(d.fam) : focusView(d.id);
    node.on('keydown', (ev, d) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(d); } });

    if (A.sim) A.sim.stop();
    // the committed layout (notes/layout.json) holds its entities in place; the rest settle around them with seeded
    // physics, so the same model draws the same map on every load
    for (const d of nodes) { const p = layout[d.id]; if (p) { d.x = d.fx = p[0]; d.y = d.fy = p[1]; d.held = true; } }
    A.sim = d3.forceSimulation(nodes).randomSource(d3.randomLcg(0.42))
      .force('link', d3.forceLink(links).id(d => d.id).distance(cfg.linkLen).strength(0.18))
      .force('dlink', d3.forceLink(dlLinks).id(d => d.id).distance(cfg.linkLen * 0.6).strength(0.5))
      .force('charge', d3.forceManyBody().strength(-620 * cfg.spacing).distanceMax(700 * Math.max(1, cfg.spacing)))
      .force('y', d3.forceY(d => bandY[d.layer] * BAND).strength(0.45))
      .force('x', d3.forceX(0).strength(0.08))
      .force('collide', d3.forceCollide(d => radius(d.id) + 34))
      .stop();
    for (let i = 0; i < 400; i++) A.sim.tick();  // settle before the first frame
    if (pos) for (const d of nodes) { const p = pos.get(d.id); if (p) { d.x = p.x; d.y = p.y; d.vx = d.vy = 0; d.userPinned = p.pin; d.held = p.held; if (p.pin || p.held) { d.fx = p.x; d.fy = p.y; } else { d.fx = d.fy = null; } } }
    A.curNodes = nodes; node.classed('pinned', d => !!d.userPinned);

    // ---- cursor lens, repulsion, snap ----
    // Layout space is d.x/d.y (physics). Display space is d.px/d.py/d.pz, recomputed every frame through the lens.
    // The lens never writes x/y; repulsion only changes velocities; snapping and hit-testing use display space.
    const mouse = { x: 0, y: 0, sx: 0, sy: 0, inside: false };   // layout + screen
    const lens = { x: 0, y: 0, s: 0 };
    let snapped = null, drag = null, dragFrom = null, dragMoved = false, focusId = null, edgeHover = false;
    const kNow = () => d3.zoomTransform(svg.node()).k;
    const fisheye = (x, y, m, R) => {
      const dx = x - lens.x, dy = y - lens.y, dd = Math.hypot(dx, dy);
      if (!dd || dd >= R || m <= 0) return [x, y, 1];
      const e = Math.exp(m), k0 = e / (e - 1) * R, k1 = m / R;
      const k = k0 * (1 - Math.exp(-dd * k1)) / dd * 0.75 + 0.25;
      return [lens.x + dx * k, lens.y + dy * k, Math.min(k, 10)];
    };
    const gauss = (x, y, m, R) => {
      const dx = x - lens.x, dy = y - lens.y, d2 = dx * dx + dy * dy, sg = R / 2, g = Math.min(m / 4, 2) * Math.exp(-d2 / (2 * sg * sg));
      return [lens.x + dx * (1 + g), lens.y + dy * (1 + g), 1 + g * 1.5];
    };
    const hyper = (x, y, m, Rpx) => {
      const dx = x - lens.x, dy = y - lens.y, d = Math.hypot(dx, dy);
      if (!d || m <= 0) return [x, y, 1];
      const R = Math.max(W(), H()) / kNow() * 0.75, c = R / (1 + m * (cfg.lensRadius / 180));
      const t = Math.tanh(d / c) * R / d, sb = 1 - lens.s, k = sb + (1 - sb) * t;
      const local = (R / c) / Math.cosh(d / c) ** 2;
      return [lens.x + dx * k, lens.y + dy * k, Math.max(0.35, sb + (1 - sb) * Math.min(local, 6))];
    };
    const project = () => {
      const mode = drag ? 'off' : cfg.lensMode, R = cfg.lensRadius / kNow();
      const f = mode === 'soft' ? gauss : mode === 'hyperbolic' ? hyper : mode === 'fisheye' ? fisheye : null;
      const m = mode === 'hyperbolic' ? cfg.lensStrength : cfg.lensStrength * lens.s;
      for (const d of nodes) { const p = f ? f(d.x, d.y, m, R) : [d.x, d.y, 1]; d.px = p[0]; d.py = p[1]; d.pz = p[2]; }
    };
    const drawnR = d => radius(d.id) * Math.sqrt(d.pz || 1);
    const screenGap = d => (Math.hypot(d.px - mouse.x, d.py - mouse.y) - drawnR(d)) * kNow();  // px from cursor to the drawn edge
    const nearest = () => { let best = null, bd = Infinity; for (const d of nodes) { const g = screenGap(d); if (g < bd) { best = d; bd = g; } } return [best, bd]; };
    const setSnap = d => {
      if (d === snapped) return;
      if (snapped && snapped !== drag && snapped !== kbd && !snapped.userPinned && !snapped.held) { snapped.fx = null; snapped.fy = null; }
      snapped = d;
      if (snapped && !snapped.userPinned && !snapped.held) { snapped.fx = snapped.x; snapped.fy = snapped.y; }  // so physics can't slide it away
    };
    const updateSnap = () => {
      if (drag) return;
      if (!cfg.snapOn || !mouse.inside) { setSnap(null); return; }
      const reach = cfg.snapDist;
      if (snapped) {
        const g = screenGap(snapped);
        if (g > reach * 1.7) setSnap(null);
        else { const [n, ng_] = nearest(); if (n !== snapped && ng_ < reach * 0.5 && ng_ < g - 6) setSnap(n); return; }
      }
      const [n, g] = nearest(); if (g < reach) setSnap(n);
    };
    A.sim.force('push', alpha => {   // repulsion: clears space around the cursor; links spring nodes back
      if (!cfg.pushOn || !mouse.inside || drag || kbd) return;
      const k = kNow(), R = cfg.lensRadius * 0.6 / k, strength = cfg.pushStrength * 0.9 / k;
      for (const d of nodes) {
        if (d === snapped || d.fx != null) continue;
        const dx = d.x - lens.x, dy = d.y - lens.y, dd = Math.hypot(dx, dy) || 0.01;
        if (dd > R) continue;
        const f = strength * (1 - dd / R) ** 2 * alpha * 4;
        d.vx += dx / dd * f; d.vy += dy / dd * f;
      }
    });
    const ov = content.append('g').attr('class', 'lens-ov').style('pointer-events', 'none');
    const ring = ov.append('circle').attr('fill', 'none').style('stroke', 'var(--ink)').attr('stroke-width', 2).attr('vector-effect', 'non-scaling-stroke').attr('display', 'none');
    // with the details panel showing the preview, the tooltip would only repeat it
    const panelShows = d => !d.singleton && !d.datalog && !st.system && !cfg.collapsed && !panel.hidden && window.matchMedia('(min-width: 901px)').matches;
    const tipAtNode = d => {
      if (panelShows(d)) { tip.hidden = true; return; }
      const t = d3.zoomTransform(svg.node()), [x, y] = t.apply([d.px, d.py]), r = drawnR(d) * t.k;
      tip.innerHTML = tipFor(d); tip.hidden = false;
      const w = tip.offsetWidth, h = tip.offsetHeight;
      let lx = x + r + 14, ly = y - 10;
      if (lx + w > W() - 8) lx = Math.max(8, x - r - 14 - w);
      ly = Math.max(8, Math.min(ly, H() - h - 8));
      tip.style.left = lx + 'px'; tip.style.top = ly + 'px';
    };
    const pointer = ev => { const [sx, sy] = d3.pointer(ev, svg.node()), t = d3.zoomTransform(svg.node()), [x, y] = t.invert([sx, sy]); return { sx, sy, x, y }; };
    let kbd = null, kbdAt = null;   // entity chosen with the arrow keys; cleared once the mouse really moves
    // the keyboard choice holds still (fixed in place, lens jumps straight to it) so stepping doesn't make it wobble
    const setKbd = d => {
      if (kbd && kbd !== d && !kbd.userPinned && !kbd.held && kbd !== snapped) { kbd.fx = null; kbd.fy = null; }
      kbd = d || null; kbdAt = null;
      if (kbd) { if (!kbd.userPinned && !kbd.held) { kbd.fx = kbd.x; kbd.fy = kbd.y; } kbd.vx = kbd.vy = 0; lens.x = kbd.x; lens.y = kbd.y; ensureVisible(kbd.x, kbd.y); }
    };
    svg.on('pointermove.atlas', ev => {
      const p = pointer(ev);
      if (kbd && (!kbdAt || Math.hypot(p.sx - kbdAt[0], p.sy - kbdAt[1]) > 6)) { if (kbdAt) setKbd(null); else kbdAt = [p.sx, p.sy]; }
      if (!mouse.inside) { lens.x = p.x; lens.y = p.y; }
      Object.assign(mouse, p, { inside: true });
      if (drag) {
        if (Math.hypot(p.sx - dragFrom[0], p.sy - dragFrom[1]) > 4) dragMoved = true;
        if (dragMoved) { drag.fx = p.x; drag.fy = p.y; A.sim.alphaTarget(0.25).restart(); }
        return;
      }
      if (cfg.pushOn) A.sim.alphaTarget(0.12).restart();
    }).on('pointerleave.atlas', () => {
      mouse.inside = false;
      if (!drag) { setSnap(null); A.sim.alphaTarget(0); }
    }).on('pointerdown.atlas', ev => {
      if (ev.button) return;
      const p = pointer(ev); Object.assign(mouse, p, { inside: true });
      const [n, g] = nearest();
      const grab = (cfg.snapOn && snapped) || (g < 4 ? n : null);
      if (!grab) return;
      drag = grab; dragFrom = [p.sx, p.sy]; dragMoved = false; A.grabbing = true; hide();
      svg.node().setPointerCapture(ev.pointerId);
    });
    const endDrag = () => {
      if (!drag) return;
      const d = drag; drag = null; A.grabbing = false;
      if (!dragMoved) { if (d.datalog) A.dataView(d.fam); else if (!d.singleton) flyTo(d); }
      else { d.userPinned = true; d.fx = d.x; d.fy = d.y; node.filter(x => x === d).classed('pinned', true); }
      A.sim.alphaTarget(mouse.inside && cfg.pushOn ? 0.12 : 0);
    };
    svg.on('pointerup.atlas', endDrag).on('pointercancel.atlas', endDrag);
    edge.on('mouseenter', (ev, d) => { if (focusId || drag) return; edgeHover = true; show(relTip(d.r), ev); edge.classed('faded', e => e !== d); node.classed('faded', n => n.id !== d.source.id && n.id !== d.target.id); })
      .on('mousemove', ev => { if (edgeHover) placeTip(ev); })
      .on('mouseleave', () => { if (!edgeHover) return; edgeHover = false; hide(); edge.classed('faded', false); node.classed('faded', false); applySystem(); });

    const gen = ++A.viewGen;
    const nbrs = d => { const s_ = new Set(); links.forEach(l => { if (l.source === d) s_.add(l.target); if (l.target === d) s_.add(l.source); }); return [...s_]; };
    A.arrowNav = {
      key(k) {
        let cur = kbd || (focusId && nodes.find(d => d.id === focusId));
        if (!cur) {   // start from the entity nearest the middle of the view
          const t = d3.zoomTransform(svg.node()), [cx, cy] = t.invert([W() / 2, H() / 2]);
          cur = nodes.reduce((a, d) => Math.hypot(d.x - cx, d.y - cy) < Math.hypot(a.x - cx, a.y - cy) ? d : a);
        } else {      // follow a relationship that way; with none, the nearest entity that way
          cur = pickToward(cur, DIRS[k], nbrs(cur)) || pickToward(cur, DIRS[k], nodes) || cur;
        }
        setKbd(cur);
      },
      enter() { if (kbd && !kbd.singleton) { const d = kbd; setKbd(null); if (d.datalog) A.dataView(d.fam); else flyTo(d); return true; } return false; },
      select(id) { setKbd(id && nodes.find(x => x.id === id)); },
      // hovering a system in the list: light up what it creates, changes or reads
      system(name) {
        if (!name) { highlight(focusId); return; }
        showRoles(rolesOf(name));
      },
    };
    const frame = () => {
      if (gen !== A.viewGen || st.view !== 'overview') return;
      updateSnap();
      const lead = kbd || snapped, tx = lead ? lead.x : mouse.x, ty = lead ? lead.y : mouse.y;
      const a = reduceMotion ? 1 : Math.min(1, cfg.lensFollow * (snapped ? 1.6 : 1));
      lens.x += (tx - lens.x) * a; lens.y += (ty - lens.y) * a;
      lens.s += (((mouse.inside || kbd) && cfg.lensMode !== 'off' ? 1 : 0) - lens.s) * (reduceMotion ? 1 : 0.12);
      project();
      // focus: the snapped node, or with snap off the node under the cursor
      let f = drag || kbd || (cfg.snapOn ? snapped : null);
      if (!f && !cfg.snapOn && mouse.inside) { const [n, g] = nearest(); if (g <= 0) f = n; }
      const fid = f ? f.id : null;
      if (fid !== focusId) {
        focusId = fid; edgeHover = false;
        highlight(fid);
        if (!fid) hide();
        previewPanel(f && !f.singleton && !f.datalog ? fid : null);
      }
      if (f && !drag) tipAtNode(f);
      // draw through the lens, magnified nodes on top
      edge.each(function (d) {
        const bend = 18 * (d.r._i - 1) + (d.r._i > 1 ? 10 : 6);
        const p = edgePath(d.source.px, d.source.py, d.target.px, d.target.py, drawnR(d.target) + 4, bend, drawnR(d.source) + 3);
        const g = d3.select(this);
        g.selectAll('path').attr('d', p.d);
        g.select('text').attr('x', p.lx).attr('y', p.ly - (d.r.via ? 10 : 3));
        if (d.r.via) g.select('.via').attr('transform', `translate(${p.lx},${p.ly})`);
      });
      dledge.select('path').attr('d', d => `M${d.source.px},${d.source.py}L${d.target.px},${d.target.py}`);
      node.attr('transform', d => `translate(${d.px},${d.py}) scale(${Math.min(3, Math.sqrt(d.pz || 1))})`);
      node.sort((p, q) => (p.pz || 1) - (q.pz || 1));
      if (f) {
        ring.attr('display', null).attr('cx', f.px).attr('cy', f.py).attr('r', drawnR(f) + 4 / kNow());
      } else ring.attr('display', 'none');
      requestAnimationFrame(frame);
    };
    project();
    requestAnimationFrame(frame);
    A.sim.alpha(0.02).restart();
    legend(['ownership', 'registry', 'directed', 'attachment', 'classifier'].concat(links.some(l => l.r.l.type === 'keyed') ? ['keyed'] : []), 'map');
    applySystem();
    // the details panel previews the focused entity; it falls back to the overview after a short grace
    // period, so sweeping across gaps between nodes doesn't flash the overview
    const basePanel = () => { if (st.system) systemPanel(st.system, role); else overviewPanel(); };
    let previewing = null, baseScroll = 0, revertT = 0;
    const previewPanel = id => {
      clearTimeout(revertT);
      if (id) { if (!previewing) baseScroll = panel.scrollTop; previewing = id; focusPanel(id); return; }
      if (!previewing) return;
      revertT = setTimeout(() => { if (gen !== A.viewGen || st.view !== 'overview' || focusId) return; previewing = null; basePanel(); panel.scrollTop = baseScroll; }, 350);
    };
    basePanel();
    try { history.replaceState(null, '', st.system ? '#sys.' + st.system : location.pathname); } catch (e) {}
    setTimeout(() => fit(0), 30);
  }

  Object.assign(A, { overviewView, radius });
}
