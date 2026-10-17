// Settings (saved in the browser) and the view state.
import { LAYERS, REL_NAME } from './util.js';

export function install(A) {
  const DEFAULTS = { layers: LAYERS.slice(), rel: Object.keys(REL_NAME), edgeLabels: 'auto', names: true, viaEdges: true, spacing: 1, linkLen: 150, legend: true, legendCollapsed: false, hint: true, collapsed: false,
    lensMode: 'fisheye', lensStrength: 2.5, lensRadius: 170, lensFollow: 0.18, pushOn: true, pushStrength: 3, snapOn: true, snapDist: 26 };
  const loadSettings = () => { try { return Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem('atlas.settings.v1') || '{}')); } catch (e) { return Object.assign({}, DEFAULTS); } };
  const cfg = loadSettings();
  const saveSettings = () => { try { localStorage.setItem('atlas.settings.v1', JSON.stringify(Object.assign(cfg, { layers: [...st.layers], rel: [...st.rel] }))); } catch (e) {} };
  const st = { layers: new Set(cfg.layers.filter(l => LAYERS.includes(l)).length ? cfg.layers : LAYERS), rel: new Set(cfg.rel), view: 'overview', sel: null };

  Object.assign(A, { DEFAULTS, cfg, saveSettings, st });
}
