// Pins the browser download policy (rc_download.js) — the size cap especially, which
// nothing in the page asserted: deleting the threshold used to keep every test green.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { hum, downloadMode, progressText, RC_DOWNLOAD_CAP } = require(path.join(__dirname, '..', '..', 'rc_download.js'));

test('downloadMode buffers up to the cap and goes native one byte over', () => {
  assert.equal(downloadMode(RC_DOWNLOAD_CAP), 'buffer'); // exactly at cap: still buffered
  assert.equal(downloadMode(RC_DOWNLOAD_CAP + 1), 'native'); // one byte over: hand to the browser
  assert.equal(downloadMode(0), 'buffer');
});

test('downloadMode honours an explicit cap', () => {
  assert.equal(downloadMode(100, 50), 'native');
  assert.equal(downloadMode(50, 50), 'buffer');
});

test('hum formats byte sizes', () => {
  assert.equal(hum(512), '512 B');
  assert.equal(hum(1536), '1.5 KB');
  assert.equal(hum(5 * 1048576), '5.0 MB');
});

test('progressText shows percent and denominator only with a known total', () => {
  assert.equal(progressText('a.bin', 512, 1024), '⬇ a.bin 50% 512 B/1.0 KB');
  assert.equal(progressText('a.bin', 512, 0), '⬇ a.bin 512 B'); // unknown total: no percent
});
