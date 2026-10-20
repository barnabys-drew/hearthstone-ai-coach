// Pure, DOM-free logic shared by the renderer (browser global) and the
// node:test suite (CommonJS). No Electron, no window, no fetch in here.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.OverlayLogic = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  const CARD_ID_RE = /^[A-Za-z0-9_.-]+$/;

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  function isValidCardId(id) {
    return CARD_ID_RE.test(String(id));
  }

  // HTML for one deck/opponent row. Pure: previous count and the resolved
  // art URL are passed in; the caller owns state and async art resolution.
  function cardRowHtml(card, { left = null, remaining = 0, prevLeft, artUrl = null } = {}) {
    const copies = Number(left ?? card.count ?? 1);
    const gone = copies === 0;
    const flash = prevLeft !== undefined && prevLeft !== copies;
    const odds = !gone && remaining ? `${Math.round((copies / remaining) * 100)}%` : '';
    const art = (artUrl && card.id && isValidCardId(card.id))
      ? ` style="background-image: linear-gradient(90deg, rgba(24,25,30,.96) 38%, rgba(24,25,30,.55) 70%, rgba(24,25,30,.25)), url('${artUrl}')"`
      : '';
    const html = `<div class="deck-card${artUrl && art ? ' art' : ''}${gone ? ' gone' : ''}${flash ? ' flash' : ''}"${art}>`
      + `<span class="cost">${escapeHtml(card.cost ?? '?')}</span>`
      + `<span class="deck-name">${escapeHtml(card.name)}</span>`
      + `${copies > 1 ? `<span class="copies">×${copies}</span>` : ''}`
      + `<span class="odds">${odds}</span></div>`;
    return { html, copies };
  }

  // idle/gameover cards sit on screen indefinitely by design — never stale.
  function isAdviceStale(advice, liveTurn, staleSeconds, nowSeconds) {
    const kind = advice?.kind || 'idle';
    if (kind === 'idle' || kind === 'gameover') return false;
    const age = advice?.ts ? nowSeconds - Number(advice.ts) : Infinity;
    const wrongTurn = Boolean(liveTurn) && advice?.turn != null
      && Number(advice.turn) !== Number(liveTurn);
    return wrongTurn || age > Number(staleSeconds || 75);
  }

  // Lessons panel structure: ONE synthesized headline (a coach-authored read
  // across all games, marked headline:true — newest wins), then two short
  // glanceable points (deck tips / kill mechanics). Anything currently
  // matched (shown in red above) is excluded so nothing appears twice.
  function panelLessons(records, { points = 2, exclude = [], deck = null } = {}) {
    const excluded = new Set(exclude);
    const usable = (records || []).filter((rec) => rec && rec.lesson && !excluded.has(rec.lesson));
    const headline = usable.find((rec) => rec.headline) || null;
    const rest = usable.filter((rec) => rec !== headline);
    // Tips for the CURRENT deck first, then general ones. Tips tagged for a
    // DIFFERENT deck are noise and never shown (real complaint: Aya Rogue
    // tips filling the panel during a Two Bit Rogue session). When the live
    // deck is unknown, deck-tagged tips still rank first (old behavior).
    const norm = (s) => (s || '').trim().toLowerCase();
    const current = norm(deck);
    const tagged = rest.filter((rec) => norm(rec.deck));
    const deckTips = current ? tagged.filter((rec) => norm(rec.deck) === current) : tagged;
    const generalTips = rest.filter((rec) => !norm(rec.deck));
    return { headline, points: [...deckTips, ...generalTips].slice(0, points) };
  }

  // Short display form for a panel row: title if present, else clipped lesson.
  function lessonLabel(rec, max = 70) {
    const text = rec.title || rec.lesson || '';
    return text.length > max ? `${text.slice(0, max - 1)}…` : text;
  }

  // Deep-merge saved config over defaults, per-panel and per-hotkey, so a
  // partial config.json never loses fields added in newer versions.
  function mergeConfig(defaults, saved, panels) {
    const cfg = { ...defaults, ...(saved || {}) };
    cfg.windows = { ...defaults.windows };
    for (const name of panels) {
      cfg.windows[name] = { ...defaults.windows[name], ...(((saved || {}).windows || {})[name] || {}) };
    }
    cfg.hotkeys = { ...defaults.hotkeys, ...((saved || {}).hotkeys || {}) };
    return cfg;
  }

  // Who answers mulligans and Discovers (hstracker/choices.py owns the
  // semantics; the overlay only picks and displays). Order = click cycle.
  const CHOICES_MODES = ['jev', 'coach', 'both', 'off'];
  const CHOICES_LABELS = { jev: 'Jev', coach: 'Coach', both: 'Both', off: 'Off' };

  function choicesMode(settings) {
    const mode = settings && settings.choices;
    return CHOICES_MODES.includes(mode) ? mode : null; // null = tracker default
  }

  function nextChoicesMode(mode) {
    const i = CHOICES_MODES.indexOf(mode);
    return CHOICES_MODES[(i + 1) % CHOICES_MODES.length]; // unknown -> first
  }

  function choicesLabel(mode) {
    return mode ? CHOICES_LABELS[mode] : 'Default';
  }

  // Jev's answer as a one-line second opinion, or null when it shouldn't
  // show: the picked mode isn't `both` (in `jev` mode Jev's answer IS the
  // main card, Discover picks included; in `coach`/`off` it doesn't belong
  // on screen), nothing from Jev, the answer is for a different decision
  // than the card on screen, or it is older than maxAge seconds.
  function secondOpinion(advice, jev, nowSeconds, mode, maxAge = 180) {
    if (mode !== 'both') return null;
    if (!jev || !jev.ts || nowSeconds - Number(jev.ts) > maxAge) return null;
    if (advice && String(advice.model || '').startsWith('jev')) return null;
    if (jev.kind === 'mulligan') {
      // Beside the coach's mulligan card, or over whatever older card is up
      // (last game's post-mortem) until the coach answers; gone once a newer
      // turn card replaces it.
      const newerTurnCard = advice && advice.kind !== 'mulligan' && Number(advice.ts || 0) > Number(jev.ts);
      if (newerTurnCard) return null;
      const rows = (jev.mulligan || []).map((r) => {
        const pct = /(\d+%)/.exec(r.reason || '');
        return `${r.keep ? 'keep' : 'toss'} ${r.card}${pct ? ` ${pct[1]}` : ''}`;
      });
      return rows.length ? `Jev: ${rows.join(' · ')}` : null;
    }
    if (jev.kind === 'discover' && jev.discover) {
      if (advice && advice.turn != null && jev.turn != null && Number(advice.turn) > Number(jev.turn)) return null;
      return `Jev: ${jev.discover}`;
    }
    return null;
  }

  return {
    escapeHtml, isValidCardId, cardRowHtml, isAdviceStale, mergeConfig, panelLessons, lessonLabel,
    CHOICES_MODES, choicesMode, nextChoicesMode, choicesLabel, secondOpinion,
  };
});
