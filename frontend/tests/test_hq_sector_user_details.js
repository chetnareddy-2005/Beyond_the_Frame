const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const frontend = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(frontend, 'app.js'), 'utf8');

function functionSource(name, nextName) {
  const start = app.indexOf(`function ${name}(`);
  const nextMarker = nextName.includes(' ') ? nextName : `function ${nextName}(`;
  const end = app.indexOf(nextMarker, start);
  assert.notEqual(start, -1, `${name} should exist`);
  assert.notEqual(end, -1, `${nextName} should follow ${name}`);
  return app.slice(start, end);
}

function render(functionName, nextName, args) {
  const context = {
    escapeHTML(value) {
      return String(value ?? '').replace(/[&<>"']/g, (character) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
      })[character]);
    },
  };
  vm.runInNewContext(`${functionSource(functionName, nextName)}\nthis.render = ${functionName};`, context);
  return context.render(...args);
}

function renderPagedList(sectorCount, page = 1) {
  const elements = new Map([
    ['#hq-users-list', { innerHTML: '' }],
    ['#hq-users-pagination', { innerHTML: '', hidden: false }],
  ]);
  const context = {
    escapeHTML(value) {
      return String(value ?? '').replace(/[&<>"']/g, (character) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
      })[character]);
    },
    $: (selector) => elements.get(selector),
  };
  const source = [
    'const HQ_USERS_PAGE_SIZE = 4;',
    `let hqUsersPage = ${page};`,
    functionSource('renderHeadquartersSectorSummary', 'renderHeadquartersSectorDetails'),
    functionSource('renderHeadquartersUsersList', 'async function loadHeadquartersUsers'),
    'this.renderList = renderHeadquartersUsersList;',
    'this.currentPage = () => hqUsersPage;',
  ].join('\n');
  vm.runInNewContext(source, context);
  const sectors = Array.from({ length: sectorCount }, (_, index) => ({
    sector_id: `S-${index + 1}`,
    sector_name: `Sector ${index + 1}`,
    head_phone: '+15555550111',
    members: [{ member_role: 'head', full_name: `Head ${index + 1}` }],
  }));
  context.renderList(sectors, []);
  return { elements, context, sectors };
}

const sector = {
  request_id: 'request-1',
  sector_id: 'S-01',
  sector_name: 'North Sector',
  sector_address: 'Synthetic HQ address',
  head_phone: '+15555550111',
  head_email: 'head@example.test',
  members: [
    { full_name: 'Synthetic Head', member_role: 'head', rank: 'Captain', member_id: '1234567', document_available: true, document_key: 'head' },
    { full_name: 'Synthetic Technician', member_role: 'technical', rank: 'Technician', member_id: '7654321', document_available: false },
  ],
};

test('approved sector summary is compact and keeps decommissioning separate', () => {
  const markup = render('renderHeadquartersSectorSummary', 'renderHeadquartersSectorDetails', [sector, null]);
  assert.match(markup, /North Sector/);
  assert.match(markup, /S-01/);
  assert.match(markup, /Synthetic Head/);
  assert.match(markup, /\+15555550111/);
  assert.match(markup, /View more details/);
  assert.match(markup, /data-decommission-sector="S-01"/);
  assert.doesNotMatch(markup, /registry-member|Member ID|document_available/);
});

test('details render existing account and roster data without member IDs or passwords', () => {
  const markup = render('renderHeadquartersSectorDetails', 'async function loadHeadquartersUsers', [
    sector,
    { username: 'shared@example.test', must_change_password: true, password: 'must-not-render' },
  ]);
  assert.match(markup, /Synthetic HQ address/);
  assert.match(markup, /head@example\.test/);
  assert.match(markup, /shared@example\.test/);
  assert.match(markup, /Password update required/);
  assert.match(markup, /Synthetic Head/);
  assert.match(markup, /Synthetic Technician/);
  assert.match(markup, /Captain/);
  assert.match(markup, /Technician/);
  assert.match(markup, /data-registry-document="request-1" data-registry-member="head"/);
  assert.doesNotMatch(markup, /1234567|7654321|must-not-render|Member ID/);
});

test('details popup is a labeled native dialog with explicit close control', () => {
  assert.match(html, /<dialog id="hq-sector-details-dialog"[^>]*aria-labelledby="hq-sector-details-title"/);
  assert.match(html, /id="close-hq-sector-details"[^>]*aria-label="Close sector details"/);
  assert.match(app, /hqSectorDetailsDialog\?\.addEventListener\('click', \(event\) => \{\s*if \(event\.target === hqSectorDetailsDialog\) hqSectorDetailsDialog\.close\(\);/);
  assert.match(app, /hqSectorDetailsDialog\?\.addEventListener\('close', \(\) => \{\s*if \(hqSectorDetailsOpener\?\.isConnected\) hqSectorDetailsOpener\.focus\(\{ preventScroll: true \}\);/);
  assert.match(app, /hqSectorDetailsDialog\.showModal\(\)/);
});

test('approved sector list paginates in groups of four for empty, boundary, and multi-page counts', () => {
  for (const [count, expectedPages, expectedVisible] of [
    [0, 1, 0], [1, 1, 1], [4, 1, 4], [5, 2, 4], [10, 3, 4],
  ]) {
    const { elements } = renderPagedList(count);
    const listMarkup = elements.get('#hq-users-list').innerHTML;
    const pagination = elements.get('#hq-users-pagination');
    assert.equal((listMarkup.match(/data-view-sector-details=/g) || []).length, expectedVisible, `${count} roster(s) visible per page`);
    assert.equal(pagination.hidden, count <= 4);
    if (count > 4) {
      assert.match(pagination.innerHTML, new RegExp(`Page 1 of ${expectedPages}`));
      assert.match(pagination.innerHTML, /data-hq-users-page="previous" disabled/);
      assert.doesNotMatch(pagination.innerHTML, /data-hq-users-page="next" disabled/);
    }
  }
});

test('pagination advances and clamps to the last valid page after roster data shrinks', () => {
  const { elements, context, sectors } = renderPagedList(10, 3);
  assert.equal(context.currentPage(), 3);
  assert.match(elements.get('#hq-users-pagination').innerHTML, /Page 3 of 3/);
  context.renderList(sectors.slice(0, 5), []);
  assert.equal(context.currentPage(), 2);
  assert.match(elements.get('#hq-users-pagination').innerHTML, /Page 2 of 2/);
  assert.match(elements.get('#hq-users-pagination').innerHTML, /data-hq-users-page="next" disabled/);
});
