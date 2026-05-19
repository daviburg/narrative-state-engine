// Diagnostic phase 4: launch VS Code WITH extensions (no --disable-extensions)
// to probe real Chat panel DOM with Copilot active.
// Run: node scripts/diagnose-phase4.js
const { _electron } = require('playwright');
const path = require('path');
const os = require('os');
const fs = require('fs');

async function main() {
  const ld = process.env.LOCALAPPDATA;
  const ep = path.join(ld, 'Programs', 'Microsoft VS Code', 'Code.exe');
  const td = fs.mkdtempSync(path.join(os.tmpdir(), 'nse-diag4-'));

  console.log('Launching VS Code WITHOUT --disable-extensions...');
  const app = await _electron.launch({
    executablePath: ep,
    args: [
      '--disable-gpu-sandbox',
      '--no-sandbox',
      '--user-data-dir=' + td,
    ],
    timeout: 60000,
  });

  const page = await app.firstWindow();
  console.log('Waiting 10s...');
  await page.waitForTimeout(10000);

  // Dismiss onboarding
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

    try {
      const closeBtn = page.locator('.onboarding-a-close');
      if (await closeBtn.isVisible({ timeout: 500 })) {
        await closeBtn.click();
        console.log('Clicked onboarding close');
        await page.waitForTimeout(500);
        continue;
      }
    } catch {}

    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
    break;
  }

  // Wait for extensions to fully load
  console.log('Waiting 15s for extensions to load...');
  await page.waitForTimeout(15000);

  // Press Escape to close any remaining dialogs
  await page.keyboard.press('Escape');
  await page.waitForTimeout(500);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(500);

  await page.screenshot({ path: 'test-results/diag4-ready.png', fullPage: true });

  // Open Chat with Ctrl+Alt+I
  console.log('\n=== Opening Chat with Ctrl+Alt+I ===');
  await page.keyboard.press('Control+Alt+i');
  await page.waitForTimeout(5000);
  await page.screenshot({ path: 'test-results/diag4-chat-open.png', fullPage: true });

  // Comprehensive chat selector probe
  const chatSelectors = [
    '[role="textbox"][aria-label*="Chat"]',
    '[role="textbox"][aria-label*="chat"]',
    '[role="textbox"]',
    '.interactive-input-editor',
    '.interactive-input-editor .monaco-editor textarea',
    '.interactive-session',
    '.interactive-item-container',
    '.interactive-response',
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
    '.chat-view-welcome',
    '[class*="rendered-markdown"]',
    '.chat-attachments-container',
    '[aria-label*="Ask"]',
    '[placeholder*="Ask"]',
    '[class*="chat-input"]',
    '[class*="chat-response"]',
  ];

  console.log('\n=== Chat selector probe ===');
  for (const sel of chatSelectors) {
    try {
      const count = await page.locator(sel).count();
      if (count > 0) {
        const first = page.locator(sel).first();
        const info = await first.evaluate(
          (el) => {
            const attrs = [];
            attrs.push('tag=' + el.tagName);
            if (el.id) attrs.push('id=' + el.id);
            attrs.push('class=' + (el.className || '').substring(0, 100));
            attrs.push('visible=' + el.checkVisibility());
            const ariaLabel = el.getAttribute('aria-label');
            if (ariaLabel) attrs.push('aria-label=' + ariaLabel.substring(0, 60));
            const role = el.getAttribute('role');
            if (role) attrs.push('role=' + role);
            const placeholder = el.getAttribute('placeholder');
            if (placeholder) attrs.push('placeholder=' + placeholder.substring(0, 60));
            return attrs.join(' ');
          }
        );
        console.log('FOUND', sel, '=> count=' + count, info);
      } else {
        console.log('MISS', sel);
      }
    } catch (e) {
      console.log('ERR', sel, e.message.substring(0, 80));
    }
  }

  // Dump the chat panel area HTML
  console.log('\n=== Chat area outerHTML ===');
  const chatHtml = await page.evaluate(() => {
    // Try to find the chat panel/view
    const candidates = [
      document.querySelector('[id*="chat"]'),
      document.querySelector('.chat-widget'),
      document.querySelector('[class*="chat-editor"]'),
      document.querySelector('.interactive-session'),
      document.querySelector('[class*="chat-view"]'),
    ];
    for (const el of candidates) {
      if (el && el.checkVisibility()) return el.outerHTML.substring(0, 5000);
    }
    return 'No visible chat element found';
  });
  console.log(chatHtml);

  console.log('\nClosing...');
  await app.close();
  try { fs.rmSync(td, { recursive: true, force: true }); } catch {}
  console.log('Done.');
}

main().catch((err) => {
  console.error('FAILED:', err.message);
  process.exit(1);
});
