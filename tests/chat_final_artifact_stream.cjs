// Exercise the actual SSE consumer without a running browser, model or database.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../frontend/static/chat.js'), 'utf8');
const consumer = source.slice(source.indexOf('  function parseSseBlock('), source.indexOf('  async function requestAgentStream('));
const ref = id => ({ version_id: id, artifact_id: 'file', name: `${id}.xlsx` });

function harness(stored = {}) {
  const calls = [], cards = [], previews = [];
  const timeline = { markdown: '', root: { dataset: {} }, cancelButton: {}, titleText: {} };
  const bubble = { classList: { add() {} } };
  const context = {
    TextDecoder, Uint8Array, CustomEvent: class {},
    activeConversationId: 'chat', status: {}, messageList: {},
    window: {
      renderMarkdown: (_, text) => calls.push(['text', text]),
      renderArtifactCards: (_, refs) => { cards.push(...refs); calls.push(['card', refs]); },
      dispatchEvent() {},
    },
    artifactPreview: { onVersionReady: value => previews.push(value) },
    requestJson: async () => [stored],
    finishTimeline: (value, state) => { value.terminal = !['queued', 'running', 'waiting_approval'].includes(state); },
    renderCitations() {}, stopTimelineClock() {}, updateTimelineSearchMode() {},
    addAvailableResumeButton: async () => {},
  };
  vm.createContext(context);
  vm.runInContext(`${consumer}\nthis.consume = consumeAgentStream;`, context);
  async function consume(events, beforeRead = () => {}) {
    let index = 0;
    const response = { ok: true, body: { getReader: () => ({
      async read() {
        beforeRead(index);
        if (index === events.length) return { done: true };
        const [event, data, id] = events[index++];
        return { done: false, value: new TextEncoder().encode(`${id ? `id: ${id}\n` : ''}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`) };
      },
      cancel: async () => {}, releaseLock() {},
    }) } };
    await context.consume(response, { assistantBubble: bubble, timeline });
  }
  return { consume, calls, cards, previews, timeline };
}

const ready = value => ['artifact_ready', { conversation_id: 'chat', ref: value }];
const done = refs => ['done', { artifact_refs: refs, usage: {} }];

test('streams text immediately but exposes only the final output after done', async () => {
  const h = harness();
  await h.consume([
    ready(ref('v1')), ['delta', { content: 'Đã sửa ' }], ready(ref('v2')),
    ['delta', { content: 'xong.' }], done([ref('v2')]),
  ], index => {
    if (index <= 4) assert.equal(h.cards.length, 0, 'file appeared before answer completed');
    if (index === 2) assert.equal(h.calls[0][1], 'Đã sửa ');
  });
  assert.deepEqual(h.cards.map(value => value.version_id), ['v2']);
  assert.deepEqual(h.previews.map(value => value.version_id), ['v2']);
  assert.equal(h.calls.at(-1)[0], 'card');
});

test('a replayed earlier output cannot replace the latest pending file', async () => {
  const h = harness();
  await h.consume([ready(ref('v1')), ready(ref('v2')), ready(ref('v1')), done(undefined)]);
  assert.deepEqual(h.cards.map(value => value.version_id), ['v2']);
});

test('snapshot recovery renders only the final stored file after stored text', async () => {
  const h = harness({ id: 'answer', content: 'Hoàn thành', artifact_refs: [ref('v1'), ref('v2')] });
  await h.consume([ready(ref('v1')), ['snapshot', { status: 'completed', message_id: 'answer' }]]);
  assert.deepEqual(h.cards.map(value => value.version_id), ['v2']);
  assert.equal(h.calls[0][0], 'text');
});

test('cancellation and errors never expose pending intermediate files', async () => {
  const cancelled = harness();
  await cancelled.consume([ready(ref('v1')), ['cancelled', {}]]);
  assert.equal(cancelled.cards.length, 0);
  const failed = harness();
  await assert.rejects(failed.consume([ready(ref('v1')), ['error', { message: 'failed', resumable: false }]]));
  assert.equal(failed.cards.length, 0);
});

test('pending final output survives an interrupted subscription', async () => {
  const h = harness();
  await assert.rejects(h.consume([ready(ref('v1')), ready(ref('v2'))]));
  assert.equal(h.cards.length, 0);
  await h.consume([done(undefined)]);
  assert.deepEqual(h.cards.map(value => value.version_id), ['v2']);
});

