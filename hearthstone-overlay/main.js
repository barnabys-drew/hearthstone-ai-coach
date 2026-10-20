const { app, BrowserWindow, globalShortcut, ipcMain, net } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { mergeConfig, CHOICES_MODES, nextChoicesMode } = require('./renderer/logic.js');

const appDir = __dirname;
const configPath = path.join(appDir, 'config.json');

// Standalone always-on-top panels; each has its own saved bounds and
// visibility, and every one is draggable + resizable on all four edges.
// 'controls' is special: a tiny button bar that is NEVER click-through, so
// the mouse can always reach its choices picker and quit / move-lock buttons.
const PANELS = ['advice', 'deck', 'opponent', 'lessons', 'stats', 'controls'];
const DATA_FILES = ['live.json', 'advice.json', 'lessons.json', 'lesson_store.json', 'deck_stats.json', 'settings.json', 'jev.json'];

const defaults = {
  overlayDir: process.env.HS_OVERLAY_DIR_WIN || path.join(os.homedir(), 'hs-overlay'),
  opacity: 0.94,
  pollMs: 250,
  staleAdviceSeconds: 75,
  windows: {
    advice: { x: 48, y: 96, width: 430, height: 560, visible: true },
    deck: { x: 490, y: 96, width: 260, height: 700, visible: true },
    opponent: { x: 762, y: 96, width: 260, height: 480, visible: true },
    lessons: { x: 48, y: 668, width: 430, height: 220, visible: true },
    stats: { x: 48, y: 900, width: 430, height: 150, visible: true },
    controls: { x: 48, y: 48, width: 160, height: 36, visible: true },
  },
  hotkeys: {
    toggleClickThrough: 'CommandOrControl+Shift+F',
    toggleVisible: 'CommandOrControl+Shift+9',
    opacityDown: 'CommandOrControl+Shift+-',
    opacityUp: 'CommandOrControl+Shift+=',
    resetBounds: 'CommandOrControl+Shift+0',
    toggleAdvice: 'CommandOrControl+Shift+1',
    toggleDeck: 'CommandOrControl+Shift+2',
    toggleOpponent: 'CommandOrControl+Shift+3',
    toggleLessons: 'CommandOrControl+Shift+4',
    toggleStats: 'CommandOrControl+Shift+5',
    cycleChoices: 'CommandOrControl+Shift+J',
  },
};

let config = loadConfig();
let wins = {}; // panel name -> BrowserWindow
let clickThrough = true;
let moveModeTimer = null;

// Move mode auto-reverts to click-through after this much idle time, so the
// hotkey behaves like "unlock, drag, and forget" instead of a manual toggle.
const MOVE_MODE_IDLE_MS = 4000;

function armMoveModeTimer() {
  if (clickThrough) return;
  clearTimeout(moveModeTimer);
  moveModeTimer = setTimeout(() => setClickThrough(true), MOVE_MODE_IDLE_MS);
}

function loadConfig() {
  try {
    if (fs.existsSync(configPath)) {
      const cfg = mergeConfig(defaults, JSON.parse(fs.readFileSync(configPath, 'utf8')), PANELS);
      // Saved configs predate the choices button; a narrower bar would clip it.
      cfg.windows.controls.width = Math.max(cfg.windows.controls.width, defaults.windows.controls.width);
      return cfg;
    }
  } catch (error) {
    console.error('Could not read config.json:', error);
  }
  return mergeConfig(defaults, null, PANELS);
}

let saveTimer = null;

function saveConfigNow() {
  for (const name of PANELS) {
    const win = wins[name];
    if (win && !win.isDestroyed()) {
      config.windows[name] = { ...config.windows[name], ...win.getBounds(), visible: win.isVisible() };
    }
  }
  try {
    fs.writeFileSync(configPath, JSON.stringify(config, null, 2));
  } catch (error) {
    console.error('Could not write config.json:', error);
  }
}

// Move/resize events fire continuously during a drag across four windows;
// batch them into one write instead of hammering the disk.
function saveConfig() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveConfigNow, 400);
}

