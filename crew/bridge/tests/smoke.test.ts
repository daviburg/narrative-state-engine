import { test, expect } from '@playwright/test';
import { createBridge, type VSCodeBridge } from '../src/index';
import * as path from 'path';
import * as os from 'os';
import * as fs from 'fs';

/**
 * Smoke test for the VS Code agent bridge.
 *
 * SKIPPED by default — requires VS Code and GitHub Copilot to be installed.
 *
 * To run manually:
 *   cd crew/bridge
 *   npm install
 *   npm run build
 *   BRIDGE_SMOKE=1 npx playwright test tests/smoke.test.ts
 */

const SKIP_REASON =
  'Requires VS Code + GitHub Copilot installed. Set BRIDGE_SMOKE=1 to run.';

test.describe('VS Code Bridge Smoke Test', () => {
  let bridge: VSCodeBridge;
  let tmpDir: string;

  test.beforeAll(async () => {
    // Create a temp user data dir for clean state
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'nse-bridge-test-'));
  });

  test.afterAll(async () => {
    if (bridge) {
      await bridge.close();
    }
    // Clean up temp dir
    if (tmpDir && fs.existsSync(tmpDir)) {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  test('launch VS Code, send prompt, read response', async () => {
    test.skip(!['1', 'true'].includes(process.env.BRIDGE_SMOKE ?? ''), SKIP_REASON);

    // Use the repo root as the workspace
    const workspacePath = path.resolve(__dirname, '..', '..', '..');

    bridge = await createBridge({
      workspacePath,
      userDataDir: tmpDir,
      defaultTimeout: 180_000, // 3 minutes for CI-like environments
    });

    const response = await bridge.sendPrompt('developer', 'What is 2+2?');

    console.log('Response received:', response);

    expect(response).toBeTruthy();
    expect(response.length).toBeGreaterThan(0);
  });
});
