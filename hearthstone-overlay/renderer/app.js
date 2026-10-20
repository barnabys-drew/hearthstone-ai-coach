const $ = (id) => document.getElementById(id);
const {
  escapeHtml, isValidCardId, cardRowHtml, isAdviceStale, panelLessons, lessonLabel,
  CHOICES_MODES, choicesMode, nextChoicesMode, choicesLabel, secondOpinion,
} = window.OverlayLogic;

// Which panel this window shows: advice | deck | opponent | lessons | all (browser mode).
const panel = new URLSearchParams(location.search).get('panel') || 'all';

let config = { overlayDir: '', pollMs: 250, staleAdviceSeconds: 75 };
let mtimes = new Map();
let live = null;
let advice = null;
let lessonsDoc = null;
let lessonStore = null;
let deckStats = null;
let settings = null; // settings.json: who answers mulligans/Discovers (choices picker)
let jevDoc = null; // jev.json: Jev's latest answer, shown as a second opinion
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

function setHidden(node, hidden) { node.classList.toggle('hidden', hidden); }

function cardRow(card, { left = null, remaining = 0 } = {}) {
  const key = `${card.name}|${card.cost}`;
  const artUrl = card.id && isValidCardId(card.id) ? resolveArt(card.id) : null;
  const { html, copies } = cardRowHtml(card, { left, remaining, prevLeft: deckSeen.get(key), artUrl });
  deckSeen.set(key, copies);
  return html;
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

  const second = secondOpinion(current, jevDoc, Date.now() / 1000);
  $('second-opinion').textContent = second || '';
  setHidden($('second-opinion'), !second);

  $('warning').textContent = current.warning || '';
  setHidden($('warning'), !current.warning);

  // Phase 6a: make the authoring model visible (standing constraint) so
  // behavior differences and credit burn stay attributable per model.
  const dataPath = $('data-path');
  if (dataPath) dataPath.textContent = current.model ? `${config.overlayDir} · coach: ${current.model}` : config.overlayDir;

  setHidden($('stale'), !isAdviceStale(current, live?.turn, config.staleAdviceSeconds, Date.now() / 1000));
  renderChoices($('choices-toggle'), (label) => `choices: ${label}`);
}

// The picker's current state on whichever button this window has: the
// controls bar (Electron) or the advice footer (browser mode).
function renderChoices(node, format = (label) => label) {
  if (!node) return;
  const mode = choicesMode(settings);
  node.textContent = format(choicesLabel(mode));
  node.classList.remove(...CHOICES_MODES);
  if (mode) node.classList.add(mode);
}

async function cycleChoices() {
  try {
    await window.overlayAPI.setChoices(nextChoicesMode(choicesMode(settings)));
    if (await pollFile('settings.json')) render();
  } catch (error) {
    console.warn('could not change choices mode:', error);
  }
}

function renderLessons() {
  // Red live matches (full text — they matter RIGHT NOW), then ONE
  // synthesized cross-game headline, then two glanceable tips.
  const matchedRecs = live?.lessons_matched || [];
  const { headline, points } = panelLessons(lessonStore?.lessons || [], { exclude: matchedRecs.map((m) => m.lesson), deck: live?.deck });
  const sections = [];
  if (matchedRecs.length) {
    sections.push(matchedRecs.map((m) => `<div class="lesson matched">${escapeHtml(m.cost ? `${m.lesson} — cost last time: ${m.cost}` : m.lesson)}</div>`).join(''));
  }
  if (headline) {
    sections.push(`<div class="lesson headline">${escapeHtml(headline.lesson)}</div>`);
  }
  if (points.length) {
    sections.push(points.map((rec) => {
      const tag = (rec.deck || '').trim();
      return `<div class="lesson">${tag ? `<span class="lesson-tag">${escapeHtml(tag)}</span> ` : ''}${escapeHtml(lessonLabel(rec))}</div>`;
    }).join(''));
  }
  $('lessons').innerHTML = sections.length ? sections.join('') : '<div class="empty">No lessons recorded yet.</div>';
}

