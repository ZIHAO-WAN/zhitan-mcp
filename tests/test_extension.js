const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function check(executeScript) {
  const status = {textContent: '', style: {}};
  let click;
  const context = {
    document: {getElementById: id => id === 'status' ? status : {addEventListener: (_, handler) => { if (id === 'check') click = handler; }}},
    chrome: {tabs: {query: async () => [{id: 1, url: 'https://www.zhipin.com/web/geek/jobs'}]}, scripting: {executeScript}},
  };
  vm.runInNewContext(fs.readFileSync('browser-extension/inspect.js', 'utf8') + fs.readFileSync('browser-extension/popup.js', 'utf8'), context);
  await click();
  return status.textContent;
}

(async () => {
  assert.match(await check(async () => [{result: {state: 'authenticated', jobLinks: 2, jobCards: 2, jobs: [{}, {}], hasDescription: false}}]), /可提取职位：2/);
  assert.match(await check(async () => { throw new Error('Cannot access contents of url'); }), /诊断 B/);
  assert.match(await check(async () => { throw new Error('other'); }), /诊断 C/);
  console.log('Extension popup checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
