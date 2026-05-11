import { test } from '@playwright/test';
import { VSCodeApp } from '../src/page-objects/vscode-app';
import { ChatPanel } from '../src/page-objects/chat-panel';
import * as path from 'path';
import * as child_process from 'child_process';
import * as fs from 'fs';

/**
 * Diagnostic test for discovering VS Code chat response DOM structure.
 *
 * This test sends a simple prompt and captures:
 *   - Screenshots (before & after)
 *   - Full accessibility tree
 *   - CSS selector probe results (which candidate selectors match)
 *   - CSS class census (all classes in the chat panel area)
 *   - Raw HTML of the chat panel
 *
 * Run:
 *   1. Close all VS Code windows
 *   2. cd crew/bridge
 *      npm install && npm run build
 *      BRIDGE_DIAGNOSTIC=1 npx playwright test tests/diagnostic.test.ts
 *   3. Inspect test-results/diagnostic/after/selector-probe.json
 */

const SKIP_REASON =
  'Set BRIDGE_DIAGNOSTIC=1 to run the DOM diagnostic test.';

function isVSCodeRunning(): boolean {
  try {
    if (process.platform === 'win32') {
      const out = child_process.execSync(
        'tasklist /FI "IMAGENAME eq Code.exe" /NH',
        { encoding: 'utf8' },
      );
      return out.includes('Code.exe');
    }
    const out = child_process.execSync('pgrep -x code || pgrep -x "Electron"', {
      encoding: 'utf8',
    });
    return out.trim().length > 0;
  } catch {
    return false;
  }
}

test.describe('DOM Diagnostic', () => {
  test('capture chat response DOM after sending a prompt', async () => {
    test.skip(
      !['1', 'true'].includes(process.env.BRIDGE_DIAGNOSTIC ?? ''),
      SKIP_REASON,
    );
    test.skip(
      isVSCodeRunning(),
      'Close all VS Code windows before running (single-instance lock).',
    );
    test.setTimeout(300_000); // 5 minutes

    const vscode = new VSCodeApp();
    const workspacePath = path.resolve(__dirname, '..', '..', '..');
    const page = await vscode.launch({ workspacePath });

    try {
      const chat = new ChatPanel(page);
      await chat.open();
      await page.waitForTimeout(3_000);

      const outputDir = path.resolve(
        __dirname, '..', 'test-results', 'diagnostic',
      );
      fs.mkdirSync(outputDir, { recursive: true });

      // Capture BEFORE state
      console.log('Capturing pre-send diagnostics...');
      await chat.captureDiagnostics(path.join(outputDir, 'before'));

      // Select agent and send prompt
      console.log('Selecting agent and sending prompt...');
      await chat.selectAgent('developer');
      await chat.sendPrompt('What is 2+2? Reply with just the number.');

      // Wait for response — generous fixed wait since we're discovering selectors
      console.log('Prompt sent, waiting 60s for response to complete...');
      await page.waitForTimeout(60_000);

      // Capture AFTER state
      console.log('Capturing post-response diagnostics...');
      await chat.captureDiagnostics(path.join(outputDir, 'after'));

      console.log('\n=== DIAGNOSTIC COMPLETE ===');
      console.log(`Output: ${outputDir}`);
      console.log('Key files to inspect:');
      console.log(
        '  after/selector-probe.json  — which CSS selectors matched response elements',
      );
      console.log(
        '  after/css-class-census.json — all CSS classes in the chat panel area',
      );
      console.log(
        '  after/chat-panel.html       — raw HTML of the chat area',
      );
      console.log('  after/screenshot.png        — visual state');
      console.log(
        '  after/accessibility-tree.json — full ARIA tree',
      );

      // Compare before/after to highlight new CSS classes
      try {
        const beforeClasses = JSON.parse(
          fs.readFileSync(
            path.join(outputDir, 'before', 'css-class-census.json'),
            'utf8',
          ),
        );
        const afterClasses = JSON.parse(
          fs.readFileSync(
            path.join(outputDir, 'after', 'css-class-census.json'),
            'utf8',
          ),
        );
        const newClasses = Object.keys(afterClasses).filter(
          cls => !(cls in beforeClasses),
        );
        if (newClasses.length > 0) {
          console.log(
            `\nNew CSS classes after response: ${newClasses.join(', ')}`,
          );
        }
      } catch {
        console.log('Could not compare before/after class census.');
      }

      // Log selector probe results
      try {
        const probeResults = JSON.parse(
          fs.readFileSync(
            path.join(outputDir, 'after', 'selector-probe.json'),
            'utf8',
          ),
        );
        console.log('\nSelector probe results (count > 0):');
        for (const [sel, info] of Object.entries(probeResults)) {
          const { count, samples } = info as {
            count: number;
            samples: string[];
          };
          if (count > 0) {
            console.log(`  ${sel}: ${count} matches`);
            for (const s of samples) {
              console.log(`    → ${s}`);
            }
          }
        }
      } catch {
        console.log('Could not read selector probe results.');
      }
    } finally {
      await vscode.close();
    }
  });
});