function createPanel(name) {
  const bounds = config.windows[name];
  const isControls = name === 'controls'; // fixed-size button bar, always clickable
  const win = new BrowserWindow({
    x: bounds.x,
    y: bounds.y,
    width: bounds.width,
    height: bounds.height,
    show: bounds.visible !== false,
    frame: false,
    transparent: true,
    resizable: !isControls,
    movable: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  // Defense-in-depth: panels render local files only — never navigate or pop up.
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', (event) => event.preventDefault());
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.setOpacity(Number(config.opacity || 0.94));
  if (!isControls) win.setIgnoreMouseEvents(true, { forward: true });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'), { query: { panel: name } });
  win.on('move', () => { saveConfig(); armMoveModeTimer(); });
  win.on('resize', () => { saveConfig(); armMoveModeTimer(); });
  win.on('close', saveConfigNow);
  return win;
}

function broadcast(channel, payload) {
  for (const name of PANELS) {
    const win = wins[name];
    if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
  }
}

function setClickThrough(value) {
  clickThrough = value;
  clearTimeout(moveModeTimer);
  for (const name of PANELS) {
    if (name === 'controls') continue; // the button bar must stay clickable
    const win = wins[name];
    if (win && !win.isDestroyed()) win.setIgnoreMouseEvents(clickThrough, { forward: true });
  }
  broadcast('overlay-state', { clickThrough, opacity: Number(config.opacity || 0.94) });
  armMoveModeTimer();
}

function changeOpacity(delta) {
  const next = Math.max(0.35, Math.min(1, Number(((config.opacity || 0.94) + delta).toFixed(2))));
  config.opacity = next;
  for (const name of PANELS) {
    const win = wins[name];
    if (win && !win.isDestroyed()) win.setOpacity(next);
  }
  saveConfig();
  broadcast('overlay-state', { clickThrough, opacity: next });
}

function togglePanel(name) {
  const win = wins[name];
  if (!win || win.isDestroyed()) return;
  win.isVisible() ? win.hide() : win.showInactive();
  saveConfig();
}

function registerHotkey(accelerator, handler) {
  if (!globalShortcut.register(accelerator, handler)) {
    console.warn(`Hotkey ${accelerator} is taken by another app — that shortcut will not work.`);
  }
}

function registerHotkeys() {
  const h = { ...defaults.hotkeys, ...(config.hotkeys || {}) };
  registerHotkey(h.toggleClickThrough, () => setClickThrough(!clickThrough));
  registerHotkey(h.toggleVisible, () => {
    const anyVisible = PANELS.some((name) => wins[name] && !wins[name].isDestroyed() && wins[name].isVisible());
    for (const name of PANELS) {
      const win = wins[name];
      if (!win || win.isDestroyed()) continue;
      anyVisible ? win.hide() : win.showInactive();
    }
    saveConfig();
  });
  registerHotkey(h.opacityDown, () => changeOpacity(-0.08));
  registerHotkey(h.opacityUp, () => changeOpacity(0.08));
  registerHotkey(h.resetBounds, () => {
    for (const name of PANELS) {
      const win = wins[name];
      if (win && !win.isDestroyed()) win.setBounds(defaults.windows[name]);
    }
    saveConfig();
  });
  registerHotkey(h.toggleAdvice, () => togglePanel('advice'));
  registerHotkey(h.toggleDeck, () => togglePanel('deck'));
  registerHotkey(h.toggleOpponent, () => togglePanel('opponent'));
  registerHotkey(h.toggleLessons, () => togglePanel('lessons'));
  registerHotkey(h.toggleStats, () => togglePanel('stats'));
  registerHotkey(h.cycleChoices, () => {
    // A throw here would be uncaught in the main process (an error dialog
    // mid-game) when the shared folder is briefly unwritable.
    try {
      setChoices(nextChoicesMode(readChoices()));
    } catch (error) {
      console.warn('Could not change the choices mode:', error.message);
    }
  });
}

// Choices mode lives in the SHARED folder (settings.json), not config.json:
// `hst live` on the WSL side reads it to decide who answers mulligans and
// Discovers (hstracker/choices.py).
function settingsPath() {
  return path.join(config.overlayDir, 'settings.json');
}

function readSettings() {
  try {
    const data = JSON.parse(fs.readFileSync(settingsPath(), 'utf8'));
    return data && typeof data === 'object' && !Array.isArray(data) ? data : {};
  } catch (_error) {
    return {};
  }
}

function readChoices() {
  return readSettings().choices;
}

function setChoices(mode) {
  if (!CHOICES_MODES.includes(mode)) throw new Error(`Unknown choices mode: ${mode}`);
  const next = { ...readSettings(), choices: mode, updated: Date.now() / 1000 };
  const tmp = `${settingsPath()}.tmp`;
  fs.mkdirSync(config.overlayDir, { recursive: true });
  fs.writeFileSync(tmp, JSON.stringify(next, null, 1));
  fs.renameSync(tmp, settingsPath());
  return mode;
}

// Push, don't poll: watch the shared folder and nudge only the affected
// renderers when a data file lands. Renderers keep a slow fallback poll in
// case a watch event is ever missed.
let watchDebounce = new Map();

function watchOverlayDir() {
  try {
    fs.mkdirSync(config.overlayDir, { recursive: true });
    fs.watch(config.overlayDir, (_eventType, fileName) => {
      if (!fileName || !DATA_FILES.includes(fileName)) return;
      clearTimeout(watchDebounce.get(fileName));
      // Tiny debounce: atomic replace can fire multiple events per write.
      watchDebounce.set(fileName, setTimeout(() => broadcast('overlay-file-changed', fileName), 40));
    });
  } catch (error) {
    console.warn('fs.watch unavailable, renderers fall back to polling:', error.message);
  }
}

// Card-art tiles cached on disk so rows render instantly and offline.
const ART_CACHE_DIR = path.join(appDir, 'art-cache');
const artDownloads = new Set();

ipcMain.handle('card-art', (_event, cardId) => {
  if (!/^[A-Za-z0-9_.-]+$/.test(String(cardId))) return null;
  const cached = path.join(ART_CACHE_DIR, `${cardId}.png`);
  if (fs.existsSync(cached)) return `file://${cached.replaceAll('\\', '/')}`;
  const remote = `https://art.hearthstonejson.com/v1/tiles/${cardId}.png`;
  if (!artDownloads.has(cardId)) {
    artDownloads.add(cardId);
    fs.mkdirSync(ART_CACHE_DIR, { recursive: true });
    const request = net.request(remote);
    request.on('response', (response) => {
      if (response.statusCode !== 200) return;
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => fs.writeFile(`${cached}.tmp`, Buffer.concat(chunks), (err) => {
        if (!err) fs.rename(`${cached}.tmp`, cached, () => {});
      }));
    });
    request.on('error', () => artDownloads.delete(cardId));
    request.end();
  }
  return remote; // serve the remote URL this time; the cache hits next render
});

ipcMain.handle('overlay-config', () => ({
  overlayDir: config.overlayDir,
  pollMs: config.pollMs,
  staleAdviceSeconds: config.staleAdviceSeconds,
  push: true,
}));

ipcMain.handle('read-json', async (_event, fileName) => {
  if (!DATA_FILES.includes(fileName)) {
    throw new Error(`Unsupported overlay file: ${fileName}`);
  }
  const filePath = path.join(config.overlayDir, fileName);
  const stat = fs.statSync(filePath);
  const data = JSON.parse(fs.readFileSync(filePath, 'utf8'));
  return { fileName, path: filePath, mtimeMs: stat.mtimeMs, data };
});

ipcMain.handle('set-click-through', (_event, value) => setClickThrough(Boolean(value)));

ipcMain.handle('set-choices', (_event, mode) => setChoices(String(mode)));

// Quit button on the controls bar — the only mouse-driven way out, since
// every other panel is click-through.
ipcMain.handle('overlay-quit', () => {
  saveConfigNow();
  app.quit();
});

app.whenReady().then(() => {
  for (const name of PANELS) wins[name] = createPanel(name);
  registerHotkeys();
  watchOverlayDir();
});

app.on('window-all-closed', () => {
  globalShortcut.unregisterAll();
  app.quit();
});
