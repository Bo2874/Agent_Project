const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const withBrowser = require('./browser_cdp.cjs');
withBrowser(async send => {
  const sourcePath = path.join(__dirname, '../frontend/static/connectors.js');
  if (fs.existsSync(sourcePath)) await send('Runtime.evaluate', { expression: fs.readFileSync(sourcePath, 'utf8') });
  const result = await send('Runtime.evaluate', { awaitPromise: true, returnByValue: true, expression: `(${async function () {
    const check = (v, message) => { if (!v) throw new Error(message); };
    const tick = () => new Promise(resolve => setTimeout(resolve, 0));
    check(window.GoogleConnectors, 'Google connector UI must exist');
    const root = document.createElement('section');
    root.innerHTML = '<button data-google-choose>Google files</button><span data-google-count></span><div data-google-chips></div><p data-google-status></p>';
    document.body.append(root);
    const saved = { chat1: [], chat2: [] }, writes = [];
    let status = 'connected', oldResolve;
    const files = [{ connection_id: 'connection-a', file_id: 'sheet-a', name: '<svg onload=alert(1)>.xlsx', mime_type: 'application/vnd.google-apps.spreadsheet' },
      { connection_id: 'connection-a', file_id: 'doc-a', name: 'Document', mime_type: 'application/vnd.google-apps.document' }];
    const request = async (url, options = {}) => {
      if (url === '/api/connections/google') return { configured: true, connections: [{ id: 'connection-a', status }] };
      if (url.endsWith('/files')) return { items: status === 'connected' ? files : [] };
      const chat = url.split('/')[3];
      if (options.method === 'PUT') { const refs = JSON.parse(options.body).file_refs; saved[chat] = files.filter(file => refs.some(ref => ref.file_id === file.file_id)).map(file => ({ ...file, context_id: chat })); writes.push({ chat, refs }); }
      if (chat === 'slow') return new Promise(resolve => { oldResolve = resolve; });
      return { items: saved[chat] || [] };
    };
    const panel = window.GoogleConnectors.createChatPanel(root, { requestJson: request });
    await panel.setConversation('chat1');
    await panel.open();
    const choices = [...root.querySelectorAll('input[type=checkbox]')];
    check(choices.length === 2, 'must show explicitly granted files');
    check(!root.querySelector('svg'), 'provider filenames must remain text');
    choices[0].checked = true;
    root.querySelector('[data-google-save]').click(); await tick(); await tick();
    check(writes.length === 1 && writes[0].refs[0].context_id === 'chat1', 'save must bind selected chat');
    check(root.querySelector('[data-google-chips]').textContent.includes('<svg'), 'selected filename must display safely');
    await panel.setConversation('chat2');
    check(!root.querySelector('[data-google-chips]').textContent, 'global grant must not select another chat');
    const slow = panel.setConversation('slow'); await tick();
    await panel.setConversation('chat2'); oldResolve({ items: files }); await slow;
    check(!root.querySelector('[data-google-chips]').textContent, 'late previous-chat response must be ignored');
    await panel.setConversation('chat1'); await panel.open();
    root.querySelector('input[type=checkbox]').checked = false;
    root.querySelector('[data-google-save]').click(); await tick(); await tick();
    check(saved.chat1.length === 0, 'unselect must persist');
    status = 'reconnect'; await panel.setConversation('chat1');
    check(root.querySelector('[data-google-choose]').disabled, 'reconnect must not resurrect old selections');
    const page = document.createElement('main');
    page.innerHTML = '<button data-google-connect></button><button data-google-pick></button><button data-google-disconnect></button><p data-google-account></p><p data-google-status></p><div data-google-files></div>';
    document.body.append(page);
    let configured = false;
    const pageRequest = async url => url === '/api/connections/google'
      ? { configured, connections: configured ? [{ id: 'connection-a', status }] : [] }
      : { items: [] };
    const pagePanel = await window.GoogleConnectors.initPage(page, pageRequest);
    check(page.querySelector('[data-google-connect]').disabled, 'unconfigured OAuth must disable connect');
    configured = true; status = 'disconnected'; await pagePanel.refresh();
    check(page.querySelector('[data-google-pick]').disabled, 'disconnected account must disable Picker');
    status = 'connected'; await pagePanel.refresh();
    check(!page.querySelector('[data-google-pick]').disabled && page.querySelector('[data-google-files]').textContent, 'connected account must show no-file state');
    let pickerToken, callback, disposed = false;
    window.google = { picker: { Action: { PICKED: 'picked', CANCEL: 'cancel' }, Feature: { MULTISELECT_ENABLED: 'multi' },
      DocsView: class { setIncludeFolders() { return this; } setSelectFolderEnabled() { return this; } setMimeTypes() { return this; } },
      PickerBuilder: class {
        setDeveloperKey() { return this; } setAppId() { return this; } setOAuthToken(token) { pickerToken = token; return this; }
        setOrigin() { return this; } addView() { return this; } enableFeature() { return this; } setCallback(fn) { callback = fn; return this; }
        build() { return { setVisible(value) { if (value) setTimeout(() => callback({ action: 'picked', docs: [{ id: 'sheet-a' }] }), 0); }, dispose() { disposed = true; pickerToken = null; } }; }
      }
    } };
    const picked = await window.GoogleConnectors.pickFiles('connection-a', async () => ({ access_token: 'picker-token-canary', api_key: 'public-key', app_id: '123', expires_at: new Date(Date.now() + 60000).toISOString() }));
    check(picked[0] === 'sheet-a' && disposed && pickerToken === null, 'Picker must dispose and release browser token');
    check(!document.body.textContent.includes('picker-token-canary'), 'browser token must not enter rendered content');
    return 'PASS';
  }.toString()})()` });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  assert.equal(result.result.value, 'PASS');
  console.log('PASS: Google selection, unselect, account reconnect, XSS and conversation races');
}).catch(error => { console.error(error); process.exitCode = 1; });
