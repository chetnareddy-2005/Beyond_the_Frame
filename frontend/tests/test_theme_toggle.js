const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const frontend = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(frontend, 'app.js'), 'utf8');
const styles = fs.readFileSync(path.join(frontend, 'styles.css'), 'utf8');

function themeHarness(initialTheme = 'dark') {
  const themeToggle = {
    attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    title: '',
  };
  const icons = Object.fromEntries(['#theme-toggle-sun', '#theme-toggle-moon'].map((selector) => [
    selector,
    {
      hidden: selector === '#theme-toggle-moon',
      toggleAttribute(name, enabled) {
        if (name === 'hidden') this.hidden = enabled;
      },
    },
  ]));
  const document = {
    documentElement: { dataset: {}, style: {} },
    querySelector() { return { content: '' }; },
    querySelectorAll() { return []; },
  };
  const context = {
    document,
    localStorage: { setItem() {} },
    $: (selector) => selector === '#theme-toggle' ? themeToggle : icons[selector],
  };
  const start = app.indexOf('function applyColorTheme(');
  const end = app.indexOf('\ntry {', start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  vm.runInNewContext(`${app.slice(start, end)}\nthis.applyColorTheme = applyColorTheme;`, context);
  context.applyColorTheme(initialTheme, false);
  return { context, document, themeToggle, icons };
}

test('theme toggle markup uses inline sun and moon SVG icons with an accessible action label', () => {
  const match = html.match(/<button id="theme-toggle"[\s\S]*?<\/button>/);
  assert.ok(match, 'theme toggle button should exist');
  assert.match(match[0], /aria-label="Switch to light mode"/);
  assert.match(match[0], /<svg id="theme-toggle-sun"[^>]*viewBox="0 0 24 24"/);
  assert.match(match[0], /<svg id="theme-toggle-moon"[^>]*viewBox="0 0 24 24"[^>]*hidden/);
  assert.match(styles, /\.theme-toggle-icon \{ width: 19px; height: 19px;/);
  assert.doesNotMatch(app, /â˜/);
});

test('dark theme shows the sun action icon and labels the light-mode action', () => {
  const { context, document, themeToggle, icons } = themeHarness('dark');
  assert.equal(document.documentElement.dataset.theme, 'dark');
  assert.equal(icons['#theme-toggle-sun'].hidden, false);
  assert.equal(icons['#theme-toggle-moon'].hidden, true);
  assert.equal(themeToggle.attributes['aria-label'], 'Switch to light mode');
  context.applyColorTheme('light', false);
  assert.equal(document.documentElement.dataset.theme, 'light');
  assert.equal(icons['#theme-toggle-sun'].hidden, true);
  assert.equal(icons['#theme-toggle-moon'].hidden, false);
  assert.equal(themeToggle.attributes['aria-label'], 'Switch to dark mode');
  assert.equal(themeToggle.title, 'Switch to dark mode');
});
