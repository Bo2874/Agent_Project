/* Run: node tests/artifact_panel_browser.cjs (CHROME_PATH overrides the browser). */
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'artifact-panel-test-'));
const chrome = process.env.CHROME_PATH || (process.platform === 'win32'
  ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : 'google-chrome');
const child = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run',
  '--no-default-browser-check', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
  'about:blank'], { windowsHide: true, stdio: 'ignore' });
let ws, next = 0;
const pending = new Map();
async function send(method, params = {}) {
  const id = ++next;
  const response = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  ws.send(JSON.stringify({ id, method, params }));
  return response;
}
(async () => {
  try {
    let port;
    for (let attempt = 0; attempt < 100; attempt++) {
      try { port = fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]; break; }
      catch { await delay(100); }
    }
    assert.ok(port, 'Chrome must start');
    const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
    ws = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
    ws.addEventListener('message', event => {
      const packet = JSON.parse(event.data), callback = pending.get(packet.id);
      if (!callback) return;
      pending.delete(packet.id);
      packet.error ? callback.reject(new Error(JSON.stringify(packet.error))) : callback.resolve(packet.result);
    });
    await new Promise(resolve => ws.addEventListener('open', resolve));
    await send('Runtime.enable');
    const source = fs.readFileSync(path.join(__dirname, '../frontend/static/artifact-panel.js'), 'utf8');
    await send('Runtime.evaluate', { expression: source });
    const result = await send('Runtime.evaluate', { awaitPromise: true, returnByValue: true,
      expression: `(${async function () {
        const check = (value, message) => { if (!value) throw new Error(message); };
        const tick = () => new Promise(resolve => setTimeout(resolve, 0));
        const root = document.createElement('main'); root.className = 'chat-shell'; document.body.append(root);
        const queue = [];
        const panel = window.createArtifactPanel(root, { requestJson: (url, options) => {
          if (url.includes('/versions?')) return Promise.resolve({ items: [], total: 0 });
          return new Promise(resolve => queue.push({ url, resolve, options }));
        } });
        const data = (version, artifact = 'a') => ({ version_id: version, artifact_id: artifact,
          name: 'report.xlsx', version_number: Number(version.slice(1)), parent_version_id: null,
          calculation_status: 'fresh', preview: { kind: 'xlsx', sheet: 'Sheet1',
            offset: 0, limit: 100, sheets: [{ name: 'Sheet1', rows: 1, columns: 1 }], cells: [] } });
        const ref = (version, artifact = 'a') => ({ version_id: version, artifact_id: artifact });
        const loading = panel.open('v1', 'chat1');
        panel.onVersionReady(ref('v2'), 'chat1'); panel.onVersionReady(ref('v3'), 'chat1');
        queue.shift().resolve(data('v1')); await loading; await tick();
        check(queue.length === 1 && queue[0].url.includes('/v3/preview'), 'initial load must follow latest ready version');
        panel.onVersionReady(ref('v4'), 'chat1'); queue.shift().resolve(data('v3')); await tick();
        check(queue.length === 1 && queue[0].url.includes('/v4/preview'), 'automatic refresh must retain a newer event');
        queue.shift().resolve(data('v4')); await tick();
        check(root.querySelector('.artifact-preview').dataset.versionId === 'v4', 'v4 must be displayed');
        const page = panel.open('v4', 'chat1', { offset: 100 }, false);
        panel.onVersionReady(ref('v5'), 'chat1'); queue.shift().resolve(data('v4')); await page; await tick();
        check(queue.length === 1 && queue[0].url.includes('/v5/preview'), 'pagination must retain ready events');
        panel.close(); queue.shift().resolve(data('v5')); await tick();
        panel.onVersionReady(ref('v6'), 'chat1');
        check(root.querySelector('.artifact-preview').hidden && queue.length === 0, 'closed panel must stay closed');
        const first = panel.open('v1', 'chat1'); panel.onVersionReady(ref('v9'), 'chat1');
        const switched = panel.open('v2', 'chat2');
        queue.shift().resolve(data('v1')); queue.shift().resolve(data('v2', 'other'));
        await Promise.all([first, switched]); await tick();
        check(queue.length === 0 && root.querySelector('.artifact-preview').dataset.versionId === 'v2', 'conversation switch must discard stale events');
        panel.close(); root.remove(); return 'PASS';
      }.toString()})()` });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    assert.equal(result.result.value, 'PASS');
    console.log('PASS: preview loading, refresh, pagination, close and context races in real Chrome');
  } catch (error) { console.error(error); process.exitCode = 1; }
  finally {
    if (ws?.readyState === WebSocket.OPEN) { await send('Browser.close').catch(() => {}); ws.close(); }
    child.kill();
    await delay(500);
    const target = path.resolve(profile), parent = path.resolve(os.tmpdir());
    if (path.dirname(target) === parent && path.basename(target).startsWith('artifact-panel-test-')) {
      fs.rmSync(target, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
    }
  }
})();
