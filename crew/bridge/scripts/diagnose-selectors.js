// Diagnostic: launch VS Code, screenshot, dump title bar DOM.
// Run: node scripts/diagnose-selectors.js
const { _electron } = require('playwright');
const path = require('path');
const os = require('os');
const fs = require('fs');

async function main() {
  const ld = process.env.LOCALAPPDATA;
  const ep = path.join(ld, 'Programs', 'Microsoft VS Code', 'Code.exe');
  const td = fs.mkdtempSync(path.join(os.tmpdir(), 'nse-diag-'));

  console.log('Temp dir:', td);
  console.log('Launching VS Code from:', ep);

  const app = await _electron.launch({
    executablePath: ep,
    args: [
      '--disable-gpu-sandbox',
      '--no-sandbox',
      '--disable-extensions',
      '--user-data-dir=' + td,
    ],
    timeout: 60000,
  });

  const page = await app.firstWindow();
  console.log('Window obtained, waiting 15s for stabilization...');
  await page.waitForTimeout(15000);

  // Screenshot
  const ssPath = path.join(__dirname, '..', 'test-results', 'diag-screenshot.png');
  await page.screenshot({ path: ssPath, fullPage: true });
  console.log('Screenshot saved:', ssPath);

  // Probe selectors
  const selectors = [
    '.titlebar-text',
    '.window-title',
    '[class*="titlebar"]',
    '[class*="title-bar"]',
    '[role="banner"]',
    '.titlebar-container',
    '.titlebar-center',
    '.titlebar-left',
    '.titlebar-right',
    '#workbench\\.parts\\.titlebar',
    '[id*="titlebar"]',
    '[aria-label*="Title"]',
    '[class*="window-title"]',
    '.title',
    '[class*="title"]',
  ];

  console.log('\n=== Selector probe ===');
  for (const sel of selectors) {
    try {
      const count = await page.locator(sel).count();
      if (count > 0) {
        const text = await page.locator(sel).first().textContent();
        const info = await page.locator(sel).first().evaluate(
          (el) => el.tagName + ' id=' + el.id + ' class=' + el.className
        );
        console.log('FOUND', sel, '=> count=' + count, 'tag=' + info, 'text=' + (text || '').substring(0, 100));
      } else {
        console.log('MISS', sel);
      }
    } catch (e) {
      console.log('ERR', sel, e.message.substring(0, 60));
    }
  }

  // Dump titlebar outerHTML
  console.log('\n=== Titlebar DOM ===');
  const html = await page.evaluate(() => {
    const candidates = [
      document.querySelector('#workbench\\.parts\\.titlebar'),
      document.querySelector('[id*="titlebar"]'),
      document.querySelector('[class*="titlebar"]'),
      document.querySelector('[role="banner"]'),
    ];
    for (const el of candidates) {
      if (el) return el.outerHTML.substring(0, 5000);
    }
    // Fallback: body children
    return Array.from(document.body.children)
      .map((c) => '<' + c.tagName + ' id="' + c.id + '" class="' + c.className.substring(0, 80) + '">')
      .join('\n');
  });
  console.log(html);

  // ARIA landmarks
  console.log('\n=== ARIA landmarks ===');
  const landmarks = await page.evaluate(() => {
    const roles = ['banner', 'main', 'navigation', 'complementary', 'contentinfo', 'application'];
    const results = [];
    for (const role of roles) {
      const els = document.querySelectorAll('[role="' + role + '"]');
      for (const el of els) {
        results.push('role=' + role + ' tag=' + el.tagName + ' id=' + el.id + ' class=' + el.className.substring(0, 80));
      }
    }
    return results;
  });
  landmarks.forEach((l) => console.log(' ', l));

  console.log('\nClosing...');
  await app.close();

  try {
    fs.rmSync(td, { recursive: true, force: true });
  } catch (e) {
    console.log('Cleanup skip:', td);
  }
  console.log('Done.');
}

main().catch((err) => {
  console.error('FAILED:', err.message);
  process.exit(1);
});
