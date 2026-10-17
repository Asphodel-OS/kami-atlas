// Kami Domain Atlas. Loads the generated data, then installs the modules in dependency order onto one shared
// context A: each module reads what earlier ones defined and adds its own. Mutable shared state lives on A too.
import { esc } from './util.js';
import { loadData, install as data } from './data.js';
import { install as state } from './state.js';
import { install as tips } from './tips.js';
import { install as canvas } from './canvas.js';
import { install as nav } from './nav.js';
import { install as panels } from './panels.js';
import { install as focus } from './focus.js';
import { install as map } from './map.js';
import { install as ui } from './ui.js';

const A = { panel: document.getElementById('panel'), sim: null, arrowNav: null, fitTarget: null, grabbing: false, viewGen: 0, navRestoring: false, curNodes: null };
try {
  Object.assign(A, await loadData());
} catch (e) {
  A.panel.innerHTML = '<h2>Data did not load</h2><p class="muted">' + esc(e.message) + '. Rerun kami/kami-atlas/generate.py and republish.</p>';
  throw e;
}
for (const step of [state, data, tips, canvas, nav, panels, focus, map, ui]) step(A);
