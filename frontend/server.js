const http = require('http');
const fs = require('fs');
const path = require('path');
const { Readable } = require('stream');

const PORT = 3000;
const PUBLIC_ROOT = path.join(__dirname, 'public');
const API_BASE = 'http://api:8000';

const MIME = {
  '.html': 'text/html',
  '.css': 'text/css',
  '.js': 'text/javascript',
  '.json': 'application/json',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
};

function proxyApi(req, res) {
  const url = API_BASE + req.url;
  const headers = { ...req.headers };
  delete headers.host;
  delete headers.connection;
  const controller = new AbortController();
  let clientClosed = false;
  res.on('close', () => {
    clientClosed = true;
    controller.abort();
  });
  fetch(url, {
    method: req.method,
    headers,
    body: ['GET', 'HEAD'].includes(req.method) ? undefined : req,
    duplex: 'half',
    signal: controller.signal,
  }).then((upstream) => {
    if (clientClosed) {
      upstream.body && upstream.body.cancel();
      return;
    }
    res.writeHead(upstream.status, {
      'content-type': upstream.headers.get('content-type') || 'application/octet-stream',
      'cache-control': 'no-cache',
      ...(upstream.headers.get('content-disposition')
        ? { 'content-disposition': upstream.headers.get('content-disposition') }
        : {}),
    });
    if (upstream.body) {
      const stream = Readable.fromWeb(upstream.body);
      res.on('close', () => stream.destroy());
      stream.pipe(res);
    } else {
      res.end();
    }
  }).catch((err) => {
    if (clientClosed) return;
    res.writeHead(502, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: 'api unreachable', detail: String(err) }));
  });
}

function serveStatic(req, res) {
  const urlPath = decodeURIComponent(req.url.split('?')[0]);
  const rel = urlPath === '/' ? 'index.html' : urlPath;
  const filePath = path.normalize(path.join(PUBLIC_ROOT, rel));
  if (!filePath.startsWith(PUBLIC_ROOT)) {
    res.writeHead(403, { 'content-type': 'text/plain' });
    res.end('forbidden');
    return;
  }
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404, { 'content-type': 'text/plain' });
      res.end('not found');
      return;
    }
    res.writeHead(200, {
      'content-type': MIME[path.extname(filePath)] || 'application/octet-stream',
    });
    res.end(data);
  });
}

http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    proxyApi(req, res);
  } else {
    serveStatic(req, res);
  }
}).listen(PORT, '0.0.0.0', () => {
  console.log(`frontend listening on 0.0.0.0:${PORT}`);
});
