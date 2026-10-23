"""Bounded, read-only 0.2.0 historical UI. No state/archive/source/model jobs."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import signal
import time
from urllib.parse import parse_qs, urlsplit

from . import connected_server as C

VERSION = '0.2.0'
ROOT = Path(__file__).resolve().parents[2]
PROTOTYPE = ROOT / 'prototype/departure-connected-2026-10-22'
BOOT = """document.addEventListener('DOMContentLoaded', () => {
  document.querySelector('[data-mode="replay"]')?.click();
}, {once: true});"""
NOTICE = '''<aside id="release-demo-banner" class="notice amber" role="note">
<p>離線公開示範：600 列、10 天已曝光的校準期歷史彙總；不是今日資料，也不是個人節時或準時保證。本版未攜帶任何真實封存 issue 或即時來源，策略仍未合格。</p>
<p>Offline public demonstration: 600 aggregate rows from 10 exposed calibration days. Not current data, individual time savings or an on-time guarantee. Real sealed issues and live sources are not bundled. No policy is qualified.</p>
</aside>'''


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n').encode()


class Application:
    def __init__(self, clock=time.time):
        # Non-None frozen_state prevents any old state.json read. Do not invoke
        # connected_server.main(), change canonical ROOT or fabricate publications.
        self.candidate = C.Application(report=ROOT / '_offline_no_runtime', clock=clock,
            frozen_state={'attempts': [], 'issues': {}, 'process': {'status': 'stopped'}})

    def dispatch(self, target):
        url = urlsplit(target)
        if url.scheme or url.netloc or url.fragment:
            return 400, 'text/plain', b'Only relative same-origin routes are accepted.'
        query = parse_qs(url.query, keep_blank_values=True)
        if url.path in C.STATIC:
            if url.query:
                if url.path not in ('/', '/index.html'):
                    return 404, 'text/plain', b'Route is not in the offline allowlist.'
                if set(query) != {'lang'} or query['lang'] not in (['zh'], ['en']):
                    return 400, 'text/plain', b'Only lang=zh or lang=en is accepted.'
            path = PROTOTYPE / C.STATIC[url.path]
            if not path.resolve().is_relative_to(PROTOTYPE.resolve()):
                return 404, 'text/plain', b'Asset is outside the fixed allowlist.'
            body = path.read_bytes()
            if path.suffix == '.html':
                page = body.decode().replace('<div class="page-shell">', '<div class="page-shell">' + NOTICE, 1)
                body = page.replace('</body>', '<script>' + BOOT + '</script></body>', 1).encode()
            kind = {'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript'}[path.suffix]
            return 200, kind, body
        if url.path not in ('/api/status', '/api/prospective'):
            return 404, 'text/plain', b'Route is not in the offline allowlist.'
        try:
            value = self.candidate.route(url.path, query)
            if url.path == '/api/status':
                value.update(delivery_mode='offline_release_demo', release_version=VERSION,
                    source_HTTP=0, model_forwards=0,
                    bundled_example={'type': 'exposed_calibration_aggregates', 'rows': 600, 'days': 10})
            return 200, 'application/json', encoded(value)
        except C.BadQuery:
            return 400, 'text/plain', b'Query is outside the fixed offline contract.'
        except (C.ApiUnavailable, OSError, ValueError, TypeError, KeyError):
            return 503, 'text/plain', b'Artifact unavailable; no substitute values are generated.'


class Handler(BaseHTTPRequestHandler):
    application = None

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def do_GET(self):
        try:
            status, kind, body = self.application.dispatch(self.path)
        except (OSError, ValueError, TypeError, KeyError):
            status, kind, body = 503, 'text/plain', b'Offline asset unavailable.'
        self.send_response(status)
        self.send_header('Content-Type', kind + '; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *ignored):
        pass


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration-seconds', type=int, default=3600)
    parser.add_argument('--port', type=int, choices=(8767,), default=8767)
    args = parser.parse_args(argv)
    if not 1 <= args.duration_seconds <= 3600:
        parser.error('duration must be 1..3600 seconds')
    return args


def main(argv=None):
    args = arguments(argv)
    Handler.application = Application()
    server = HTTPServer(('127.0.0.1', args.port), Handler)
    server.timeout = 1
    stopped = False
    deadline = time.monotonic() + args.duration_seconds

    def stop(*ignored):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f'0.2.0 offline historical demo: http://127.0.0.1:{args.port}/ '
          f'(stops within {args.duration_seconds}s; no source/model jobs)', flush=True)
    try:
        while not stopped and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
