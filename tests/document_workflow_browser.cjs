/* Task-owned localhost acceptance with real app/worker; no model service. */
const { spawn } = require('node:child_process');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const assert = require('node:assert/strict');
const [base, conversation, uploadPath, output, formats = 'docx,pdf'] = process.argv.slice(2);
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'document-workflow-'));
const chrome = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const child = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let ws, sequence = 0; const pending = new Map(), report = { formats: formats.split(','), checks: [] };
function send(method, params = {}) { const id = ++sequence; const result = new Promise((resolve, reject) => pending.set(id, { resolve, reject })); ws.send(JSON.stringify({ id, method, params })); return result; }
async function evaluate(expression) { const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }); if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails)); return result.result.value; }
async function wait(expression, timeout = 180000) { const deadline = Date.now() + timeout; while (Date.now() < deadline) { if (await evaluate(expression)) return; await delay(150); } throw new Error(`Timeout: ${expression}`); }
async function navigate(url) { await send('Page.navigate', { url: base + url }); await wait(`location.href === ${JSON.stringify(base + url)} && document.readyState === 'complete' && !!window.ArtifactWorkspace`); }
async function screenshot(name) { const shot = await send('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(path.join(output, name), Buffer.from(shot.data, 'base64')); }
async function message(text) {
  const count = await evaluate(`document.querySelectorAll('.artifact-card').length`);
  await wait(`!document.querySelector('[data-message-form] textarea').disabled`);
  await evaluate(`(() => { const input = document.querySelector('[data-message-form] textarea'); input.value = ${JSON.stringify(text)}; input.dispatchEvent(new Event('input', { bubbles: true })); input.form.requestSubmit(); })()`);
  await wait(`!document.querySelector('[data-message-form] textarea').disabled && (document.querySelectorAll('.artifact-card').length > ${count} || document.body.innerText.includes('Saved a verified file version.'))`);
  assert.ok(await evaluate(`document.querySelectorAll('.artifact-card').length > ${count}`), 'completed document request must publish a real file card');
  assert.equal(await evaluate(`document.querySelector('.artifact-preview').hidden`), true, 'new output must keep the panel closed');
  return evaluate(`[...document.querySelectorAll('.artifact-card')].at(-1).dataset.versionId`);
}
async function preview(version) {
  await evaluate(`window.ArtifactWorkspace.onOpen(${JSON.stringify(version)}, ${JSON.stringify(conversation)})`);
  await wait(`document.querySelector('.artifact-preview').dataset.versionId === ${JSON.stringify(version)} && [...document.querySelectorAll('.artifact-document-page img')].length > 0 && [...document.querySelectorAll('.artifact-document-page img')].every(image => image.complete && image.naturalWidth > 0)`);
  return evaluate(`fetch('/api/artifact-versions/${version}/preview').then(response => response.json())`);
}
async function close() { await evaluate(`document.querySelector('.artifact-preview').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`); }
(async () => {
  try {
    fs.mkdirSync(output, { recursive: true });
    let port; for (let n = 0; n < 100; n++) { try { port = fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]; break; } catch { await delay(100); } }
    assert.ok(port, 'Chrome must start');
    const tabs = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
    ws = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl);
    ws.addEventListener('message', event => { const result = JSON.parse(event.data), callback = pending.get(result.id); if (!callback) return; pending.delete(result.id); result.error ? callback.reject(new Error(JSON.stringify(result.error))) : callback.resolve(result.result); });
    await new Promise(resolve => ws.addEventListener('open', resolve));
    await send('Runtime.enable'); await send('Page.enable');
    await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
    await navigate(`/chat?conversation=${conversation}`);
    if (report.formats.includes('docx')) {
      const first = await message('create DOCX');
      const original = await preview(first); report.docx_original = first;
      await screenshot('word-desktop.png'); await close();
      const revised = await message(`edit ${first} DOCX`); report.docx_revised = revised;
      const current = await preview(revised);
      assert.equal(current.parent_version_id, first);
      await evaluate(`(() => { const select = document.querySelector('.artifact-preview select'); select.value = ${JSON.stringify(first)}; select.dispatchEvent(new Event('change', { bubbles: true })); })()`);
      await wait(`document.querySelector('.artifact-preview').dataset.versionId === ${JSON.stringify(first)}`);
      const old = await evaluate(`fetch('/api/artifact-versions/${first}/preview').then(response => response.json())`);
      assert.equal(old.sha256, original.sha256); assert.equal(old.preview.source_version_id, first);
      await close();
      await navigate('/artifacts');
      const document = await send('DOM.getDocument');
      const input = await send('DOM.querySelector', { nodeId: document.root.nodeId, selector: '#working-files' });
      await send('DOM.setFileInputFiles', { nodeId: input.nodeId, files: [uploadPath] });
      await evaluate(`document.querySelector('[data-artifact-upload]').requestSubmit()`);
      await wait(`document.querySelector('[data-artifact-status]').textContent.includes('Đã lưu file')`);
      const files = await evaluate(`fetch('/api/artifacts?q=uploaded-word.docx').then(response => response.json())`);
      const uploaded = files.items.find(item => item.name === 'uploaded-word.docx'); assert.ok(uploaded);
      report.uploaded_original = uploaded.version_id; report.uploaded_sha256 = uploaded.sha256;
      await navigate(`/chat?conversation=${conversation}`);
      await wait(`!document.querySelector('[data-working-choose]').disabled`);
      await evaluate(`document.querySelector('[data-working-choose]').click()`);
      await wait(`!!document.querySelector('input[type=checkbox][value="${uploaded.version_id}"]')`);
      await evaluate(`(() => { document.querySelector('input[type=checkbox][value="${uploaded.version_id}"]').click(); [...document.querySelectorAll('.working-file-picker button')].find(button => button.textContent === 'Lưu lựa chọn').click(); })()`);
      await wait(`!document.querySelector('.working-file-picker').open`);
      report.uploaded_revised = await message(`edit ${uploaded.version_id} DOCX`);
      const selected = await evaluate(`fetch('/api/conversations/${conversation}/artifacts').then(response => response.json())`);
      assert.equal(selected.items[0].version_id, uploaded.version_id, 'new output must not change selected input');
      report.checks.push('Word create/edit, upload/select/edit, pinned original and old-version preview');
    }
    if (report.formats.includes('pdf')) {
      const first = await message('create PDF'); report.pdf_original = first;
      await preview(first);
      await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
      await wait(`document.querySelector('.artifact-preview').getAttribute('role') === 'dialog'`);
      assert.equal(await evaluate(`document.querySelector('.chat-main').inert`), true);
      assert.equal(await evaluate(`document.documentElement.scrollWidth <= innerWidth + 1`), true);
      await screenshot('pdf-mobile.png'); await close();
      await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
      report.pdf_revised = await message(`edit ${first} PDF`);
      const revised = await preview(report.pdf_revised);
      assert.equal(revised.parent_version_id, first); assert.equal(revised.preview.source_version_id, report.pdf_revised);
      await screenshot('pdf-desktop.png'); await close();
      report.checks.push('PDF create/reflow/edit, exact-version page assets, mobile dialog and closed-panel behavior');
    }
    fs.writeFileSync(path.join(output, 'browser.json'), JSON.stringify(report, null, 2));
    console.log('PASS: real browser chat, queue, worker, model fixture, Word upload/edit and PDF reflow');
  } catch (error) {
    console.error(error);
    if (ws?.readyState === WebSocket.OPEN) { console.error(await evaluate(`document.body.innerText.slice(-3500)`).catch(() => '')); await screenshot('failure.png').catch(() => {}); }
    process.exitCode = 1;
  } finally {
    if (ws?.readyState === WebSocket.OPEN) { await send('Browser.close').catch(() => {}); ws.close(); }
    child.kill(); await delay(500);
    const target = path.resolve(profile);
    if (path.dirname(target) === path.resolve(os.tmpdir()) && path.basename(target).startsWith('document-workflow-')) fs.rmSync(target, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
})();
