// Diagnostic phase 3: probe Chat panel DOM after full onboarding dismissal.
// Run: node scripts/diagnose-phase3.js
const { _electron } = require('playwright');
const path = require('path');
const os = require('os');
const fs = require('fs');

async function main() {
  const ld = process.env.LOCALAPPDATA;
  const ep = path.join(ld, 'Programs', 'Microsoft VS Code', 'Code.exe');
  const td = fs.mkdtempSync(path.join(os.tmpdir(), 'nse-diag3-'));

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
  console.log('Waiting 10s...');
  await page.waitForTimeout(10000);

  // Dismiss onboarding completely
  console.log('\n=== Dismissing onboarding ===');
  for (let i = 0; i < 10; i++) {
    try {
      const skip = page.locator('text="Continue without Signing In"');
      if (await skip.isVisible({ timeout: 1000 })) {
        await skip.click();
        console.log('Clicked "Continue without Signing In"');
        await page.waitForTimeout(1000);
        continue;
      }
    } catch {}

    // Try close button (X)
    try {
      const closeBtn = page.locator('.onboarding-a-close');
      if (await closeBtn.isVisible({ timeout: 500 })) {
        await closeBtn.click();
        console.log('Clicked onboarding close');
        await page.waitForTimeout(500);
        continue;
      }
    } catch {}

    // Try Escape
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
    break;
  }

  await page.waitForTimeout(2000);
  await page.screenshot({ path: 'test-results/diag3-after-dismiss.png', fullPage: true });
  console.log('After dismiss screenshot saved');

  // Try opening Chat with Ctrl+Alt+I
  console.log('\n=== Trying Ctrl+Alt+I ===');
  await page.keyboard.press('Control+Alt+i');
  await page.waitForTimeout(5000);
  await page.screenshot({ path: 'test-results/diag3-after-chat-shortcut.png', fullPage: true });

  // Probe chat selectors
  const chatSelectors = [
    '[role="textbox"][aria-label*="Chat"]',
    '[role="textbox"]',
    '.interactive-input-editor',
    '.interactive-session',
    '[id="workbench.panel.chat"]',
    '[id*="chat"]',
    '[class*="chat-widget"]',
    '[class*="chatWidget"]',
    '[class*="chat-editor"]',
    '.chat-input-part',
    '.chat-widget',
    '[aria-label*="Chat"]',
    '[class*="interactive"]',
    'textarea',
    '.monaco-editor textarea',
    '.chat-view-container',
    '.codicon-chat-sparkle',
  ];

  console.log('\n=== Chat selector probe (after Ctrl+Alt+I) ===');
  for (const sel of chatSelectors) {
    try {
      const count = await page.locator(sel).count();
      if (count > 0) {
        const info = await page.locator(sel).first().evaluate(
          (el) => el.tagName + ' id=' + el.id + ' class=' + (el.className || '').substring(0, 120)
            + ' visible=' + el.checkVisibility()
        );
        console.log('FOUND', sel, '=> count=' + count, info);
      } else {
        console.log('MISS', sel);
      }
    } catch (e) {
      console.log('ERR', sel, e.message.substring(0, 80));
    }
  }

  // Try command palette approach
  console.log('\n=== Trying Command Palette "Chat: Open Chat" ===');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(500);
  await page.keyboard.press('Control+Shift+p');
  await page.waitForTimeout(2000);

  // Check if command palette opened
  const qiCount = await page.locator('.quick-input-widget').count();
  console.log('Quick input widget count:', qiCount);

  if (qiCount > 0) {
    // Check visibility
    const visible = await page.locator('.quick-input-widget').first().evaluate(el => el.checkVisibility());
    console.log('Quick input visible:', visible);

    // Type and check available commands
    const input = page.locator('.quick-input-widget input').first();
    await input.fill('Chat');
    await page.waitForTimeout(1000);
    await page.screenshot({ path: 'test-results/diag3-cmdpalette-chat.png', fullPage: true });

    // Check what commands are listed
    const items = await page.locator('.quick-input-widget .monaco-list-row').allTextContents();
    console.log('Available Chat commands:', items.slice(0, 10));

    await input.fill('Chat: Open Chat');
    await page.waitForTimeout(500);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(5000);
    await page.screenshot({ path: 'test-results/diag3-after-openchat.png', fullPage: true });

    // Probe again
    console.log('\n=== Chat selector probe (after command palette) ===');
    for (const sel of chatSelectors) {
      try {
        const count = await page.locator(sel).count();
        if (count > 0) {
          const info = await page.locator(sel).first().evaluate(
            (el) => el.tagName + ' id=' + el.id + ' class=' + (el.className || '').substring(0, 120)
              + ' visible=' + el.checkVisibility()
          );
          console.log('FOUND', sel, '=> count=' + count, info);
        } else {
          console.log('MISS', sel);
        }
      } catch (e) {
        console.log('ERR', sel, e.message.substring(0, 80));
      }
    }
  } else {
    console.log('Command palette did not open!');

    // Dump body children
    const bodyKids = await page.evaluate(() => {
      return Array.from(document.body.children).map(c =>
        '<' + c.tagName + ' id=' + c.id + ' class=' + (c.className || '').substring(0, 80) + ' visible=' + c.checkVisibility() + '>'
      ).join('\n');
    });
    console.log('Body children:\n', bodyKids);
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
