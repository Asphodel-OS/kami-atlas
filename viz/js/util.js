// Pure helpers and the name tables the other modules share.

export const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const arr = v => v == null ? [] : Array.isArray(v) ? v : [v];
export const LAYERS = ['runtime', 'hybrid', 'registry'];
export const LAYER_NAME = { runtime: 'Runtime', hybrid: 'Hybrid', registry: 'Registry' };
export const REL_NAME = { ownership: 'Ownership', registry: 'Registry reference', directed: 'Directed', attachment: 'Attachment', classifier: 'Classifier', keyed: 'Keyed' };
export const REL_GLOSS = { ownership: 'belongs to', registry: 'instance of', directed: 'points at', attachment: 'is attached to',
    classifier: 'is classified by', keyed: 'id hashed from its key' };
export const REL_COMP = { ownership: 'IDOwns*, IdHolder', registry: 'Index*', directed: 'IdSource, IdTarget', attachment: 'IDAnchor', classifier: 'IDType', keyed: 'keccak256(ns, keys…)' };
export const LAYER_GLOSS = { runtime: 'made by playing', hybrid: 'registry entry + player instance', registry: 'made by admins' };
export const list = (xs, max = 8) => xs.length ? xs.slice(0, max).map(x => '<code>' + esc(x) + '</code>').join(', ') + (xs.length > max ? ` <span class="muted">+${xs.length - max} more</span>` : '') : '<span class="muted">none</span>';
export const trunc = (s, n) => s.length > n ? s.slice(0, n - 1) + '…' : s;
