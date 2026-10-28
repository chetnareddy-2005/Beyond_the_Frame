const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const frontend = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(frontend, 'app.js'), 'utf8');

function functionSource(name, nextMarker) {
  const start = app.indexOf(`function ${name}(`);
  const end = app.indexOf(nextMarker, start);
  assert.notEqual(start, -1, `${name} should exist`);
  assert.notEqual(end, -1, `${nextMarker} should follow ${name}`);
  return app.slice(start, end);
}

function evaluate(functionName, nextMarker, expression, globals = {}) {
  const context = { ...globals };
  vm.runInNewContext(`${functionSource(functionName, nextMarker)}\nthis.result = ${expression};`, context);
  return context.result;
}

test('sector login selector is present, hidden by default, and submits selected sector only for sector role', () => {
  assert.match(html, /id="login-sector-selector" hidden/);
  assert.match(html, /id="login-sector-number"[^>]*disabled/);
  assert.match(app, /api\('\/api\/sector-registry\/approved-sectors'\)/);
  assert.match(app, /account_type: accountType, \.\.\.\(accountType === 'sector' \? \{ sector_number: sectorNumber \} : \{\}\)/);
  assert.match(app, /No active, HQ-approved sectors are available for Sector Account sign-in\./);
});

test('sector options accept only backend sector identifiers and names without inventing choices', () => {
  const sectors = [
    { id: 'S1', name: 'North Sector' },
    { id: '', name: 'Missing ID' },
    { id: 'S3', name: '' },
  ];
  const result = evaluate('renderLoginSectorOptions', 'async function updateLoginSectorSelector', 'renderLoginSectorOptions(sectors)', { sectors });
  assert.deepEqual(JSON.parse(JSON.stringify(result)), [{ id: 'S1', name: 'North Sector' }]);
});

test('sector login with no approved sectors explains the empty state and stays disabled', async () => {
  const elements = {
    '#login-sector-selector': { hidden: false },
    '#login-sector-number': {
      disabled: false,
      options: [],
      replaceChildren(...options) { this.options = options; },
      add(option) { this.options.push(option); },
    },
    '#login-sector-status': { textContent: '' },
    '#login-submit': { disabled: false },
    '#login-account-type': { value: 'sector' },
  };
  class FakeOption {
    constructor(text, value) {
      this.text = text;
      this.value = value;
    }
  }
  const context = {
    $: (selector) => elements[selector],
    api: async () => ({ sectors: [] }),
    renderLoginSectorOptions: (sectors) => sectors,
    activeLoginSectors: [],
    loginSectorLoadRequest: 0,
    Option: FakeOption,
  };
  vm.runInNewContext(
    `async ${functionSource('updateLoginSectorSelector', "$('#login-account-type').addEventListener")}\nthis.result = updateLoginSectorSelector();`,
    context,
  );
  await context.result;
  assert.equal(elements['#login-sector-selector'].hidden, false);
  assert.equal(elements['#login-sector-number'].disabled, true);
  assert.equal(elements['#login-sector-number'].options.length, 1);
  assert.match(elements['#login-sector-status'].textContent, /No active, HQ-approved sectors/);
  assert.equal(elements['#login-submit'].disabled, true);
});

test('first-time password rule accepts valid boundary examples and rejects invalid examples', () => {
  const check = (password) => evaluate('isValidFirstPassword', 'let forcedPasswordCurrent', 'isValidFirstPassword(password)', { password });
  for (const password of ['Abcdef1!', 'North-2026!', 'A1b!2345678']) assert.equal(check(password), true);
  for (const password of ['Ab1!', 'Abcdefghijkl1!', 'abcdefgh1!', 'ABCDEFGH1!', 'Abcdefgh!', 'Abcdefg1', 'Abcdef1 ']) {
    assert.equal(check(password), false, `password length ${password.length}`);
  }
  assert.match(html, /Use 8–12 characters, including at least one uppercase letter, one lowercase letter, one number, and one symbol\./);
  assert.match(app, /if \(!isValidFirstPassword\(newPassword\)\)[\s\S]*?else if \(confirmation !== newPassword\)/);
});

test('HQ Admin login continues without a sector selector value', () => {
  assert.match(html, /<option value="headquarters_admin">Headquarters Admin<\/option>/);
  assert.match(app, /const sectorNumber = accountType === 'sector' \? \$\('#login-sector-number'\)\.value : '';/);
  assert.match(app, /\(accountType === 'sector' \? \{ sector_number: sectorNumber \} : \{\}\)/);
});