test('an explicit reset discards outputs from the previous attempt', async () => {
  const h = harness();
  await h.consume([ready(ref('v1')), ['reset', { message: 'Retry' }], done([])]);
  assert.equal(h.cards.length, 0);
});

test('answer reset replaces a tool preface without losing the pending final file', async () => {
  const h = harness();
  await h.consume([
    ready(ref('v1')), ['delta', { content: 'I will edit this.' }],
    ['answer_reset', {}], ['delta', { content: 'Edited successfully.' }], done(undefined),
  ]);
  const texts = h.calls.filter(call => call[0] === 'text').map(call => call[1]);
  assert.deepEqual(texts, ['I will edit this.', '', 'Edited successfully.']);
  assert.deepEqual(h.cards.map(value => value.version_id), ['v1']);
});

function cardRenderer() {
  class Element {
    constructor() { this.children = []; this.dataset = {}; }
    append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
    remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
    querySelector(selector) { return this.children.find(child => child.className === selector.slice(1)); }
    addEventListener() {}
  }
  const row = new Element(), bubble = { closest: () => row };
  const context = { window: {}, document: { createElement: () => new Element() } };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../frontend/static/artifact-panel.js'), 'utf8'), context);
  return { render: context.window.renderArtifactCards, row, bubble };
}

test('reloading a chat replaces previous cards instead of accumulating versions', () => {
  const { render, row, bubble } = cardRenderer();
  render(bubble, [ref('v1')], 'chat', { replace: true });
  render(bubble, [ref('v2')], 'chat', { replace: true });
  render(bubble, [ref('v2')], 'chat', { replace: true });
  assert.equal(row.children.length, 1);
  assert.deepEqual(row.children[0].children.map(card => card.dataset.versionId), ['v2']);
  render(bubble, [], 'chat', { replace: true });
  assert.equal(row.children.length, 0);
});

test('the Automation results renderer still supports multiple files', () => {
  const { render, row, bubble } = cardRenderer();
  render(bubble, [ref('v1'), ref('v2')], 'automation');
  assert.deepEqual(row.children[0].children.map(card => card.dataset.versionId), ['v1', 'v2']);
});

test('authoritative done refs override pending outputs and can explicitly contain no file', async () => {
  const empty = harness();
  await empty.consume([ready(ref('v1')), done([])]);
  assert.equal(empty.cards.length, 0);
  const final = harness();
  await final.consume([ready(ref('v1')), done([ref('v2')])]);
  assert.deepEqual(final.cards.map(value => value.version_id), ['v2']);
});

test('a reloaded active run exposes no file until polling loads the completed answer', async () => {
  const calls = [], timers = [];
  let run = { id: 'run', status: 'running', artifact_refs: [ref('v1')] };
  const bubble = {}, timeline = {
    root: { isConnected: true, closest: () => ({ querySelector: () => bubble }) }, titleText: {},
  };
  const context = {
    activeConversationId: 'chat',
    window: {
      setTimeout: callback => { timers.push(callback); return timers.length; },
      renderMarkdown: (_, text) => calls.push(['text', text]),
      renderArtifactCards: (_, refs) => calls.push(['card', refs.map(value => value.version_id)]),
    },
    requestJson: async url => url.endsWith('/runs') ? [run] : [
      { id: 'answer', content: 'Completed answer', artifact_refs: [ref('v1'), ref('v2')] },
    ],
    updateStoredTimeline: (value, current) => { value.terminal = current.status === 'completed'; },
    renderCitations() {}, stopTimelineClock() {},
  };
  vm.createContext(context);
  const polling = source.slice(source.indexOf('  function pollStoredTimeline('), source.indexOf('  async function loadConversations('));
  const helper = source.slice(source.indexOf('  function renderFinalArtifactCard('), source.indexOf('  async function consumeAgentStream('));
  vm.runInContext(`${helper}\n${polling}\nthis.poll = pollStoredTimeline;`, context);
  context.poll(timeline, 'chat', 'run');
  await timers.shift()();
  assert.equal(calls.length, 0);
  run = { ...run, status: 'completed', assistant_message_id: 'answer' };
  await timers.shift()();
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0], ['text', 'Completed answer']);
  assert.equal(calls[1][0], 'card');
  assert.equal(calls[1][1].length, 1);
  assert.equal(calls[1][1][0], 'v2');
  assert.equal(timeline.terminal, true);
});
