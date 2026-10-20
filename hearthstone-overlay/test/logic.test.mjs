import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { escapeHtml, isValidCardId, cardRowHtml, isAdviceStale, mergeConfig, panelLessons, lessonLabel } = require('../renderer/logic.js');

test('panelLessons: one headline + two points, deck tips preferred, matched excluded', () => {
  const records = [
    { lesson: 'old headline', headline: true },
    { lesson: 'general tip 1' },
    { lesson: 'deck tip 1', deck: 'Aya Rogue' },
    { lesson: 'matched one', deck: 'Aya Rogue' },
    { lesson: 'deck tip 2', deck: 'Aya Rogue' },
  ];
  const p = panelLessons(records, { exclude: ['matched one'] });
  assert.equal(p.headline.lesson, 'old headline');
  assert.deepEqual(p.points.map((r) => r.lesson), ['deck tip 1', 'deck tip 2'], 'deck tips win, matched excluded');
  const noDeck = panelLessons([{ lesson: 'h', headline: true }, { lesson: 'g1' }, { lesson: 'g2' }, { lesson: 'g3' }]);
  assert.deepEqual(noDeck.points.map((r) => r.lesson), ['g1', 'g2'], 'falls back to general tips');
  assert.deepEqual(panelLessons(null), { headline: null, points: [] });
});

test('panelLessons: tips filtered by the deck being played (regression)', () => {
  // Real bug 2026-07-13: Aya Rogue tips filled the panel through a 17-game
  // Two Bit Rogue session because deck-tagged tips outranked everything
  // regardless of which deck was live.
  const records = [
    { lesson: 'aya tip', deck: 'Aya Rogue' },
    { lesson: 'two bit tip', deck: 'Two Bit Rogue' },
    { lesson: 'general tip' },
  ];
  const playing = panelLessons(records, { deck: 'Two Bit Rogue' });
  assert.deepEqual(playing.points.map((r) => r.lesson), ['two bit tip', 'general tip'],
    'current-deck tip first, then general; other decks never shown');
  const caseInsensitive = panelLessons(records, { deck: 'two bit rogue' });
  assert.deepEqual(caseInsensitive.points.map((r) => r.lesson), ['two bit tip', 'general tip']);
  const noMatch = panelLessons(records, { deck: 'Burn Warrior' });
  assert.deepEqual(noMatch.points.map((r) => r.lesson), ['general tip'],
    'no current-deck tips: general only, other decks still hidden');
  const unknownDeck = panelLessons(records, { deck: null });
  assert.deepEqual(unknownDeck.points.map((r) => r.lesson), ['aya tip', 'two bit tip'],
    'deck unknown: old behavior (tagged tips first) so nothing vanishes');
});

test('lessonLabel: prefers title, clips long text', () => {
  assert.equal(lessonLabel({ lesson: 'long text here', title: 'Short' }), 'Short');
  const clipped = lessonLabel({ lesson: 'x'.repeat(100) }, 20);
  assert.equal(clipped.length, 20);
  assert.ok(clipped.endsWith('…'));
});

test('escapeHtml escapes all five HTML-significant characters', () => {
  assert.equal(escapeHtml(`<img src=x onerror="a&b('c')">`),
    '&lt;img src=x onerror=&quot;a&amp;b(&#39;c&#39;)&quot;&gt;');
  assert.equal(escapeHtml(null), '');
  assert.equal(escapeHtml(undefined), '');
  assert.equal(escapeHtml(42), '42');
});

test('isValidCardId accepts real ids and rejects injection shapes', () => {
  assert.ok(isValidCardId('CORE_EX1_145'));
  assert.ok(isValidCardId('CATA_485'));
  assert.ok(!isValidCardId("x') , url('javascript:alert(1)"));
  assert.ok(!isValidCardId('../../etc/passwd'));
  assert.ok(!isValidCardId(''));
});

test('cardRowHtml renders name, cost gem, count and odds', () => {
  const { html, copies } = cardRowHtml(
    { name: 'Fan of Knives', cost: 2, count: 2 },
    { left: 2, remaining: 20 },
  );
  assert.equal(copies, 2);
  assert.match(html, /Fan of Knives/);
  assert.match(html, /×2/);
  assert.match(html, /10%/); // 2 of 20
  assert.doesNotMatch(html, /gone|flash|art/);
});

test('cardRowHtml greys fully-drawn cards and hides their odds', () => {
  const { html } = cardRowHtml({ name: 'Preparation', cost: 0, count: 2 }, { left: 0, remaining: 15 });
  assert.match(html, /class="deck-card gone"/);
  assert.doesNotMatch(html, /%/);
  assert.doesNotMatch(html, /×0/);
});

test('cardRowHtml flashes only when the count changed', () => {
  const changed = cardRowHtml({ name: 'Rockskipper', cost: 2 }, { left: 1, prevLeft: 2 });
  assert.match(changed.html, /flash/);
  const same = cardRowHtml({ name: 'Rockskipper', cost: 2 }, { left: 1, prevLeft: 1 });
  assert.doesNotMatch(same.html, /flash/);
  const firstSeen = cardRowHtml({ name: 'Rockskipper', cost: 2 }, { left: 1, prevLeft: undefined });
  assert.doesNotMatch(firstSeen.html, /flash/);
});

