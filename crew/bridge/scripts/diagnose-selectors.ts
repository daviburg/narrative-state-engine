/**
 * Diagnostic script: launches VS Code via Electron, screenshots the window,
 * and dumps the title bar / top-level DOM structure to identify correct selectors.
 *
 * Usage:
 *   cd crew/bridge
 *   npx tsx scripts/diagnose-selectors.ts
 */
import { _electron as electron } from 'playwright';
import * as path from 'path';
import * as os from 'os';
import * as fs from 'fs';

async function main() {
  const localAppData = process.env.LOCALAPPDATA;
  const execPath = localAppData
    ? path.join(localAppData, 'Programs', 'Microsoft VS Code', 'Code.exe')
    : 'code';

  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'nse-diag-'));
  console.log('Temp dir:', tmpDir);

  console.log('Launching VS Code from:', execPath);
  const app = await electron.launch({
    executablePath: execPath,
    args: [
      '--disable-gpu-sandbox',
      '--no-sandbox',
      '--disable-extensions',
      `--user-data-dir=${tmpDir}`,
    ],
    timeout: 60_000,
  });

  const page = await app.firstWindow();
  console.log('Window obtained, waiting 10 seconds for VS Code to stabilize...');
  await page.waitForTimeout(10_000);

  // Screenshot
  const screenshotPath = path.join(__dirname, '..', 'test-results', 'diag-screenshot.png');
  await page.screenshot({ path: screenshotPath, fullPage: true });
  console.log('Screenshot saved to:', screenshotPath);

  // Dump the title bar area DOM
  const titleBarSelectors = [
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
  ];

  console.log('\n=== Selector probe results ===');
  for (const sel of titleBarSelectors) {
    const count = await page.locator(sel).count();
    if (count > 0) {
      const text = await page.locator(sel).first().textContent();
      const tag = await page.locator(sel).first().evaluate(el => el.tagName + '.' + el.className);
      console.log(`  ✓ ${sel} → count=${count}, tag=${tag}, text="${text?.trim().substring(0, 80)}"`);
    } else {
      console.log(`  ✗ ${sel} → not found`);
    }
  }

  // Dump the outerHTML of the titlebar part
  console.log('\n=== Titlebar area outerHTML ===');
  const titlebarPartHTML = await page.evaluate(() => {
    // Try various approaches to find the title bar
    const candidates = [
      document.querySelector('#workbench\\.parts\\.titlebar'),
      document.querySelector('[id*="titlebar"]'),
      document.querySelector('[class*="titlebar"]'),
      document.querySelector('[role="banner"]'),
    ];
    for (const el of candidates) {
      if (el) return el.outerHTML.substring(0, 3000);
    }

    // Fallback: dump all top-level children of body
    const body = document.body;
    return Array.from(body.children)
      .map(c => `<${c.tagName} id="${c.id}" class="${c.className}">`)
      .join('\n');
  });
  console.log(titlebarPartHTML);

  // Also dump ARIA landmarks
  console.log('\n=== ARIA landmarks ===');
  const landmarks = await page.evaluate(() => {
    const roles = ['banner', 'main', 'navigation', 'complementary', 'contentinfo'];
    const results: string[] = [];
    for (const role of roles) {
      const els = document.querySelectorAll(`[role="${role}"]`);
      for (const el of els) {
        results.push(`role="${role}" tag=${el.tagName} id="${el.id}" class="${el.className.substring(0, 80)}"`);
      }
    }
    return results;
  });
  landmarks.forEach(l => console.log(`  ${l}`));

  console.log('\nClosing VS Code...');
  await app.close();

  // Clean up temp dir
  try {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  } catch (e) {
    console.log('Could not clean temp dir (expected on Windows):', tmpDir);
  }
}

main().catch(err => {
  console.error('Diagnostic failed:', err);
  process.exit(1);
});
