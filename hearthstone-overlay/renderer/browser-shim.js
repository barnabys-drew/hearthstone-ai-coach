// Browser fallback for window.overlayAPI. In the Electron app, preload.js
// defines overlayAPI before page scripts run, so this shim is a no-op there.
// When the page is served by serve.py instead, this provides the same API
// over plain HTTP fetches.
if (!window.overlayAPI) {
  window.overlayAPI = {
    config: () => fetch('/config').then((r) => r.json()),
    readJson: (fileName) => fetch(`/data/${fileName}`, { cache: 'no-store' }).then((r) => {
      if (!r.ok) throw new Error(`${fileName}: HTTP ${r.status}`);
      return r.json();
    }),
    artPath: (cardId) => Promise.resolve(`/art/${encodeURIComponent(cardId)}.png`),
    setClickThrough: () => {},
    onState: (callback) => callback({ clickThrough: false, opacity: 1 }),
    onFileChanged: () => {}, // browser mode has no watcher; polling covers it
  };
  document.addEventListener('DOMContentLoaded', () => {
    const state = document.getElementById('overlay-state');
    if (state) state.textContent = 'browser mode';
  });
}
