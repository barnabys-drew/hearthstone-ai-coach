const { app, BrowserWindow, globalShortcut, ipcMain, net } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');

const appDir = __dirname;
const configPath = path.join(appDir, 'config.json');

// Four standalone always-on-top panels; each has its own saved bounds and
// visibility, and every one is draggable + resizable on all four edges.
const PANELS = ['advice', 'deck', 'opponent', 'lessons'];
const DATA_FILES = ['live.json', 'advice.json', 'lessons.json'];

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
  let cfg = { ...defaults };
  try {
    if (fs.existsSync(configPath)) {
      const saved = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      cfg = { ...defaults, ...saved };
      cfg.windows = { ...defaults.windows };
      for (const name of PANELS) {
        cfg.windows[name] = { ...defaults.windows[name], ...((saved.windows || {})[name] || {}) };
      }
      cfg.hotkeys = { ...defaults.hotkeys, ...(saved.hotkeys || {}) };
    }
  } catch (error) {
    console.error('Could not read config.json:', error);
  }
  return cfg;
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
  const win = new BrowserWindow({
    x: bounds.x,
    y: bounds.y,
    width: bounds.width,
    height: bounds.height,
    show: bounds.visible !== false,
    frame: false,
    transparent: true,
    resizable: true,
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
  win.setIgnoreMouseEvents(true, { forward: true });
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

app.whenReady().then(() => {
  for (const name of PANELS) wins[name] = createPanel(name);
  registerHotkeys();
  watchOverlayDir();
});

app.on('window-all-closed', () => {
  globalShortcut.unregisterAll();
  app.quit();
});
