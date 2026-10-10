// Scores a probe harvest (JSON on stdin) with motion-attestation and Gaitcha.
// Prints {"motion_attestation": {...}, "gaitcha": [per-click results]} on
// stdout; that is the command's output, not a diagnostic.
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { analyze, classifyScore } from '/opt/motion-attestation/src/analyzer.js';

// Gaitcha's EventLogger keeps one move per 50 ms and the last 30 moves.
const GAITCHA_MOVE_THROTTLE_MS = 50;
const GAITCHA_MAX_MOVES = 30;
const GAITCHA_SCRIPT = '/app/score_gaitcha.php';
const EVENT_MOUSEMOVE = 'mousemove';
const EVENT_POINTERMOVE = 'pointermove';
const EVENT_CLICK = 'click';
const STDIN_FD = 0;

function throttledMoves(segment) {
    const firstT = segment[0].t;
    const moves = [];
    let last = -Infinity;
    for (const m of segment) {
        if (m.t - last < GAITCHA_MOVE_THROTTLE_MS) continue;
        moves.push({ t: Math.round(m.t - firstT), x: Math.round(m.x), y: Math.round(m.y) });
        last = m.t;
    }
    return moves.slice(-GAITCHA_MAX_MOVES);
}

// One payload per click, built from the trusted moves since the previous
// click, the way Gaitcha's browser EventLogger would have recorded it.
function gaitchaPayloads(events) {
    const payloads = [];
    let segment = [];
    let coalesced = [];
    for (const e of events) {
        if (!e.trusted) continue;
        if (e.type === EVENT_MOUSEMOVE) {
            segment.push(e);
            continue;
        }
        if (e.type === EVENT_POINTERMOVE && typeof e.co === 'number') {
            coalesced.push(e.co);
            continue;
        }
        if (e.type !== EVENT_CLICK || !e.rect || !segment.length) continue;

        const dt = Math.round(e.t - segment[0].t);
        const cx = e.rect.l + e.rect.w / 2;
        const cy = e.rect.t + e.rect.h / 2;
        const payload = {
            moves: throttledMoves(segment),
            check: {
                type: EVENT_CLICK,
                t: dt,
                x: Math.round(e.x),
                y: Math.round(e.y),
                offset: { x: Math.round(e.x - cx), y: Math.round(e.y - cy) },
                screenDx: Math.round(e.sx - e.x),
                screenDy: Math.round(e.sy - e.y),
            },
            tabs: [],
            dt,
            moveCount: segment.length,
        };
        if (coalesced.length) {
            const avg = coalesced.reduce((a, b) => a + b, 0) / coalesced.length;
            payload.coalescedAvg = Math.round(avg * 100) / 100;
        }
        payloads.push(payload);
        segment = [];
        coalesced = [];
    }
    return payloads;
}

function scoreGaitcha(payloads) {
    if (!payloads.length) return [];
    const run = spawnSync('php', [GAITCHA_SCRIPT], { input: JSON.stringify(payloads), encoding: 'utf8' });
    if (run.error) throw new Error('run gaitcha scorer', { cause: run.error });
    if (run.status !== 0) throw new Error(`gaitcha scorer exited ${run.status}: ${run.stderr}`);
    return JSON.parse(run.stdout);
}

function scoreMotionAttestation(data) {
    if (!data) return null;
    const verdict = analyze(data);
    return {
        score: verdict.score,
        verdict: classifyScore(verdict.score),
        reasons: verdict.reasons,
        categories: verdict.categories,
    };
}

const harvest = JSON.parse(readFileSync(STDIN_FD, 'utf8'));
const result = {
    motion_attestation: scoreMotionAttestation(harvest.ma),
    gaitcha: scoreGaitcha(gaitchaPayloads(harvest.events || [])),
};
process.stdout.write(JSON.stringify(result));
