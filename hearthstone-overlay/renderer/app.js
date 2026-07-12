const $ = (id) => document.getElementById(id);

// Which panel this window shows: advice | deck | opponent | lessons | all (browser mode).
const panel = new URLSearchParams(location.search).get('panel') || 'all';

let config = { overlayDir: '', pollMs: 250, staleAdviceSeconds: 75 };
let mtimes = new Map();
let live = null;
let advice = null;
let lessonsDoc = null;
let deckSeen = new Map(); // card key -> last seen copies-left, to flash changed rows

// Card art: resolved through the host (Electron disk cache / serve.py cache),
// falling back to the remote tile URL until the cached copy exists.
const ART_URL = (id) => `https://art.hearthstonejson.com/v1/tiles/${encodeURIComponent(id)}.png`;
const artCache = new Map(); // card id -> resolved url
let artRenderQueued = false;

function resolveArt(id) {
  if (artCache.has(id)) return artCache.get(id);
  artCache.set(id, ART_URL(id));
  Promise.resolve(window.overlayAPI.artPath(id)).then((resolved) => {
    if (resolved && resolved !== artCache.get(id)) {
      artCache.set(id, resolved);
      if (!artRenderQueued) {
        artRenderQueued = true;
        setTimeout(() => { artRenderQueued = false; render(); }, 120);
      }
    }
  }).catch(() => {});
  return artCache.get(id);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function setHidden(node, hidden) { node.classList.toggle('hidden', hidden); }

function artStyle(card) {
  if (!card.id || !/^[A-Za-z0-9_.-]+$/.test(String(card.id))) return '';
  return ` style="background-image: linear-gradient(90deg, rgba(24,25,30,.96) 38%, rgba(24,25,30,.55) 70%, rgba(24,25,30,.25)), url('${resolveArt(card.id)}')"`;
}

function cardRow(card, { left = null, remaining = 0 } = {}) {
  const copies = Number(left ?? card.count ?? 1);
  const gone = copies === 0;
  const key = `${card.name}|${card.cost}`;
  const prev = deckSeen.get(key);
  const flash = prev !== undefined && prev !== copies;
  deckSeen.set(key, copies);
  const odds = !gone && remaining ? `${Math.round((copies / remaining) * 100)}%` : '';
  return `<div class="deck-card art${gone ? ' gone' : ''}${flash ? ' flash' : ''}"${artStyle(card)}>`
    + `<span class="cost">${escapeHtml(card.cost ?? '?')}</span>`
    + `<span class="deck-name">${escapeHtml(card.name)}</span>`
    + `${copies > 1 ? `<span class="copies">×${copies}</span>` : ''}`
    + `<span class="odds">${odds}</span></div>`;
}

function renderDeck() {
  if (!live || !live.me) return;
  const remaining = Number(live.me.deck_remaining || 0);
  const deck = (live.me.deck_full && live.me.deck_full.length) ? live.me.deck_full : (live.me.deck_cards_left || []);
  const extras = (live.me.deck_extra || []).filter((c) => c.cost != null);
  $('deck-count').textContent = remaining ? `${remaining} cards` : '';
  $('deck-left').innerHTML = deck.length || extras.length
    ? deck.map((c) => cardRow(c, { left: c.left ?? Number(c.count || 1), remaining })).join('')
      + (extras.length ? `<div class="deck-sep">generated / shuffled</div>${extras.map((c) => cardRow(c, { remaining })).join('')}` : '')
    : '<div class="empty">No deck list yet.</div>';
}

function renderOpponent() {
  if (!live || !live.opp) return;
  $('opp-title').textContent = live.opp.class ? `OPPONENT · ${live.opp.class}` : 'OPPONENT';
  const oppHand = Number(live.opp.hand_hidden || 0) + Number((live.opp.hand || []).length);
  const oppDeck = live.opp.deck_remaining ?? '—';
  const secrets = live.opp.secrets ? ` · ${live.opp.secrets} secrets` : '';
  $('opp-counts').textContent = `${oppHand} in hand · ${oppDeck} in deck${secrets}`;
  const played = (live.opp.played || []).slice().reverse();
  $('opp-played').innerHTML = played.length
    ? played.map((p) => cardRow(p)).join('')
    : '<div class="empty">No reveals yet.</div>';
}

function renderAdvice() {
  const current = advice || {};
  const kind = current.kind || 'idle';
  const isLethal = current.lethal?.is_lethal || kind === 'lethal';
  $('eyebrow').textContent = kind === 'idle' ? 'LIVE COACH' : kind.toUpperCase();
  $('headline').textContent = current.headline || (isLethal ? 'LETHAL' : 'Do this now');
  $('why').textContent = current.why || 'Waiting for the current turn plan.';
  $('turn-pill').textContent = `turn ${live?.turn ?? current.turn ?? '—'}${live ? ` · ${live.whose_turn === 'me' ? 'your turn' : 'opponent'}` : ''}`;

  const lethal = $('lethal');
  if (isLethal) {
    lethal.textContent = current.lethal?.math ? `LETHAL: ${current.lethal.math}` : 'LETHAL — do not trade first.';
  }
  setHidden(lethal, !isLethal);

  $('discover').textContent = current.discover ? `PICK: ${current.discover}` : '';
  setHidden($('discover'), !current.discover);

  const steps = current.steps || [];
  $('steps').innerHTML = steps.map((step) => `<li>${escapeHtml(step)}</li>`).join('');

  const mulligan = current.mulligan || [];
  $('mulligan').innerHTML = mulligan.map((row) => `<div class="mulligan-row"><span class="badge ${row.keep ? 'keep' : 'toss'}">${row.keep ? 'KEEP' : 'TOSS'}</span><div><strong>${escapeHtml(row.card)}</strong><br><span class="empty">${escapeHtml(row.reason || '')}</span></div></div>`).join('');
  setHidden($('mulligan'), !mulligan.length);

  $('warning').textContent = current.warning || '';
  setHidden($('warning'), !current.warning);

  // idle/gameover cards sit on screen indefinitely by design — never stale.
  const restingKind = kind === 'idle' || kind === 'gameover';
  const adviceAge = current.ts ? (Date.now() / 1000) - Number(current.ts) : Infinity;
  const wrongTurn = live?.turn && current.turn != null && Number(current.turn) !== Number(live.turn);
  setHidden($('stale'), restingKind || !(wrongTurn || adviceAge > Number(config.staleAdviceSeconds || 75)));
}

function renderLessons() {
  const fromFile = lessonsDoc?.lessons || [];
  const fromAdvice = advice?.lessons || [];
  const merged = [...new Set([...fromAdvice, ...fromFile])];
  $('lessons').innerHTML = merged.length
    ? merged.map((l) => `<div class="lesson">${escapeHtml(l)}</div>`).join('')
    : '<div class="empty">No lessons recorded yet.</div>';
}

function render() {
  const app = $('app');
  app.classList.toggle('waiting', !live || !live.me);
  if (panel === 'deck' || panel === 'all') renderDeck();
  if (panel === 'opponent' || panel === 'all') renderOpponent();
  if (panel === 'advice' || panel === 'all') renderAdvice();
  if (panel === 'lessons' || panel === 'all') renderLessons();
}

async function pollFile(fileName) {
  try {
    const result = await window.overlayAPI.readJson(fileName);
    if (mtimes.get(fileName) !== result.mtimeMs) {
      mtimes.set(fileName, result.mtimeMs);
      if (fileName === 'live.json') live = result.data;
      if (fileName === 'advice.json') advice = result.data;
      if (fileName === 'lessons.json') lessonsDoc = result.data;
      return true;
    }
  } catch (_error) {
    // Files do not exist until hst live / coach_publish creates them.
  }
  return false;
}

// Each panel polls only the files it renders from.
const FILES_BY_PANEL = {
  advice: ['advice.json', 'live.json'],
  deck: ['live.json'],
  opponent: ['live.json'],
  lessons: ['lessons.json', 'advice.json'],
  all: ['live.json', 'advice.json', 'lessons.json'],
};

async function tick() {
  const files = FILES_BY_PANEL[panel] || FILES_BY_PANEL.all;
  let changed = false;
  for (const file of files) changed = (await pollFile(file)) || changed;
  if (changed) render();
}

async function boot() {
  document.body.dataset.panel = panel;
  config = await window.overlayAPI.config();
  const dataPath = $('data-path');
  if (dataPath) dataPath.textContent = config.overlayDir;
  window.overlayAPI.onState((state) => {
    for (const node of document.querySelectorAll('.overlay-state')) {
      node.textContent = state.clickThrough ? `click-through · ${Math.round(state.opacity * 100)}%` : `move/resize · ${Math.round(state.opacity * 100)}%`;
    }
    document.body.classList.toggle('movable', !state.clickThrough);
  });
  // Push-first: the main process watches the shared folder and nudges us on
  // writes; the interval is only a fallback (slow in push mode).
  window.overlayAPI.onFileChanged(async (fileName) => {
    if ((FILES_BY_PANEL[panel] || FILES_BY_PANEL.all).includes(fileName)) {
      if (await pollFile(fileName)) render();
    }
  });
  render();
  setInterval(tick, config.push ? 2000 : Number(config.pollMs || 250));
  tick();
}

boot();
