// Settings (saved in the browser) and the view state.
import { LAYERS, REL_NAME } from './util.js';

export function install(A) {
  // first-load defaults (2026-10-17): the stored pointers only, no passthrough edges; cursor effects all on (Acheron's call)
  const DEFAULTS = { layers: LAYERS.slice(), rel: Object.keys(REL_NAME).filter(t => t !== 'keyed'), edgeLabels: 'auto', names: true, viaEdges: false, spacing: 1, linkLen: 150, legend: true, legendCollapsed: false, hint: true, collapsed: false,
    lensMode: 'fisheye', lensStrength: 2.5, lensRadius: 170, lensFollow: 0.18, pushOn: true, pushStrength: 3, snapOn: true, snapDist: 26,
    dataLogs: false };   // data-log families on the overview map (Acheron: off by default)
  const KEY = 'atlas.settings.v3';   // bumped when the defaults change, so every browser starts from the new ones once
  const loadSettings = () => { try { return Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch (e) { return Object.assign({}, DEFAULTS); } };
  const cfg = loadSettings();
  const saveSettings = () => { try { localStorage.setItem(KEY, JSON.stringify(Object.assign(cfg, { layers: [...st.layers], rel: [...st.rel] }))); } catch (e) {} };
  const st = { layers: new Set(cfg.layers.filter(l => LAYERS.includes(l)).length ? cfg.layers : LAYERS), rel: new Set(cfg.rel), view: 'overview', sel: null };

  Object.assign(A, { DEFAULTS, cfg, saveSettings, st });
}
