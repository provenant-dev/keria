#!/usr/bin/env node
// Summarize the per-op latency CSV into p50/p90/p99 tables, grouped by phase + op.
// Usage: node analyze.mjs [profiles/latency/latency.csv]
import { readFileSync } from 'node:fs';

const path = process.argv[2] ?? new URL('./profiles/latency/latency.csv', import.meta.url).pathname;

let text;
try {
    text = readFileSync(path, 'utf8');
} catch {
    console.error(`no latency CSV at ${path}`);
    process.exit(1);
}

const lines = text.trim().split('\n');
const header = lines.shift().split(',');
const col = Object.fromEntries(header.map((h, i) => [h, i]));

const groups = new Map();
for (const line of lines) {
    if (!line.trim()) continue;
    const f = line.split(',');
    const key = `${f[col.workload]}|${f[col.phase]}|${f[col.op]}`;
    (groups.get(key) ?? groups.set(key, []).get(key)).push(parseFloat(f[col.ms]));
}

function pct(arr, p) {
    const s = [...arr].sort((a, b) => a - b);
    return s[Math.min(Math.floor(s.length * p), s.length - 1)];
}

const rows = [...groups.entries()].map(([key, ms]) => {
    const [workload, phase, op] = key.split('|');
    return {
        workload,
        phase,
        op,
        n: ms.length,
        p50: pct(ms, 0.5),
        p90: pct(ms, 0.9),
        p99: pct(ms, 0.99),
        max: Math.max(...ms),
        total_s: ms.reduce((a, b) => a + b, 0) / 1000,
    };
});

rows.sort((a, b) => a.phase.localeCompare(b.phase) || a.op.localeCompare(b.op));

const pad = (s, w) => String(s).padEnd(w);
const num = (v) => (v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${v.toFixed(0)}ms`);
console.log(
    pad('phase', 34) + pad('op', 16) + pad('n', 5) +
    pad('p50', 9) + pad('p90', 9) + pad('p99', 9) + pad('max', 9) + 'total'
);
console.log('-'.repeat(104));
for (const r of rows) {
    console.log(
        pad(r.phase, 34) + pad(r.op, 16) + pad(r.n, 5) +
        pad(num(r.p50), 9) + pad(num(r.p90), 9) + pad(num(r.p99), 9) +
        pad(num(r.max), 9) + `${r.total_s.toFixed(1)}s`
    );
}

// Scaling hint: for resolve-verify, print latency vs the N embedded in the phase.
const verify = rows
    .filter((r) => r.op === 'resolve-verify')
    .map((r) => ({ phase: r.phase, p90: r.p90 }))
    .sort((a, b) => a.phase.localeCompare(b.phase));
if (verify.length) {
    console.log('\nresolve-verify p90 by phase (scaling curve):');
    for (const v of verify) console.log(`  ${pad(v.phase, 40)} ${num(v.p90)}`);
}
