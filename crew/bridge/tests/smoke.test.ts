import { test, expect } from '@playwright/test';
import { createBridge, type VSCodeBridge } from '../src/index';
import * as path from 'path';
import * as child_process from 'child_process';

/**
 * Smoke test for the VS Code agent bridge.
 *
 * Uses the system's default VS Code profile (which has Copilot authenticated).
 * Requires NO other VS Code instance to be running — VS Code's single-instance
 * lock prevents Playwright from attaching to a second instance with the same
 * user-data-dir.
 *
 * SKIPPED by default — requires VS Code and GitHub Copilot to be installed.
 *
 * To run manually:
 *   1. Close all VS Code windows
 *   2. cd crew/bridge
 *      npm install && npm run build
 *      BRIDGE_SMOKE=1 npx playwright test tests/smoke.test.ts
 */

const SKIP_REASON =
  'Requires VS Code + GitHub Copilot installed. Set BRIDGE_SMOKE=1 to run.';

/** Check if VS Code is already running (would block Electron launch). */
function isVSCodeRunning(): boolean {
  try {
    if (process.platform === 'win32') {
      const out = child_process.execSync('tasklist /FI "IMAGENAME eq Code.exe" /NH', { encoding: 'utf8' });
      return out.includes('Code.exe');
    }
    // macOS/Linux
    const out = child_process.execSync('pgrep -x code || pgrep -x "Electron"', { encoding: 'utf8' });
    return out.trim().length > 0;
  } catch {
    return false;
  }
}

test.describe('VS Code Bridge Smoke Test', () => {
  let bridge: VSCodeBridge;

  test.afterAll(async () => {
    if (bridge) {
      await bridge.close();
    }
  });

  test('launch VS Code, send prompt, read response', async () => {
    test.skip(!['1', 'true'].includes(process.env.BRIDGE_SMOKE ?? ''), SKIP_REASON);
    test.skip(isVSCodeRunning(), 'Close all VS Code windows before running the smoke test (single-instance lock).');

    // Use the repo root as the workspace
    const workspacePath = path.resolve(__dirname, '..', '..', '..');

    bridge = await createBridge({
      workspacePath,
      defaultTimeout: 180_000, // 3 minutes for CI-like environments
    });

    const response = await bridge.sendPrompt('developer', 'What is 2+2?');

    console.log('Response received:', response);

    expect(response).toBeTruthy();
    expect(response.length).toBeGreaterThan(0);
  });
});
