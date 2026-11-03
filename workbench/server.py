"""Loopback-only local UI/API. No uploads, remote scripts or cloud account needed."""
import argparse
import json
import mimetypes
import subprocess
import sys
import threading
import webbrowser
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap
from service import Service


def choose(kind):
    if kind not in ('psd', 'folder', 'image'):
        raise ValueError('选择类型无效')
    process = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).with_name('choose.py')), kind],
                             capture_output=True, encoding='utf-8')
    if process.returncode:
        raise ValueError('文件选择窗口未能打开：' + process.stderr[-500:])
    return {'paths': json.loads(process.stdout)}


def create_server(service, port=0):
    web = Path(__file__).parent / 'web'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def trusted(self):
            host = self.headers.get('Host', '')
            valid = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            origin = self.headers.get('Origin')
            return host in valid and (origin is None or origin == 'http://' + host)

        def reply(self, value, status=200):
            data = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.trusted():
                return self.reply({'error': '仅允许本机工作台访问'}, 403)
            route = unquote(urlsplit(self.path).path)
            if route == '/api/state':
                return self.reply(service.state())
            if route.startswith('/media/'):
                root = service.root
                relative = route[len('/media/'):]
                if relative.split('/')[0] not in ('thumbs', 'previews', 'artworks'):
                    return self.reply({'error': '文件不存在'}, 404)
            else:
                root = web
                relative = 'index.html' if route == '/' else route.lstrip('/')
            target = (root / relative).resolve()
            if root.resolve() not in target.parents or not target.is_file():
                return self.reply({'error': '文件不存在'}, 404)
            content = target.read_bytes()
            mime = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
            self.send_response(200)
            self.send_header('Content-Type', mime + ('; charset=utf-8' if mime.startswith('text/') or mime.endswith('javascript') else ''))
            self.send_header('Content-Length', str(len(content)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            if not self.trusted() or self.headers.get('X-Workbench') != '1':
                return self.reply({'error': '仅允许从工作台页面操作'}, 403)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2 * 1024**2:
                    raise ValueError('请求大小无效')
                data = json.loads(self.rfile.read(length))
                route = urlsplit(self.path).path
                if route == '/api/choose':
                    result = choose(data['kind'])
                elif route == '/api/scan':
                    result = service.scan(data['paths'])
                elif route == '/api/template':
                    result = service.configure(data['id'], data['scenes'])
                elif route == '/api/preview':
                    result = service.preview(data['id'], data['source'], data['settings'])
                elif route == '/api/batch':
                    result = service.batch(data['ids'], data['input'], data['output'], data['settings'], data.get('limit', 0))
                elif route == '/api/control':
                    result = service.command(data['action'])
                elif route == '/api/resume':
                    result = service.resume(data['path'])
                elif route == '/api/cache':
                    result = service.cache_command(data['action'], data.get('limit_bytes'))
                elif route == '/api/remove-template':
                    result = service.remove(data['id'])
                elif route == '/api/artworks':
                    result = service.artworks(data['folder'], data.get('offset', 0), data.get('limit', 40))
                elif route == '/api/preferences':
                    result = service.save_preferences(data)
                elif route == '/api/open-output':
                    result = service.open_output(data['path'])
                else:
                    return self.reply({'error': '接口不存在'}, 404)
                self.reply(result)
            except (ValueError, KeyError, OSError, TypeError) as exc:
                self.reply({'error': str(exc)}, 400)
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--data-root', type=Path, default=bootstrap.ROOT / 'data')
    parser.add_argument('--desktop', action='store_true')
    args = parser.parse_args()
    app = Service(args.data_root)
    from parallel_batch import batch_lock
    instance = batch_lock(app.root)
    try:
        instance.__enter__()
    except ValueError:
        previous = json.loads((app.root / 'server.json').read_text(encoding='utf-8'))['url']
        print(previous, flush=True)
        if not args.no_open:
            webbrowser.open(previous)
        raise SystemExit(0)
    server = None
    try:
        server = create_server(app, args.port)
        url = f'http://127.0.0.1:{server.server_port}'
        (app.root / 'server.json').write_text(json.dumps({'url': url}), encoding='utf-8')
        print(url, flush=True)
        if args.desktop:
            def desktop_lifecycle():
                # The owning desktop process keeps stdin open; EOF also handles crashes.
                sys.stdin.readline()
                if app.control:
                    app.control.stop.set()
                while app.task['status'] in ('running', 'paused', 'stopping'):
                    time.sleep(.1)
                server.shutdown()
            threading.Thread(target=desktop_lifecycle, daemon=True, name='desktop-lifecycle').start()
        if not args.no_open:
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        instance.__exit__(None, None, None)
