// Diagnostic phase 5: click the sparkle "Toggle Chat" button to open chat.
// Run: node scripts/diagnose-phase5.js
const { _electron } = require('playwright');
const path = require('path');
const os = require('os');
const fs = require('fs');

async function main() {
  const ld = process.env.LOCALAPPDATA;
  const ep = path.join(ld, 'Programs', 'Microsoft VS Code', 'Code.exe');
  const td = fs.mkdtempSync(path.join(os.tmpdir(), 'nse-diag5-'));

  console.log('Launching VS Code...');
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
  try {
    const skip = page.locator('text="Continue without Signing In"');
    if (await skip.isVisible({ timeout: 2000 })) {
      await skip.click();
      console.log('Dismissed sign-in');
      await page.waitForTimeout(1000);
    }
  } catch {}

  // Dismiss remaining onboarding steps
  for (let i = 0; i < 5; i++) {
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
  }

  console.log('Waiting 10s for extensions...');
  await page.waitForTimeout(10000);

  // Click the Toggle Chat sparkle button
  console.log('\n=== Clicking Toggle Chat sparkle ===');
  try {
    const sparkle = page.locator('[aria-label="Toggle Chat"]').first();
    if (await sparkle.isVisible({ timeout: 3000 })) {
      await sparkle.click();
      console.log('Clicked Toggle Chat');
      await page.waitForTimeout(5000);
    } else {
      console.log('Toggle Chat not visible');
    }
  } catch (e) {
    console.log('Error clicking sparkle:', e.message.substring(0, 80));
  }

  await page.screenshot({ path: 'test-results/diag5-after-sparkle.png', fullPage: true });

  // Full DOM dump of visible panels
  console.log('\n=== Full panel probe ===');
  const chatSelectors = [
    '[role="textbox"]',
    'textarea',
    '.chat-widget',
    '[class*="chat-editor"]',
    '[class*="chat-widget"]',
    '.interactive-session',
    '[id*="chat"]',
    '[class*="chat-view"]',
    '[aria-label*="Chat"]',
    '[aria-label*="Ask"]',
    '.chat-input-part',
    '[class*="chat-input"]',
    '.chat-welcome-view',
    '.chat-view-welcome',
    '.rendered-markdown',
    '[class*="chatEditor"]',
    '.editor-instance',
    '[data-mode-id]',
    '.chat-progress-task',
    '.chat-used-context',
    '.chat-followups',
  ];

  for (const sel of chatSelectors) {
    try {
      const count = await page.locator(sel).count();
      if (count > 0) {
        for (let i = 0; i < Math.min(count, 3); i++) {
          const info = await page.locator(sel).nth(i).evaluate(
            (el) => {
              const parts = [];
              parts.push('tag=' + el.tagName);
              if (el.id) parts.push('id=' + el.id);
              parts.push('class=' + (el.className || '').substring(0, 120));
              parts.push('vis=' + el.checkVisibility());
              const al = el.getAttribute('aria-label');
              if (al) parts.push('al=' + al.substring(0, 60));
              const ph = el.getAttribute('placeholder');
              if (ph) parts.push('ph=' + ph);
              return parts.join(' ');
            }
          );
          console.log(`FOUND[${i}]`, sel, '=>', info);
        }
        if (count > 3) console.log(`  ... and ${count - 3} more`);
      } else {
        console.log('MISS', sel);
      }
    } catch (e) {
      console.log('ERR', sel, e.message.substring(0, 60));
    }
  }

  // Also dump the sidebar/panel area
  console.log('\n=== Panel area ===');
  const panelHTML = await page.evaluate(() => {
    const panels = document.querySelectorAll('[id*="workbench.panel"], [id*="workbench.parts.panel"]');
    const results = [];
    for (const p of panels) {
      results.push('Panel: id=' + p.id + ' class=' + p.className.substring(0, 80) + ' visible=' + p.checkVisibility());
      results.push(p.innerHTML.substring(0, 1000));
    }
    if (results.length === 0) {
      // Check the secondary sidebar / chat sidebar
      const sidebarParts = document.querySelectorAll('[class*="sidebar"], [id*="sidebar"]');
      for (const s of sidebarParts) {
        results.push('Sidebar: id=' + s.id + ' class=' + s.className.substring(0, 80) + ' visible=' + s.checkVisibility());
      }
    }
    return results.join('\n---\n');
  });
  console.log(panelHTML);

  console.log('\nClosing...');
  await app.close();
  try { fs.rmSync(td, { recursive: true, force: true }); } catch {}
  console.log('Done.');
}

main().catch((err) => {
  console.error('FAILED:', err.message);
  process.exit(1);
});
