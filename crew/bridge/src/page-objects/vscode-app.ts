import { _electron as electron, type ElectronApplication, type Page } from 'playwright';
import * as path from 'path';
import { SELECTORS } from '../selectors';

export interface VSCodeLaunchOptions {
  /** Path to the VS Code executable. Defaults to system 'code' resolved via PATH. */
  executablePath?: string;
  /** Workspace folder to open on launch. */
  workspacePath?: string;
  /** Temporary user data directory for clean state. If not set, uses a temp dir. */
  userDataDir?: string;
  /** Extensions to keep enabled (all others are disabled). Defaults to ['GitHub.copilot', 'GitHub.copilot-chat']. */
  enabledExtensions?: string[];
}

const DEFAULT_ENABLED_EXTENSIONS = ['GitHub.copilot', 'GitHub.copilot-chat'];

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

  // Fallback: assume 'code' is on PATH and resolve to electron
  return 'code';
}

export class VSCodeApp {
  private app: ElectronApplication | null = null;
  private page: Page | null = null;

  async launch(options: VSCodeLaunchOptions = {}): Promise<Page> {
    const execPath = resolveExecutablePath(options.executablePath);
    const enabledExtensions = options.enabledExtensions ?? DEFAULT_ENABLED_EXTENSIONS;

    const args: string[] = [
      '--disable-gpu-sandbox',
      '--no-sandbox',
    ];

    // Disable all extensions, then selectively enable
    args.push('--disable-extensions');
    for (const ext of enabledExtensions) {
      args.push(`--enable-extension=${ext}`);
    }

    if (options.userDataDir) {
      args.push(`--user-data-dir=${options.userDataDir}`);
    }

    if (options.workspacePath) {
      args.push(options.workspacePath);
    }

    this.app = await electron.launch({
      executablePath: execPath,
      args,
      timeout: 60_000,
    });

    this.page = await this.app.firstWindow();

    // Wait for VS Code to be fully loaded
    await this.page.waitForSelector(SELECTORS.titleBar, { timeout: 30_000 });

    // Log VS Code version from the title bar
    const titleText = await this.page.textContent(SELECTORS.titleBar);
    console.log(`VS Code launched: ${titleText}`);

    return this.page;
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