test('cardRowHtml escapes hostile card names and coerces counts', () => {
  const { html } = cardRowHtml(
    { name: `<script>alert('x')</script>`, cost: '"><b>', count: '2' },
    { left: '2', remaining: 10 },
  );
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /×2/); // string '2' coerced to number
});

test('cardRowHtml only applies art for validated ids', () => {
  const good = cardRowHtml({ name: 'X', cost: 1, id: 'CORE_1' }, { artUrl: 'file:///tile.png' });
  assert.match(good.html, /background-image/);
  const bad = cardRowHtml({ name: 'X', cost: 1, id: "') url('evil" }, { artUrl: 'file:///tile.png' });
  assert.doesNotMatch(bad.html, /background-image/);
});

test('isAdviceStale: idle and gameover cards never go stale', () => {
  const old = { kind: 'idle', ts: 0, turn: 1 };
  assert.equal(isAdviceStale(old, 9, 75, 1e10), false);
  assert.equal(isAdviceStale({ kind: 'gameover', ts: 0 }, 9, 75, 1e10), false);
});

test('isAdviceStale: wrong turn or old age flags turn advice', () => {
  const now = 1000;
  const fresh = { kind: 'turn', ts: now - 10, turn: 5 };
  assert.equal(isAdviceStale(fresh, 5, 75, now), false);
  assert.equal(isAdviceStale(fresh, 6, 75, now), true, 'live turn moved on');
  const aged = { kind: 'turn', ts: now - 100, turn: 5 };
  assert.equal(isAdviceStale(aged, 5, 75, now), true, 'older than threshold');
  assert.equal(isAdviceStale({ kind: 'turn' }, 5, 75, now), true, 'no timestamp = stale');
});

test('mergeConfig deep-merges panels and hotkeys over defaults', () => {
  const defaults = {
    opacity: 0.9,
    windows: {
      advice: { x: 1, y: 2, width: 100, height: 200, visible: true },
      deck: { x: 3, y: 4, width: 100, height: 200, visible: true },
    },
    hotkeys: { toggleClickThrough: 'A', toggleVisible: 'B' },
  };
  const saved = {
    opacity: 0.5,
    windows: { advice: { x: 99 } },
    hotkeys: { toggleVisible: 'Z' },
  };
  const cfg = mergeConfig(defaults, saved, ['advice', 'deck']);
  assert.equal(cfg.opacity, 0.5);
  assert.equal(cfg.windows.advice.x, 99, 'saved value wins');
  assert.equal(cfg.windows.advice.height, 200, 'missing fields fall back to defaults');
  assert.deepEqual(cfg.windows.deck, defaults.windows.deck);
  assert.equal(cfg.hotkeys.toggleVisible, 'Z');
  assert.equal(cfg.hotkeys.toggleClickThrough, 'A', 'new hotkeys survive old configs');
  assert.equal(mergeConfig(defaults, null, ['advice', 'deck']).opacity, 0.9, 'null saved = defaults');
});

const { CHOICES_MODES, choicesMode, nextChoicesMode, choicesLabel, secondOpinion } = require('../renderer/logic.js');

test('choices picker: cycle order, labels, invalid settings fall back to the tracker default', () => {
  assert.deepEqual(CHOICES_MODES, ['jev', 'coach', 'both', 'off']);
  assert.equal(nextChoicesMode('jev'), 'coach');
  assert.equal(nextChoicesMode('off'), 'jev', 'wraps around');
  assert.equal(nextChoicesMode(null), 'jev', 'unset starts the cycle');
  assert.equal(choicesMode({ choices: 'both' }), 'both');
  assert.equal(choicesMode({ choices: 'sonnet' }), null);
  assert.equal(choicesMode(null), null);
  assert.equal(choicesLabel(null), 'Default');
  assert.equal(choicesLabel('jev'), 'Jev');
});

test('secondOpinion: Jev beside the coach, never duplicating or outliving the decision', () => {
  const jevMull = { kind: 'mulligan', ts: 1000, mulligan: [
    { card: 'Slam', keep: true, reason: 'Jev: 72% keep' },
    { card: 'For Glory!', keep: false, reason: 'Jev: 22% keep' },
  ] };
  const coachMull = { kind: 'mulligan', model: 'haiku-5-5', ts: 1010 };
  assert.equal(secondOpinion(coachMull, jevMull, 1020), 'Jev: keep Slam 72% · toss For Glory! 22%');
  assert.equal(secondOpinion({ kind: 'mulligan', model: 'jev-1.13.0', ts: 1000 }, jevMull, 1020), null,
    'Jev already is the main card');
  assert.equal(secondOpinion({ kind: 'gameover', ts: 900 }, jevMull, 1020) !== null, true,
    'shows over last game\'s card until the coach answers');
  assert.equal(secondOpinion({ kind: 'turn', ts: 1100 }, jevMull, 1120), null, 'a newer turn card hides it');
  assert.equal(secondOpinion(coachMull, jevMull, 1000 + 181), null, 'stale after 3 minutes');
  assert.equal(secondOpinion(coachMull, null, 1020), null);
  const jevDisc = { kind: 'discover', ts: 2000, turn: 5, discover: 'Pick B — Jev 70%' };
  assert.equal(secondOpinion({ kind: 'turn', turn: 5, model: 'haiku-5-5', ts: 2001 }, jevDisc, 2010), 'Jev: Pick B — Jev 70%');
  assert.equal(secondOpinion({ kind: 'turn', turn: 6, ts: 2050 }, jevDisc, 2060), null, 'next turn hides it');
});
