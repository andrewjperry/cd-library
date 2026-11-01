#!/usr/bin/env python3
"""CD Library helper for the Raspberry Pi. Standard library only; needs ffprobe (apt install ffmpeg).
Serves the page, remembers the music folder, and rebuilds the catalogue on request."""
import json, os, re, subprocess, threading, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, 'config.json')
LIBRARY = os.path.join(HERE, 'library.json')
AUDIO = ('.m4a', '.mp3', '.flac', '.ogg', '.wav')
DISC = re.compile(r'\s*\[Disc \d+\]$')
PORT = int(os.environ.get('CDLIB_PORT', '8080'))

def load_config():
    try:
        return json.load(open(CONFIG))
    except Exception:
        return {'music_dir': os.path.expanduser('~/Music')}

def save_config(c):
    json.dump(c, open(CONFIG, 'w'), indent=1)

state = {'running': False, 'done': 0, 'total': 0, 'finished': False, 'error': None, 'albums': 0, 'tracks': 0}
lock = threading.Lock()

def num(v):
    m = re.match(r'\d+', str(v or ''))
    return int(m.group()) if m else 0

def probe(path):
    try:
        out = subprocess.run(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', path],
                             capture_output=True, text=True, timeout=60)
        fmt = json.loads(out.stdout).get('format', {})
    except Exception:
        fmt = {}
    return {k.lower(): v for k, v in fmt.get('tags', {}).items()}, float(fmt.get('duration', 0) or 0)

def scan():
    try:
        root = load_config()['music_dir']
        files = []
        for artist in sorted(os.listdir(root)):
            ap = os.path.join(root, artist)
            if artist.startswith('.') or not os.path.isdir(ap): continue
            for album in sorted(os.listdir(ap)):
                bp = os.path.join(ap, album)
                if album.startswith('.') or not os.path.isdir(bp): continue
                for f in sorted(os.listdir(bp)):
                    if not f.startswith('.') and f.lower().endswith(AUDIO):
                        files.append((artist, album, f))
        with lock:
            state.update(total=len(files), done=0)
        groups, order = {}, []
        for artist, folder, f in files:
            tags, secs = probe(os.path.join(root, artist, folder, f))
            key = (artist, DISC.sub('', folder))
            if key not in groups:
                groups[key] = dict(artist=artist, album=DISC.sub('', tags.get('album') or folder), year='', tracks=[])
                order.append(key)
            g = groups[key]
            m = re.match(r'\d{4}', tags.get('date') or tags.get('year') or '')
            if m and not g['year']: g['year'] = m.group()
            g['tracks'].append(dict(d=num(tags.get('disc')) or 1, n=num(tags.get('track')),
                                    t=tags.get('title') or os.path.splitext(f)[0], s=round(secs, 1),
                                    p=f'{artist}/{folder}/{f}'))
            with lock:
                state['done'] += 1
        for g in groups.values():
            g['tracks'].sort(key=lambda t: (t['d'], t['n'], t['p']))
        artists = sorted({k[0] for k in order}, key=str.lower)
        lib = []
        for a in artists:
            lib += sorted([groups[k] for k in order if k[0] == a], key=lambda g: (num(g['year']) or 9999, g['album']))
        tmp = LIBRARY + '.tmp'
        json.dump(lib, open(tmp, 'w'), ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, LIBRARY)
        with lock:
            state.update(running=False, finished=True, error=None, albums=len(lib), tracks=sum(len(a['tracks']) for a in lib))
    except Exception as e:
        with lock:
            state.update(running=False, finished=False, error=str(e))

def start_scan():
    with lock:
        if state['running']: return
        state.update(running=True, finished=False, error=None, done=0, total=0)
    threading.Thread(target=scan, daemon=True).start()

def page():
    t = open(os.path.join(HERE, 'template.html'), encoding='utf-8').read()
    try:
        lib = open(LIBRARY, encoding='utf-8').read().replace('</', '<\\/')
    except Exception:
        lib = '[]'
    return t.replace('__LIB__', lib).encode('utf-8')

class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype='application/json'):
        if not isinstance(body, bytes): body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split('?')[0]
        if p in ('/', '/index.html'): self.send(200, page(), 'text/html')
        elif p == '/api/settings': self.send(200, load_config())
        elif p == '/api/rescan':
            with lock: self.send(200, dict(state))
        else: self.send(404, {'error': 'not found'})

    def do_POST(self):
        p = self.path.split('?')[0]
        n = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(n) if n else b''
        if p == '/api/settings':
            try:
                d = os.path.expanduser(json.loads(body)['music_dir'])
                if not os.path.isdir(d): raise ValueError('no such folder')
                save_config({'music_dir': d})
                self.send(200, {'music_dir': d})
            except Exception:
                self.send(400, {'error': 'bad folder'})
        elif p == '/api/rescan':
            start_scan()
            self.send(200, {'started': True})
        else: self.send(404, {'error': 'not found'})

    def log_message(self, *a): pass

if __name__ == '__main__':
    if not os.path.exists(LIBRARY): start_scan()
    print(f'CD Library on http://0.0.0.0:{PORT}  (music: {load_config()["music_dir"]})', flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
