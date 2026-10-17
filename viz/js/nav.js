// Exploration history for the Back button: view, selection and camera.

export function install(A) {
  const { st, svg, zoom } = A;
  // exploration history for the panel's Back button: view, selection and camera
  const navStack = [];
  // the overview's layout as it was left (including pins), so Back returns to the same picture
  const navSave = () => st.view === 'overview' && A.curNodes && navStack.length && (navStack[navStack.length - 1].pos = new Map(A.curNodes.map(d => [d.id, { x: d.x, y: d.y, pin: !!d.userPinned, held: !!d.held }])));
  const navKey = () => st.view === 'focus' ? 'focus:' + st.sel : st.view ? 'overview:' + (st.system || '') : null;
  function navPush(next) {
    const cur = navKey();
    if (A.navRestoring || !cur || cur === next) return;
    navStack.push({ view: st.view, sel: st.sel, sub: st.sub, system: st.system, t: d3.zoomTransform(svg.node()) });
    navSave();
    if (navStack.length > 60) navStack.shift();
    document.getElementById('navback').disabled = false;
  }
  function navBack() {
    const s_ = navStack.pop(); if (!s_) return;
    document.getElementById('navback').disabled = !navStack.length;
    const fromFocus = st.view === 'focus';
    A.navRestoring = true;
    try { if (s_.view === 'focus') A.focusView(s_.sel, s_.sub); else A.overviewView(s_.system, s_.pos); } finally { A.navRestoring = false; }
    // leaving a drill-in for the map refits it; otherwise restore the camera as it was
    if (!(fromFocus && s_.view === 'overview')) setTimeout(() => svg.interrupt().call(zoom.transform, s_.t), 60);  // after the view's own fit
  }

  Object.assign(A, { navPush, navBack });
}