function renderStats() {
  if (!deckStats) return;
  $('stats-deck').textContent = deckStats.deck || '';
  $('stats-record').textContent = deckStats.games
    ? `${deckStats.wins}–${deckStats.losses} · ${deckStats.winrate}% over ${deckStats.games} games`
    : 'first recorded game with this deck';
  $('stats-record').classList.remove('empty');
  const streak = $('stats-streak');
  streak.textContent = deckStats.streak || '';
  streak.className = deckStats.streak ? `streak ${deckStats.streak[0] === 'W' ? 'w' : 'l'}` : '';
  const overall = deckStats.overall;
  $('stats-overall').textContent = overall?.games
    ? `Overall: ${overall.wins}–${overall.losses} · ${overall.winrate}% over ${overall.games} games`
    : '';
  $('stats-last10').innerHTML = (deckStats.last10 || []).map((w) => `<span class="pip ${w ? 'w' : 'l'}"></span>`).join('')
    + (deckStats.last10?.length ? '<span class="pip-label">last 10, newest first</span>' : '');
  $('stats-matchups').innerHTML = (deckStats.matchups || []).map((m) =>
    `<div class="matchup"><span class="mu-class">${escapeHtml(m.opp_class)}</span><span class="mu-rec">${m.wins}–${m.games - m.wins}</span><span class="mu-rate ${m.winrate >= 50 ? 'good' : 'bad'}">${m.winrate}%</span></div>`
  ).join('');
}

function render() {
  const app = $('app');
  app.classList.toggle('waiting', !live || !live.me);
  if (panel === 'deck' || panel === 'all') renderDeck();
  if (panel === 'opponent' || panel === 'all') renderOpponent();
  if (panel === 'advice' || panel === 'all') renderAdvice();
  if (panel === 'lessons' || panel === 'all') renderLessons();
  if (panel === 'stats' || panel === 'all') renderStats();
  if (panel === 'controls') renderChoices($('btn-choices'));
}

async function pollFile(fileName) {
  try {
    const result = await window.overlayAPI.readJson(fileName);
    if (mtimes.get(fileName) !== result.mtimeMs) {
      mtimes.set(fileName, result.mtimeMs);
      if (fileName === 'live.json') live = result.data;
      if (fileName === 'advice.json') advice = result.data;
      if (fileName === 'lessons.json') lessonsDoc = result.data;
      if (fileName === 'lesson_store.json') lessonStore = result.data;
      if (fileName === 'deck_stats.json') deckStats = result.data;
      if (fileName === 'settings.json') settings = result.data;
      if (fileName === 'jev.json') jevDoc = result.data;
      return true;
    }
  } catch (_error) {
    // Files do not exist until hst live / coach_publish creates them.
  }
  return false;
}

// Each panel polls only the files it renders from.
const FILES_BY_PANEL = {
  advice: ['advice.json', 'live.json', 'settings.json', 'jev.json'],
  deck: ['live.json'],
  opponent: ['live.json'],
  lessons: ['lesson_store.json', 'live.json'],
  stats: ['deck_stats.json'],
  controls: ['settings.json'], // buttons, plus the choices picker's state
  all: ['live.json', 'advice.json', 'lessons.json', 'lesson_store.json', 'deck_stats.json', 'settings.json', 'jev.json'],
};

async function tick() {
  const files = FILES_BY_PANEL[panel] || FILES_BY_PANEL.all;
  let changed = false;
  for (const file of files) changed = (await pollFile(file)) || changed;
  if (changed) render();
}

// Controls bar: two buttons that must always work with a mouse — quit, and
// the same move/lock toggle as the Ctrl+Shift+F hotkey. The move button's
// pressed look comes from the existing body.movable class.
function wireControls() {
  let clickThrough = true; // the main process always starts locked
  window.overlayAPI.onState((state) => { clickThrough = state.clickThrough; });
  $('btn-move').addEventListener('click', () => window.overlayAPI.setClickThrough(!clickThrough));
  $('btn-quit').addEventListener('click', () => window.overlayAPI.quit?.());
  $('btn-choices').addEventListener('click', cycleChoices);
}

async function boot() {
  document.body.dataset.panel = panel;
  if (panel === 'controls') wireControls();
  // Browser mode has no controls bar, so the advice footer's twin is the picker.
  if (panel === 'all') $('choices-toggle').addEventListener('click', cycleChoices);
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
