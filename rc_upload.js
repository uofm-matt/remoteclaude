/* Upload-resume policy for the rc-share files page — pure and DOM-free so node can
 * test it (tests/js/rc_upload.test.cjs). Mirrors android UploadLogic.kt, the other
 * client of the same server contract.
 *
 * The rule that matters: a failed probe means UNKNOWN, never "0 bytes on the server".
 * The server truncates the held partial to the PUT's offset, so sending from 0 after
 * a failed probe destroys the resume data. The policy never sends without a
 * successful probe, and after a drop it advances only to a fresh server-reported
 * offset — never a guess.
 *
 * resumeUpload(total, probe, send, opts)
 *   total  file size in bytes; <= 0 is refused up front (the server 400s it by design)
 *   probe  async () -> bytes the server already holds, or -1 for unknown (failed HEAD)
 *   send   async (off, end) -> true if the slice [off, end) was stored
 *   opts   {chunk, minChunk, maxChunk, maxStalls, onRetry} — all optional
 * resolves {ok:true} or {ok:false, reason:'empty'|'probe'|'stalled'}
 */
async function resumeUpload(total, probe, send, opts) {
  var o = opts || {};
  var minChunk = o.minChunk || (1 << 20);
  var maxChunk = o.maxChunk || (256 << 20);
  var maxStalls = o.maxStalls || 8;
  var onRetry = o.onRetry || async function () {};
  var chunk = Math.min(Math.max(o.chunk || (16 << 20), minChunk), maxChunk);

  if (total <= 0) return { ok: false, reason: 'empty' };

  // Establish where the server is before the first byte moves — never send blind.
  var stalls = 0;
  var off = await probe();
  while (off < 0) {
    if (++stalls >= maxStalls) return { ok: false, reason: 'probe' };
    await onRetry();
    off = await probe();
  }

  var lastHave = off;
  stalls = 0;
  while (off < total) {
    var end = Math.min(off + chunk, total);
    if (await send(off, end)) {
      off = end;
      lastHave = off;
      stalls = 0;
      chunk = Math.min(chunk * 2, maxChunk);  // link is good: grow toward maxChunk
      continue;
    }
    chunk = Math.max(Math.floor(chunk / 2), minChunk);  // dropped: back off
    var have = await probe();
    if (have >= 0) {
      // The server is authoritative about what survived the drop — even below off.
      stalls = have > lastHave ? 0 : stalls + 1;
      lastHave = have;
      off = have;
    } else {
      stalls++;  // unknown: keep the last trusted offset, never treat as 0
    }
    if (off >= total) break;
    if (stalls >= maxStalls) return { ok: false, reason: 'stalled' };
    await onRetry();
  }
  return { ok: true };
}

if (typeof module !== 'undefined') module.exports = { resumeUpload };
