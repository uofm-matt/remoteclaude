// rc_download.js — DOM-free helpers for the files page's download(). The page wraps
// fetch/Blob/link around these; the load-bearing decision is the size cap, because buffering
// a large file into a Blob OOMs a phone browser tab. Kept standalone so `node --test` can pin
// it (the in-page code was covered only by node --check + substring presence), mirroring
// rc_upload.js. hum() lives here too so the page and this test share one formatter.
var RC_DOWNLOAD_CAP = 256 * 1048576; // bytes; above this, hand off to the browser's native download

function hum(n) {
  if (n < 1024) return n + ' B';
  var u = ['KB', 'MB', 'GB', 'TB'], i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < 3);
  return n.toFixed(1) + ' ' + u[i];
}

// 'native' when the file is too big to buffer in memory, else 'buffer' (fetch + progress).
function downloadMode(total, cap) {
  return total > (cap === undefined ? RC_DOWNLOAD_CAP : cap) ? 'native' : 'buffer';
}

function progressText(name, got, total) {
  return '⬇ ' + name + ' ' + (total ? Math.floor((got * 100) / total) + '% ' : '') + hum(got) + (total ? '/' + hum(total) : '');
}

if (typeof module !== 'undefined') module.exports = { hum, downloadMode, progressText, RC_DOWNLOAD_CAP };
