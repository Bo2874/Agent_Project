const fs = require('node:fs');
const path = require('node:path');
const withBrowser = require('./browser_cdp.cjs');
withBrowser(async send => {
  const source = path.join(__dirname, '../frontend/static/automations.js');
  if (fs.existsSync(source)) await send('Runtime.evaluate', { expression: fs.readFileSync(source, 'utf8') });
  const result = await send('Runtime.evaluate', { awaitPromise: true, returnByValue: true, expression: `(${async function () {
    const check = (v, message) => { if (!v) throw new Error(message); };
    const until = async fn => { for (let n = 0; n < 200; n++) { if (fn()) return; await new Promise(r => setTimeout(r, 10)); } throw new Error('UI did not settle: ' + root.querySelector('[data-auto-status]').textContent + ' / ' + root.querySelector('[data-form-status]').textContent); };
    check(window.AutomationWorkspace?.mount, 'Automation UI must exist');
    const root = document.createElement('main'); document.body.append(root);
    let auto = null, run = null, opened = 0, delayPreview = true, releasePreview, googleInputs = false; const writes = [];
    window.confirm = () => true;
    window.ArtifactWorkspace = { onOpen: () => { opened += 1; } };
    window.renderArtifactCards = (parent, refs) => { for (const ref of refs) { const b = document.createElement('button'); b.textContent = ref.name; b.dataset.outputFile = ''; b.onclick = () => window.ArtifactWorkspace.onOpen(ref.version_id); parent.append(b); } };
    const requestJson = async (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : null;
      if (options.method) writes.push({ url, body });
      if (url === '/api/automations/inputs') return { connections: googleInputs ? [{ id: 'g-conn', account_name: 'Google fixture' }] : [], google_files: googleInputs ? [{ connection_id: 'g-conn', file_id: 'sheet-a', name: 'Selected Sheet', mime_type: 'application/vnd.google-apps.spreadsheet' }] : [], artifacts: [] };
      if (url === '/api/automations/inputs/google/g-conn/sheet-a/tabs') return { items: [] };
      if (url === '/api/automations/schedules/preview') { const preview = { schedule: { kind: 'daily', timezone: body.timezone, local_time: '08:00:00', weekday: null, at: null }, next_run_at: '2026-11-08T01:00:00Z' }; if (delayPreview) return new Promise(resolve => { releasePreview = () => resolve(preview); }); return preview; }
      if (url === '/api/automations' && options.method === 'POST') { auto = { ...body, id: 'auto-1', enabled: false, revision_id: 'rev-1', revision_number: 1, next_run_at: null }; return auto; }
      if (url === '/api/automations/auto-1' && options.method === 'PATCH') { auto = { ...auto, ...body, revision_number: 2 }; return auto; }
      if (url === '/api/automations') return { items: auto ? [auto] : [] };
      if (url === '/api/automations/auto-1') return auto;
      if (url === '/api/automations/auto-1/runs') return { items: run ? [run] : [] };
      if (url === '/api/automations/auto-1/run-now') { run = { id: 'run-1', automation_id: auto.id, status: 'queued', trigger: 'manual', created_at: '2026-11-07T01:00:00Z', result: {}, revision_number: 1 }; return run; }
      if (url === '/api/automations/auto-1/enabled') { auto.enabled = body.enabled; auto.next_run_at = body.enabled ? '2026-11-08T01:00:00Z' : null; if (body.stop_active && run) run.status = 'cancelling'; return auto; }
      if (url === '/api/automations/runs/run-1') return run;
      if (url === '/api/automations/runs/run-1/stop') { run.status = 'cancelling'; return run; }
      if (url === '/api/automations/runs/run-1/close-unknown') { check(body.confirmed === true, 'Reconciliation requires explicit confirmation'); run.status = 'failed'; return run; }
      throw new Error('Unexpected request ' + url);
    };
    const app = window.AutomationWorkspace.mount(root, { requestJson, poll: false, events: false });
    await app.ready;
    root.querySelector('[data-create-automation]').click();
    await until(() => root.querySelector('[data-edit-dialog]').open);
    root.querySelector('[name="name"]').value = '<img src=x onerror=alert(1)> report';
    root.querySelector('[name="instruction"]').value = 'Research and report';
    root.querySelector('[data-review-automation]').click();
    await until(() => !!releasePreview);
    root.querySelector('[name="timezone"]').value = 'UTC';
    root.querySelector('[name="timezone"]').dispatchEvent(new Event('input', { bubbles: true }));
    releasePreview(); await new Promise(resolve => setTimeout(resolve, 20));
    check(root.querySelector('[data-save-automation]').hidden, 'A stale review must not authorize a changed schedule');
    delayPreview = false;
    root.querySelector('[name="timezone"]').value = 'Asia/Ho_Chi_Minh';
    root.querySelector('[data-review-automation]').click();
    await until(() => !root.querySelector('[data-save-automation]').hidden);
    check(!writes.some(w => w.url === '/api/automations'), 'Preview must not register a task');
    root.querySelector('[data-save-automation]').click();
    await until(() => !!root.querySelector('[data-run-now]'));
    check(!root.querySelector('img'), 'Task names must be text');
    check(!auto.enabled, 'New draft must be disabled');
    auto.artifact_version_ids = ['old-version']; auto.artifact_inputs = [{ version_id: 'old-version', name: 'Original.xlsx', version_number: 1 }];
    auto.grants = [{ connection_id: 'old-connection', action: 'create_doc' }];
    await app.refresh();
    root.querySelector('[data-automation-detail] .automation-heading button').click();
    await until(() => root.querySelector('[data-edit-dialog]').open);
    check(root.querySelector('[name="artifact_version"][value="old-version"]')?.checked, 'Editing must preserve an older selected artifact');
    root.querySelector('[data-review-automation]').click();
    await until(() => !root.querySelector('[data-save-automation]').hidden);
    root.querySelector('[data-save-automation]').click();
    await until(() => !root.querySelector('[data-edit-dialog]').open);
    check(auto.artifact_version_ids.includes('old-version'), 'Saving must retain pinned artifact inputs');
    check(auto.grants.some(g => g.connection_id === 'old-connection' && g.action === 'create_doc'), 'Unavailable account grants must not disappear silently');
    root.querySelector('[data-run-now]').click();
    root.querySelector('[data-run-now]').click();
    await until(() => run && root.textContent.includes('Đang xếp hàng'));
    check(writes.filter(w => w.url.endsWith('/run-now')).length === 1, 'Rapid clicks must issue one manual request');
    root.querySelector('[data-toggle-schedule]').click();
    await until(() => auto.enabled);
    run.status = 'running'; await app.refresh();
    root.querySelector('[data-toggle-schedule]').click();
    await until(() => root.querySelector('[data-pause-keep]'));
    root.querySelector('[data-pause-keep]').click();
    await until(() => !auto.enabled);
    check(run.status === 'running', 'Pause only must preserve current run');
    const pause = writes.findLast(w => w.url.endsWith('/enabled'));
    check(pause.body.stop_active === false, 'Pause choice must reach the server');
    root.querySelector('[data-stop-run]').click();
    await until(() => root.textContent.includes('Đang yêu cầu dừng'));
    run.status = 'needs_reconciliation'; await app.refresh();
    check(root.querySelector('[data-close-unknown]'), 'Unknown runs must stay actionable after reload');
    run.result = { artifact_results: [{ name: 'Result.xlsx', version_id: 'v1', artifact_id: 'a1' }] }; await app.refresh();
    check(opened === 0, 'New result must not open preview');
    root.querySelector('[data-output-file]').click(); check(opened === 1, 'Click must open selected output');
    root.querySelector('[data-close-unknown]').click();
    await until(() => run.status === 'failed');
    googleInputs = true; auto.grants = [{ connection_id: 'g-conn', file_id: 'sheet-a', action: 'edit_sheet' }];
    await app.refresh(); root.querySelector('[data-automation-detail] .automation-heading button').click();
    await until(() => root.querySelector('[value="edit_sheet"]')?.checked);
    check(root.querySelector('[aria-label="Tab Google Sheets"]').hidden, 'Native whole-file edit must not require a tab/range');
    root.querySelector('[data-review-automation]').click();
    await until(() => !root.querySelector('[data-save-automation]').hidden);
    root.querySelector('[data-save-automation]').click();
    await until(() => !root.querySelector('[data-edit-dialog]').open);
    const native = auto.grants.find(g => g.action === 'edit_sheet');
    check(native && !('range_a1' in native) && !('sheet_id' in native), 'Whole-file native grant must save without inventing an A1 range');
    app.destroy(); root.remove();
    return 'PASS: draft review, Run now, pause choices, stop, unknown, XSS and explicit file preview';
  }} )()` });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  console.log(result.result.value);
}).catch(error => { console.error(error); process.exitCode = 1; });
