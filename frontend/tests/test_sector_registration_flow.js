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

function validationHarness({ fields = {}, files = {}, selectors = {}, selectedMethod = '', lockedMethod = '' } = {}) {
  const elements = new Map();
  const error = { textContent: '', hidden: true };
  for (const [id, value] of Object.entries(fields)) {
    elements.set(`#${id}`, { value, files: files[id] ? [files[id]] : [] });
  }
  for (const [id, file] of Object.entries(files)) {
    if (!elements.has(`#${id}`)) elements.set(`#${id}`, { value: '', files: [file] });
  }
  for (const id of ['registry-sector-address', 'registry-base-lat', 'registry-base-lon', 'registry-location-method', 'registry-resolved-address', 'registry-location-token']) {
    if (!elements.has(`#${id}`)) elements.set(`#${id}`, { value: '' });
  }
  elements.get('#registry-location-method').value = lockedMethod;
  const document = {
    getElementById(id) {
      return elements.get(`#${id}`) || { value: '' };
    },
    querySelector(selector) {
      if (selector === '[name="registry-location-source"]:checked') {
        return selectedMethod ? { value: selectedMethod } : null;
      }
      return selectors[selector] || null;
    },
    querySelectorAll() { return []; },
  };
  const context = {
    document,
    $: (selector) => selector === '#registry-submit-error' ? error : elements.get(selector),
    selectedRegistryLocationMethod: () => selectedMethod,
    registryStepError(message) { error.textContent = message; error.hidden = false; },
    basecampPinMethod: lockedMethod,
  };
  return { context, elements, error };
}

