const http = require('node:http');
const assert = require('node:assert/strict');
const withBrowser = require('./browser_cdp.cjs');

function listen(server) {
  return new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
}

(async () => {
  let received;
  const observed = new Promise(resolve => { received = resolve; });
  const picker = http.createServer((req, res) => {
    received(req.headers.referer);
    res.end('Synthetic Picker');
  });
  const page = http.createServer((req, res) => {
    res.setHeader('Referrer-Policy', process.env.PICKER_TEST_REFERRER_POLICY);
    res.setHeader('Content-Type', 'text/html');
    res.end(`<iframe src="http://127.0.0.1:${picker.address().port}/picker"></iframe>`);
  });
  await listen(picker);
  await listen(page);
  try {
    const origin = `http://127.0.0.1:${page.address().port}`;
    await withBrowser(async send => {
      await send('Page.navigate', { url: origin + '/connectors?code=private-code&state=private-state' });
      let timer;
      const referrer = await Promise.race([
        observed,
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('No iframe request')), 10000); }),
      ]).finally(() => clearTimeout(timer));
      assert.equal(referrer, origin + '/', 'Picker must receive only the allowed origin, not no-referrer or URL secrets');
    });
  } finally {
    page.closeAllConnections(); picker.closeAllConnections();
    await Promise.all([new Promise(resolve => page.close(resolve)), new Promise(resolve => picker.close(resolve))]);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
