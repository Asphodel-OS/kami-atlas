#!/usr/bin/env python3
"""Local dev server for the Kami Domain Atlas, with live reload.

    python3 dev.py            # http://127.0.0.1:8765/index.html
    python3 dev.py --port 9000

Serves viz/. The page reloads itself when anything in viz/ changes (the URL hash, e.g. #KAMI, is kept).
When notes/ or generate.py change, it reruns `generate.py --no-build` first (about a second, reusing the
compile cache), so viz/data/ is current before the reload. Contract changes still need a full
`python3 generate.py`. Standard library only; the reload hook is injected into responses here, never
written into viz/index.html.
"""
import argparse, http.server, os, socketserver, subprocess, sys, threading, time

SOURCE_ARGS = ['--ref', 'main']   # passed through to generate.py; set from the command line in main()

HERE = os.path.dirname(os.path.abspath(__file__))
VIZ = os.path.join(HERE, 'viz')
HOOK = b"<script>new EventSource('/__reload').onmessage = () => location.reload();</script>"

version = 0
cond = threading.Condition()


def snapshot(paths):
    out = {}
    for p in paths:
        for root, _, files in os.walk(p) if os.path.isdir(p) else [(os.path.dirname(p), [], [os.path.basename(p)])]:
            for f in files:
                fp = os.path.join(root, f)
                try: out[fp] = os.stat(fp).st_mtime_ns
                except OSError: pass
    return out


def bump(reason):
    global version
    with cond:
        version += 1
        cond.notify_all()
    print('%s  reload: %s' % (time.strftime('%H:%M:%S'), reason), flush=True)


def watch():
    sources = [os.path.join(HERE, 'notes'), os.path.join(HERE, 'generate.py')]
    viz = snapshot([VIZ]); src = snapshot(sources)
    while True:
        time.sleep(0.4)
        s2 = snapshot(sources)
        if s2 != src:
            src = s2
            print('%s  notes or generator changed: regenerating…' % time.strftime('%H:%M:%S'), flush=True)
            r = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'generate.py'), '--no-build', *SOURCE_ARGS], capture_output=True, text=True, cwd=HERE)
            print((r.stdout or '').strip().splitlines()[0] if r.returncode == 0 else 'generate.py failed:\n' + r.stderr[-1500:], flush=True)
        v2 = snapshot([VIZ])
        if v2 != viz:
            changed = sorted(os.path.relpath(p, HERE) for p in set(v2) | set(viz) if v2.get(p) != viz.get(p))
            viz = v2
            bump(', '.join(changed[:3]) + (' …' if len(changed) > 3 else ''))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=VIZ, **k)

    def log_message(self, *a):  # quiet
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def do_GET(self):
        path = self.path.split('?')[0].split('#')[0]
        if path == '/__reload':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            with cond: seen = version
            try:
                while True:
                    with cond:
                        cond.wait(timeout=15)
                        changed = version != seen
                    self.wfile.write(b'data: reload\n\n' if changed else b': ping\n\n')
                    self.wfile.flush()
                    if changed: return
            except (BrokenPipeError, ConnectionResetError):
                return
        if path in ('/', '/index.html'):
            body = open(os.path.join(VIZ, 'index.html'), 'rb').read() + HOOK
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    ap = argparse.ArgumentParser(description='Kami Domain Atlas dev server with live reload')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--ref', default='main', help='branch or tag of kamigotchi to read (default main; same as generate.py)')
    ap.add_argument('--local', action='store_true', help='read the kamigotchi checkout as is instead of origin/<ref>')
    a = ap.parse_args()
    SOURCE_ARGS[:] = ['--local'] if a.local else ['--ref', a.ref]
    threading.Thread(target=watch, daemon=True).start()
    srv = Server(('127.0.0.1', a.port), Handler)
    print('Kami Domain Atlas: http://127.0.0.1:%d/index.html  (live reload on viz/, regenerate on notes/ and generate.py)' % a.port, flush=True)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass


if __name__ == '__main__':
    main()
