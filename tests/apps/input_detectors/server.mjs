// Serves the input probe page and the motion-attestation collector it loads.
// Fixtures are mounted read-only at /fixtures; only plain file names resolve.
// Test-only and dependency-free: diagnostics are single JSON lines on stderr.
import { readFile } from 'node:fs/promises';
import http from 'node:http';
import { basename, extname, join, normalize } from 'node:path';

const PORT = 8099;
const FIXTURES_DIR = '/fixtures';
const MOTION_ATTESTATION_DIR = '/opt/motion-attestation';
const PROBE_PREFIX = '/probe/';
const MA_PREFIX = '/ma/';
const MA_SOURCE_DIR = 'src/';
const HEALTH_PATH = '/health';
const CONTENT_TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript' };
const DEFAULT_CONTENT_TYPE = 'application/octet-stream';

function logLine(level, msg, fields = {}) {
    process.stderr.write(`${JSON.stringify({ time: new Date().toISOString(), level, msg, ...fields })}\n`);
}

function resolve(url) {
    const path = url.split('?')[0];
    if (path.startsWith(PROBE_PREFIX)) {
        const name = path.slice(PROBE_PREFIX.length);
        return name && name === basename(name) ? join(FIXTURES_DIR, name) : null;
    }
    if (path.startsWith(MA_PREFIX)) {
        const rel = normalize(path.slice(MA_PREFIX.length));
        if (!rel.startsWith(MA_SOURCE_DIR) || rel.includes('..')) return null;
        return join(MOTION_ATTESTATION_DIR, rel);
    }
    return null;
}

const server = http.createServer(async (req, res) => {
    if (req.url === HEALTH_PATH) {
        res.writeHead(200).end('ok');
        return;
    }
    const file = resolve(req.url);
    if (!file) {
        res.writeHead(404).end();
        return;
    }
    try {
        const body = await readFile(file);
        res.writeHead(200, { 'content-type': CONTENT_TYPES[extname(file)] || DEFAULT_CONTENT_TYPE });
        res.end(body);
    } catch (err) {
        logLine('warning', 'fixture not served', { path: req.url, err: String(err) });
        res.writeHead(404).end();
    }
});

server.listen(PORT, () => logLine('info', 'input detector server listening', { port: PORT }));
