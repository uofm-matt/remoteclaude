'use strict';
// Pins the browser upload-resume policy (rc_upload.js), porting the Kotlin
// UploadLogicTest cases: a failed probe is UNKNOWN and must never turn into a
// PUT from offset 0 (the server truncates the held partial to the sent offset).
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { resumeUpload } = require(path.join(__dirname, '..', '..', 'rc_upload.js'));

test('failed initial probe never sends, fails after maxStalls unknowns', async () => {
  const sent = [];
  let probes = 0;
  const r = await resumeUpload(
    1000,
    async () => { probes++; return -1; },
    async (off, end) => { sent.push([off, end]); return true; },
    { maxStalls: 4 },
  );
  assert.equal(r.ok, false);
  assert.equal(r.reason, 'probe');
  assert.deepEqual(sent, []); // never sent blind — no offset-0 truncation
  assert.equal(probes, 4);    // terminated at the cap, not an infinite spin
});

test('transient probe failures recover once the server answers', async () => {
  const probes = [-1, -1, 0];
  const sent = [];
  let retries = 0;
  const r = await resumeUpload(
    1000,
    async () => probes.shift(),
    async (off, end) => { sent.push([off, end]); return true; },
    { onRetry: async () => { retries++; } },
  );
  assert.deepEqual(r, { ok: true });
  assert.deepEqual(sent, [[0, 1000]]); // sent once, from the recovered offset
  assert.equal(retries, 2);
});

test('resumes from the reported offset, not zero', async () => {
  const sent = [];
  const r = await resumeUpload(
    1000,
    async () => 700,
    async (off, end) => { sent.push([off, end]); return true; },
  );
  assert.deepEqual(r, { ok: true });
  assert.deepEqual(sent, [[700, 1000]]);
});

test('every drop resumes from the server-reported offset, never re-zero', async () => {
  const haves = [0, 400, 800, 1000];
  const sent = [];
  const r = await resumeUpload(
    1000,
    async () => haves.shift(),
    async (off) => { sent.push(off); return false; }, // every PUT "drops"
  );
  assert.deepEqual(r, { ok: true });       // final probe reports the full size
  assert.deepEqual(sent, [0, 400, 800]);   // each resume from the reported offset
});

test('mid-transfer drop resumes from the server-reported have', async () => {
  const probes = [0, 300]; // the failed PUT still landed 100 bytes server-side
  const results = [true, false, true];
  const sent = [];
  const r = await resumeUpload(
    400,
    async () => probes.shift(),
    async (off, end) => { sent.push([off, end]); return results.shift(); },
    { chunk: 200, minChunk: 50, maxChunk: 200 },
  );
  assert.deepEqual(r, { ok: true });
  assert.deepEqual(sent, [[0, 200], [200, 400], [300, 400]]);
});

test('mid-transfer probe failure keeps the last trusted offset', async () => {
  let first = true;
  const results = [true, false, true, true];
  const sent = [];
  const r = await resumeUpload(
    300,
    async () => { if (first) { first = false; return 0; } return -1; },
    async (off, end) => { sent.push([off, end]); return results.shift(); },
    { chunk: 100, minChunk: 100, maxChunk: 100 },
  );
  assert.deepEqual(r, { ok: true });
  // after the drop the probe fails: retry from the confirmed 100, never from 0
  assert.deepEqual(sent, [[0, 100], [100, 200], [100, 200], [200, 300]]);
});

test('mid-transfer unknown probes terminate at the stall cap', async () => {
  // HEAD dies after the start AND PUTs keep failing: the unknown-probe branch must
  // count stalls too. Deleting its stalls++ makes this loop probe->send->retry
  // forever — onRetry throws before a hang can escape into the runner.
  let first = true;
  let retries = 0;
  const sent = [];
  const r = await resumeUpload(
    300,
    async () => { if (first) { first = false; return 0; } return -1; },
    async (off, end) => { sent.push([off, end]); return false; },
    { chunk: 100, minChunk: 100, maxChunk: 100, maxStalls: 3,
      onRetry: async () => { if (++retries > 10) throw new Error('spinning: unknown-probe stalls not counted'); } },
  );
  assert.deepEqual(r, { ok: false, reason: 'stalled' });
  assert.ok(sent.every(([off]) => off === 0));  // never a guessed offset while probes fail
});

test('gives up after consecutive no-progress rounds', async () => {
  let probes = 0;
  const r = await resumeUpload(
    1000,
    async () => { probes++; return 100; },
    async () => false,
    { maxStalls: 5 },
  );
  assert.equal(r.ok, false);
  assert.equal(r.reason, 'stalled');
  assert.ok(probes <= 8, `terminated, not an infinite spin: probes=${probes}`);
});

test('success finalizes; chunk grows on success toward maxChunk', async () => {
  const sent = [];
  const r = await resumeUpload(
    1000,
    async () => 0,
    async (off, end) => { sent.push([off, end]); return true; },
    { chunk: 100, minChunk: 50, maxChunk: 400 },
  );
  assert.deepEqual(r, { ok: true });
  assert.deepEqual(sent, [[0, 100], [100, 300], [300, 700], [700, 1000]]);
});

test('chunk halves on drop toward minChunk, regrows after progress', async () => {
  const sent = [];
  let failsLeft = 3;
  const r = await resumeUpload(
    800,
    async () => 0, // probed only during the offset-0 drops
    async (off, end) => {
      sent.push(end - off);
      if (failsLeft > 0) { failsLeft--; return false; }
      return true;
    },
    { chunk: 400, minChunk: 100, maxChunk: 400 },
  );
  assert.deepEqual(r, { ok: true });
  assert.deepEqual(sent, [400, 200, 100, 100, 200, 400, 100]);
});

test('already-complete file needs no send', async () => {
  const sent = [];
  const r = await resumeUpload(
    500,
    async () => 500,
    async (off, end) => { sent.push([off, end]); return true; },
  );
  assert.deepEqual(r, { ok: true });
  assert.deepEqual(sent, []);
});

test('zero-byte file is refused without touching the network', async () => {
  let calls = 0;
  const r = await resumeUpload(
    0,
    async () => { calls++; return 0; },
    async () => { calls++; return true; },
  );
  assert.deepEqual(r, { ok: false, reason: 'empty' }); // server 400s total<=0
  assert.equal(calls, 0);
});
