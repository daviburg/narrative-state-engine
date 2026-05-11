import { _electron as electron, type ElectronApplication, type Page } from 'playwright';
import * as path from 'path';
import * as os from 'os';
import { SELECTORS } from '../selectors';

export interface VSCodeLaunchOptions {
  /** Path to the VS Code executable. Defaults to system 'code' resolved via PATH. */
  executablePath?: string;
  /** Workspace folder to open on launch. */
  workspacePath?: string;
  /** User data directory. A temp dir avoids single-instance conflicts with a running VS Code. */
  userDataDir?: string;
  /** Extensions directory. Defaults to ~/.vscode/extensions (the system install location). */
  extensionsDir?: string;
  /** Extension IDs to explicitly disable. All other extensions remain enabled. */
  disabledExtensions?: string[];
}

/**
 * Resolves the VS Code executable path.
 * On Windows, the typical path is under Program Files or the user's local app data.
 */
function resolveExecutablePath(provided?: string): string {
  if (provided) return provided;

  if (process.platform === 'win32') {
    const localAppData = process.env.LOCALAPPDATA;
    if (localAppData) {
      return path.join(localAppData, 'Programs', 'Microsoft VS Code', 'Code.exe');
    }
  }

  // Fallback: assume 'code' is on PATH
  return 'code';
}

export class VSCodeApp {
  private app: ElectronApplication | null = null;
  private page: Page | null = null;

  async launch(options: VSCodeLaunchOptions = {}): Promise<Page> {
    const execPath = resolveExecutablePath(options.executablePath);

    const args: string[] = [
      '--disable-gpu-sandbox',
      '--no-sandbox',
    ];

    // Selectively disable specific extensions (all others remain enabled)
    for (const ext of options.disabledExtensions ?? []) {
      args.push(`--disable-extension=${ext}`);
    }

    if (options.userDataDir) {
      args.push(`--user-data-dir=${options.userDataDir}`);
    }

    // Point to the system's extensions dir so installed extensions (e.g. Copilot) are available.
    // Auth tokens live in the OS credential store, so they work with any user-data-dir.
    const extDir = options.extensionsDir ?? path.join(process.env.USERPROFILE ?? os.homedir(), '.vscode', 'extensions');
    args.push(`--extensions-dir=${extDir}`);

    if (options.workspacePath) {
      args.push(options.workspacePath);
    }

    this.app = await electron.launch({
      executablePath: execPath,
      args,
      timeout: 60_000,
    });

    this.page = await this.app.firstWindow();

    // Wait for VS Code to be fully loaded (try primary selector, then fallback)
    try {
      await this.page.waitForSelector(SELECTORS.titleBar, { timeout: 30_000 });
    } catch {
      await this.page.waitForSelector(SELECTORS.titleBarFallback, { timeout: 10_000 });
    }

    // Log VS Code window title from the title bar
    const titleText =
      await this.page.textContent(SELECTORS.titleBar).catch(() => null) ??
      await this.page.textContent(SELECTORS.titleBarFallback).catch(() => null);
    console.log(`VS Code launched: ${titleText?.trim()}`);

    // Dismiss onboarding wizard if present (clean user-data-dir triggers this)
    await this.dismissOnboarding();

    return this.page;
  }

  /**
   * Dismiss the VS Code onboarding wizard (sign-in + theme picker steps).
   * This appears on first launch with a fresh user-data-dir.
   */
  private async dismissOnboarding(): Promise<void> {
    if (!this.page) return;
    const page = this.page;

    // Step 1: Skip sign-in if the dialog is showing
    try {
      const skipBtn = page.locator(SELECTORS.onboardingSkipSignIn);
      if (await skipBtn.isVisible({ timeout: 2_000 })) {
        await skipBtn.click();
        console.log('Dismissed onboarding sign-in dialog');
        await page.waitForTimeout(1_000);
      }
    } catch {
      // No sign-in dialog — already past this step or not a fresh profile
    }

    // Step 2: Click through remaining onboarding steps (theme picker, etc.)
    for (let i = 0; i < 5; i++) {
      try {
        // Look for the X close button on the onboarding dialog
        const closeBtn = page.locator(SELECTORS.onboardingClose);
        if (await closeBtn.first().isVisible({ timeout: 1_000 })) {
          await closeBtn.first().click();
          console.log('Closed onboarding dialog via X button');
          await page.waitForTimeout(500);
          break;
        }
      } catch {
        // No close button found
      }

      try {
        // Fall back to clicking Continue/Next buttons to step through
        const continueBtn = page.locator(SELECTORS.onboardingContinue).last();
        if (await continueBtn.isVisible({ timeout: 1_000 })) {
          await continueBtn.click();
          console.log('Clicked onboarding Continue/Next');
          await page.waitForTimeout(500);
        } else {
          break;
        }
      } catch {
        break;
      }
    }

    // Press Escape a couple of times to dismiss any remaining overlays
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
  }

  getPage(): Page {
    if (!this.page) {
      throw new Error('VS Code is not launched. Call launch() first.');
    }
    return this.page;
  }

  getApp(): ElectronApplication {
    if (!this.app) {
      throw new Error('VS Code is not launched. Call launch() first.');
    }
    return this.app;
  }

  async close(): Promise<void> {
    if (this.app) {
      await this.app.close();
      this.app = null;
      this.page = null;
    }
  }
}
