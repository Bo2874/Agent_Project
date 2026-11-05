/* Isolated headless Chrome for repository component QA. */
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
module.exports = async function withBrowser(run) {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'google-ui-qa-'));
  const chrome = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
  const child = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' });
  let ws, sequence = 0;
  const pending = new Map();
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence; pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  try {
    let port;
    for (let i = 0; i < 100; i++) {
      try { port = fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]; break; }
      catch { await delay(100); }
    }
    if (!port) throw new Error('Chrome did not start');
    const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
    ws = new WebSocket(targets.find(item => item.type === 'page').webSocketDebuggerUrl);
    ws.addEventListener('message', event => {
      const packet = JSON.parse(event.data), callback = pending.get(packet.id);
      if (!callback) return;
      pending.delete(packet.id);
      packet.error ? callback.reject(new Error(JSON.stringify(packet.error))) : callback.resolve(packet.result);
    });
    await new Promise(resolve => ws.addEventListener('open', resolve));
    await send('Runtime.enable');
    await run(send);
  } finally {
    if (ws?.readyState === WebSocket.OPEN) {
      await send('Browser.close').catch(() => {}); ws.close();
    }
    child.kill(); await delay(500);
    const target = path.resolve(profile), parent = path.resolve(os.tmpdir());
    if (path.dirname(target) === parent && path.basename(target).startsWith('google-ui-qa-')) {
      fs.rmSync(target, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
    }
  }
};