test('registration panels and progress labels follow the requested three-step order', () => {
  const indicators = [...html.matchAll(/data-reg-step-indicator="([123])"><span>\d<\/span><strong>([^<]+)<\/strong>/g)]
    .map((match) => match[2]);
  assert.deepEqual(indicators, ['Sector head', 'Technical team', 'Sector address']);
  const steps = [...html.matchAll(/<section class="registry-step[^"]*" data-reg-step="([123])"[\s\S]*?<\/section>/g)];
  assert.deepEqual(steps.map((match) => match[1]), ['1', '2', '3']);
  assert.doesNotMatch(steps[0][0], /registry-sector-address|registry-basecamp-map|registry-location-source/);
  assert.match(steps[0][0], /registry-head-name[\s\S]*registry-head-id[\s\S]*registry-head-rank[\s\S]*registry-head-phone[\s\S]*registry-head-email[\s\S]*registry-head-document/);
  assert.match(steps[1][0], /registry-tech1-name[\s\S]*registry-tech1-id[\s\S]*registry-tech1-rank[\s\S]*registry-tech1-document/);
  assert.match(steps[1][0], /registry-tech2-name[\s\S]*registry-tech2-id[\s\S]*registry-tech2-rank[\s\S]*registry-tech2-document/);
  assert.match(steps[2][0], /registry-sector-address[\s\S]*DEVICE_HARDWARE[\s\S]*ADDRESS_GEOCODING[\s\S]*MANUAL_MAP_CLICK[\s\S]*registry-basecamp-map[\s\S]*registry-base-lat[\s\S]*registry-base-lon[\s\S]*registry-basecamp-status/);
  assert.match(steps[2][0], /data-reg-back[\s\S]*Submit for HQ review/);
  assert.match(html, /id="registry-confirmation"[^>]*hidden/);
});

test('head step validation does not require address or location data', () => {
  const fields = {
    'registry-sector': 'Sector 1 (S1)',
    'registry-head-name': 'Synthetic Head',
    'registry-head-id': '1234567',
    'registry-head-rank': 'Officer',
    'registry-head-phone': '+15555550123',
    'registry-head-email': 'head@example.test',
  };
  const file = { size: 64, type: 'image/png' };
  const { context } = validationHarness({ fields, files: { 'registry-head-document': file } });
  vm.runInNewContext(
    `${functionSource('registryHeadDetailsValid', 'validateRegistryHeadStep')}${functionSource('validateRegistryHeadStep', 'validateRegistryTechnicalStep')}\nthis.validate = validateRegistryHeadStep;`,
    context,
  );
  assert.equal(context.validate(), true);
});

test('address step requires a non-empty address and a selected matching locked pin', () => {
  const source = functionSource('validateRegistryAddressStep', 'async function encodeRegistryDocument');
  const missingAddress = validationHarness({
    fields: { 'registry-base-lat': '10', 'registry-base-lon': '20', 'registry-location-method': 'MANUAL_MAP_CLICK' },
    selectedMethod: 'MANUAL_MAP_CLICK',
    lockedMethod: 'MANUAL_MAP_CLICK',
  });
  vm.runInNewContext(`${source}\nthis.validate = validateRegistryAddressStep;`, missingAddress.context);
  assert.equal(missingAddress.context.validate(), false);
  assert.match(missingAddress.error.textContent, /sector address/i);

  const missingPin = validationHarness({ fields: { 'registry-sector-address': 'Synthetic location' } });
  vm.runInNewContext(`${source}\nthis.validate = validateRegistryAddressStep;`, missingPin.context);
  assert.equal(missingPin.context.validate(), false);
  assert.match(missingPin.error.textContent, /lock its/i);
});

test('technical step requires complete member details, unique seven-digit IDs, and supported files', () => {
  const source = functionSource('validateRegistryTechnicalStep', 'validateRegistryAddressStep');
  const selectors = {};
  for (const [role, name, id, rank] of [
    ['technical1', 'Synthetic Tech One', '2345678', 'Technician'],
    ['technical2', 'Synthetic Tech Two', '3456789', 'Technician'],
  ]) {
    selectors[`[data-reg-member="${role}"][data-reg-field="full_name"]`] = { value: name };
    selectors[`[data-reg-member="${role}"][data-reg-field="member_id"]`] = { value: id };
    selectors[`[data-reg-member="${role}"][data-reg-field="rank"]`] = { value: rank };
  }
  const valid = validationHarness({
    fields: { 'registry-head-id': '1234567' },
    files: {
      'registry-tech1-document': { size: 64, type: 'application/pdf' },
      'registry-tech2-document': { size: 64, type: 'image/jpeg' },
    },
    selectors,
  });
  vm.runInNewContext(`${source}\nthis.validate = validateRegistryTechnicalStep;`, valid.context);
  assert.equal(valid.context.validate(), true);

  selectors['[data-reg-member="technical2"][data-reg-field="member_id"]'].value = '1234567';
  assert.equal(valid.context.validate(), false);
  assert.match(valid.error.textContent, /unique/i);
});

test('manual and geocoded locations require a current locked pin and geocoding proof', () => {
  const source = functionSource('validateRegistryAddressStep', 'async function encodeRegistryDocument');
  const commonFields = {
    'registry-sector-address': 'Synthetic sector address',
    'registry-base-lat': '17.38',
    'registry-base-lon': '78.49',
  };
  const manual = validationHarness({
    fields: { ...commonFields, 'registry-location-method': 'MANUAL_MAP_CLICK' },
    selectedMethod: 'MANUAL_MAP_CLICK',
    lockedMethod: 'MANUAL_MAP_CLICK',
  });
  vm.runInNewContext(`${source}\nthis.validate = validateRegistryAddressStep;`, manual.context);
  assert.equal(manual.context.validate(), true);

  const unresolved = validationHarness({
    fields: { ...commonFields, 'registry-location-method': 'ADDRESS_GEOCODING' },
    selectedMethod: 'ADDRESS_GEOCODING',
    lockedMethod: 'ADDRESS_GEOCODING',
  });
  vm.runInNewContext(`${source}\nthis.validate = validateRegistryAddressStep;`, unresolved.context);
  assert.equal(unresolved.context.validate(), false);
  assert.match(unresolved.error.textContent, /resolve the current sector address/i);

  unresolved.elements.get('#registry-resolved-address').value = commonFields['registry-sector-address'];
  unresolved.elements.get('#registry-location-token').value = 'synthetic-verification-token';
  assert.equal(unresolved.context.validate(), true);
});

test('step three reveals and resizes the map; successful submission uses a separate confirmation', () => {
  const stepStart = app.indexOf('function setRegistryStep(');
  const stepEnd = app.indexOf('function registryStepError(', stepStart);
  assert.match(app.slice(stepStart, stepEnd), /registryStep===3[\s\S]*initializeBasecampPicker\(\)[\s\S]*invalidateSize\(\)/);
  assert.match(app, /event\.preventDefault\(\);if\(registryStep!==3\|\|!validateRegistryAddressStep\(\)\)return;/);
  const requestStart = app.indexOf("JSON.stringify({sector_id:$('#registry-sector').value");
  const requestEnd = app.indexOf('})});', requestStart);
  assert.notEqual(requestStart, -1);
  assert.notEqual(requestEnd, -1);
  const payload = app.slice(requestStart, requestEnd);
  for (const field of [
    'sector_id', 'sector_address', 'base_camp_lat', 'base_camp_lon', 'chosen_lat', 'chosen_lon',
    'resolved_address_string', 'location_verification_token', 'location_capture_method',
    'head_phone', 'head_email', 'members', 'documents',
  ]) {
    assert.match(payload, new RegExp(`${field}(?=:|[,}]|$)`), `${field} remains in the existing request payload`);
  }
  assert.match(app, /registry-form-stepper'\)\.hidden=true;\$\('#sector-registration-form'\)\.hidden=true;\$\('#registry-confirmation'\)\.hidden=false/);
  assert.match(app, /querySelectorAll\('\[data-reg-back\]'\)\.forEach/);
});
