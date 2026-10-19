const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class FakeElement {
  constructor(tagName = 'div') {
    this.tagName = tagName;
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.buttons = new Map();
    this.classList = { add() {}, remove() {} };
    this.disabled = false;
  }

  set innerHTML(value) {
    this.html = value;
    this.children = [];
    this.buttons = new Map();
    for (const name of ['viewHistoryBtn', 'deleteHistoryBtn']) {
      if (value.includes(name)) this.buttons.set(name, new FakeElement('button'));
    }
    if (value.includes('<button') && !this.buttons.size) {
      this.buttons.set('button', new FakeElement('button'));
    }
  }

  get innerHTML() { return this.html || ''; }
  setAttribute(name, value) { this.attributes.set(name, value); }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  appendChild(child) { this.children.push(child); return child; }
  prepend(child) { this.children.unshift(child); return child; }
  querySelector(selector) { return this.buttons.get(selector.replace('.', '')) || null; }
  focus() {}
  click() { return this.listeners.get('click')?.({ currentTarget: this }); }
}

function setupHistory() {
  const storage = new Map([['authUser', JSON.stringify({ email: 'student@example.test' })]]);
  const localStorage = {
    getItem: (key) => storage.get(key) || null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key)
  };
  const requests = [];
  const savedReport = {
    analysis_id: 42,
    history_saved: true,
    uploaded_resume_name: 'resume.pdf',
    matching_percentage: 76,
    resume_summary: { text: 'Saved on another device' }
  };
  let serverRows = [{
    id: 42,
    uploaded_resume_name: 'resume.pdf',
    target_role: 'Developer',
    matching_percentage: 76,
    created_at: '2026-10-20T10:00:00Z'
  }];
  const fetch = async (url, options = {}) => {
    requests.push({ url, options });
    const route = new URL(url, 'https://app.example.test').pathname;
    let payload;
    if (route === '/auth/csrf-token') {
      payload = { csrf_token: 'test-csrf-token' };
    } else if (route === '/auth/session') {
      payload = { authenticated: true, user: { email: 'student@example.test' } };
    } else if (route === '/analyses' && options.method === 'DELETE') {
      serverRows = [];
      payload = { deleted_count: 1 };
    } else if (route === '/analyses') {
      payload = { analyses: serverRows };
    } else if (route === '/analyses/42' && options.method === 'DELETE') {
      serverRows = [];
      payload = { deleted: true, id: 42 };
    } else if (route === '/analyses/42') {
      payload = { analysis: savedReport };
    } else {
      throw new Error(`Unexpected request: ${url}`);
    }
    return { ok: true, json: async () => payload };
  };
  const document = {
    activeElement: null,
    body: new FakeElement('body'),
    createElement: (tagName) => new FakeElement(tagName),
    contains: () => false,
    addEventListener() {}
  };
  const window = {
    fetch,
    APP_CONFIG: { apiBaseUrl: 'https://api.example.test' },
    location: { protocol: 'https:', origin: 'https://app.example.test', href: 'https://app.example.test/' },
    confirm: () => true
  };
  const context = { window, document, localStorage, fetch: (...args) => window.fetch(...args), URL, Headers };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, '..', 'app_common.js'), 'utf8'),
    context,
    { filename: 'app_common.js' }
  );
  const list = new FakeElement();
  const modal = new FakeElement();
  const clearButton = new FakeElement('button');
  let openedReport;
  const controller = window.CareerNavigatorCommon.createHistoryModal({
    list, modal, clearButton,
    apiBaseUrl: 'https://api.example.test',
    onView: (item) => { openedReport = item.full; }
  });
  return { controller, list, requests, storage, openedReport: () => openedReport };
}

test('history loads a database report on a second device, opens it, and deletes it', async () => {
  const { controller, list, requests, storage, openedReport } = setupHistory();
  assert.equal(storage.has('analysisHistory_student@example.test'), false);

  await controller.render();
  const card = list.children.find((element) => element.querySelector('.viewHistoryBtn'));
  assert.ok(card, 'saved database report should be visible without local history');
  assert.match(card.innerHTML, /resume\.pdf - Developer/);
  const historyRequests = () => requests.filter(({ url }) => url.startsWith('https://api.example.test/analyses'));
  assert.equal(historyRequests()[0].url, 'https://api.example.test/analyses');
  assert.equal(historyRequests()[0].options.credentials, 'include');

  await card.querySelector('.viewHistoryBtn').click();
  assert.equal(openedReport().analysis_id, 42);
  assert.equal(openedReport().resume_summary.text, 'Saved on another device');
  assert.equal(historyRequests()[1].url, 'https://api.example.test/analyses/42');
  assert.equal(historyRequests()[1].options.credentials, 'include');

  await card.querySelector('.deleteHistoryBtn').click();
  await controller.render();
  const deleteRequest = requests.find(({ options }) => options.method === 'DELETE');
  assert.equal(deleteRequest.url, 'https://api.example.test/analyses/42');
  assert.equal(deleteRequest.options.credentials, 'include');
  assert.equal(deleteRequest.options.headers.get('X-CSRF-Token'), 'test-csrf-token');
  assert.equal(list.children.some((element) => element.querySelector('.viewHistoryBtn')), false);
});
