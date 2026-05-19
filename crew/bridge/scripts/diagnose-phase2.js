// Diagnostic phase 2: probe welcome dialog and command palette selectors.
// Run: node scripts/diagnose-phase2.js
const { _electron } = require('playwright');
const path = require('path');
const os = require('os');
const fs = require('fs');

async function main() {
  const ld = process.env.LOCALAPPDATA;
  const ep = path.join(ld, 'Programs', 'Microsoft VS Code', 'Code.exe');
  const td = fs.mkdtempSync(path.join(os.tmpdir(), 'nse-diag2-'));

  console.log('Launching VS Code...');
  const app = await _electron.launch({
    executablePath: ep,
    args: [
      '--disable-gpu-sandbox',
      '--no-sandbox',
      '--disable-extensions',
      '--enable-extension=GitHub.copilot',
      '--enable-extension=GitHub.copilot-chat',
      '--user-data-dir=' + td,
    ],
    timeout: 60000,
  });

  const page = await app.firstWindow();
  console.log('Waiting 15s for stabilization...');
  await page.waitForTimeout(15000);

  // Screenshot with welcome dialog
  await page.screenshot({ path: 'test-results/diag2-welcome.png', fullPage: true });

  // Check for welcome/sign-in dialog
  console.log('\n=== Welcome dialog probe ===');
  const welcomeSelectors = [
    'text="Continue without Signing In"',
    'text="Continue with GitHub"',
    'button:has-text("Continue")',
    '.dialog-shadow',
    '.monaco-dialog-box',
    '.dialog-message',
    '[class*="welcome"]',
    '[class*="walkthrough"]',
    '[class*="sign-in"]',
    '[class*="getting-started"]',
    '.getting-started-category',
  ];

  for (const sel of welcomeSelectors) {
    try {
      const count = await page.locator(sel).count();
      if (count > 0) {
        const text = await page.locator(sel).first().textContent();
        const info = await page.locator(sel).first().evaluate(
          (el) => el.tagName + ' id=' + el.id + ' class=' + el.className.substring(0, 80)
        );
        console.log('FOUND', sel, '=> count=' + count, 'tag=' + info, 'text=' + (text || '').substring(0, 100));
      } else {
        console.log('MISS', sel);
      }
    } catch (e) {
      console.log('ERR', sel, e.message.substring(0, 80));
    }
  }

  // Try to dismiss the welcome dialog
  console.log('\n=== Trying to dismiss welcome ===');
  try {
    const skipBtn = page.locator('text="Continue without Signing In"');
    if (await skipBtn.count() > 0) {
      await skipBtn.click();
      console.log('Clicked "Continue without Signing In"');
      await page.waitForTimeout(3000);
    }
  } catch (e) {
    console.log('Could not dismiss:', e.message.substring(0, 80));
  }

  await page.screenshot({ path: 'test-results/diag2-after-dismiss.png', fullPage: true });

  // Now try opening Command Palette
  console.log('\n=== Command Palette probe ===');
  await page.keyboard.press('Control+Shift+p');
  await page.waitForTimeout(2000);

  await page.screenshot({ path: 'test-results/diag2-cmdpalette.png', fullPage: true });

  const cmdPaletteSelectors = [
    '.quick-input-widget input[type="text"]',
    '.quick-input-widget input',
    '.quick-input-widget',
    '[class*="quick-input"]',
    'input[aria-label*="input"]',
    'input[class*="input"]',
    '.quick-input-box input',
  ];

  for (const sel of cmdPaletteSelectors) {
    try {
      const count = await page.locator(sel).count();
      if (count > 0) {
        const info = await page.locator(sel).first().evaluate(
          (el) => el.tagName + ' id=' + el.id + ' class=' + el.className.substring(0, 100)
            + ' type=' + (el.getAttribute('type') || '') + ' visible=' + el.checkVisibility()
        );
        console.log('FOUND', sel, '=> count=' + count, info);
      } else {
        console.log('MISS', sel);
      }
    } catch (e) {
      console.log('ERR', sel, e.message.substring(0, 80));
    }
  }

  // Escape to close command palette
  await page.keyboard.press('Escape');
  await page.waitForTimeout(1000);

  // Try the Chat shortcut
  console.log('\n=== Chat panel probe ===');
  await page.keyboard.press('Control+Alt+i');
  await page.waitForTimeout(3000);
  await page.screenshot({ path: 'test-results/diag2-chat.png', fullPage: true });

  const chatSelectors = [
    '[role="textbox"][aria-label*="Chat Input"]',
    '.interactive-input-editor .monaco-editor textarea',
    '.interactive-session',
    '[id="workbench.panel.chat"]',
    '[class*="chat"]',
    '[class*="interactive"]',
  ];

  for (const sel of chatSelectors) {
    try {
      const count = await page.locator(sel).count();
      if (count > 0) {
        const info = await page.locator(sel).first().evaluate(
          (el) => el.tagName + ' id=' + el.id + ' class=' + el.className.substring(0, 80)
        );
        console.log('FOUND', sel, '=> count=' + count, info);
      } else {
        console.log('MISS', sel);
      }
    } catch (e) {
      console.log('ERR', sel, e.message.substring(0, 80));
    }
  }

  console.log('\nClosing...');
  await app.close();
  try { fs.rmSync(td, { recursive: true, force: true }); } catch {}
  console.log('Done.');
}

main().catch((err) => {
  console.error('FAILED:', err.message);
  process.exit(1);
});
