import type { Page } from 'playwright';
import { SELECTORS } from '../selectors';

const DEFAULT_RESPONSE_TIMEOUT = 120_000; // 2 minutes
const STABILITY_PERIOD = 2_000; // 2 seconds of no DOM changes = streaming done

/** Returns the platform-appropriate modifier key (Meta on macOS, Control elsewhere). */
function platformModifier(): string {
  return process.platform === 'darwin' ? 'Meta' : 'Control';
}

export class ChatPanel {
  constructor(private page: Page) {}

  /**
   * Open the Chat panel via keyboard shortcut.
   * Tries Control+Alt+i (Meta+Alt+i on macOS) first, then falls back to Command Palette.
   */
  async open(): Promise<void> {
    // Try the keyboard shortcut for opening Chat
    await this.page.keyboard.press(`${platformModifier()}+Alt+i`);

    // Wait for the chat panel or input to appear
    try {
      await this.page.waitForSelector(
        `${SELECTORS.chatInput}, ${SELECTORS.chatInputFallback}, ${SELECTORS.chatPanelFallback}`,
        { timeout: 5_000 }
      );
    } catch {
      // Fallback: use the Command Palette
      await this.openViaCommandPalette();
    }
  }

  private async openViaCommandPalette(): Promise<void> {
    await this.page.keyboard.press(`${platformModifier()}+Shift+p`);
    await this.page.waitForSelector(SELECTORS.commandPaletteInput, { timeout: 5_000 });

    const input = this.page.locator(SELECTORS.commandPaletteInput);
    await input.fill('Chat: Open Chat');
    await this.page.keyboard.press('Enter');

    await this.page.waitForSelector(
      `${SELECTORS.chatInput}, ${SELECTORS.chatInputFallback}, ${SELECTORS.chatPanelFallback}`,
      { timeout: 10_000 }
    );
  }

  /**
   * Select an agent by typing @agentName in the chat input.
   */
  async selectAgent(name: string): Promise<void> {
    const input = this.page.locator(
      `${SELECTORS.chatInput}, ${SELECTORS.chatInputFallback}`
    ).first();
    await input.waitFor({ timeout: 5_000 });

    // Clear existing content and type the agent mention
    await input.focus();
    await this.page.keyboard.press(`${platformModifier()}+a`);
    await input.pressSequentially(`@${name} `, { delay: 50 });
  }

  /**
   * Send a prompt to the chat.
   */
  async sendPrompt(text: string): Promise<void> {
    const input = this.page.locator(
      `${SELECTORS.chatInput}, ${SELECTORS.chatInputFallback}`
    ).first();
    await input.waitFor({ timeout: 5_000 });
    await input.focus();

    await input.pressSequentially(text, { delay: 20 });

    // Submit with Enter
    await this.page.keyboard.press('Enter');
  }

  /**
   * Wait for the streaming response to complete.
   * Detection strategy: watch for the loading indicator to disappear,
   * then confirm stability (no new DOM mutations for STABILITY_PERIOD ms).
   */
  async waitForResponse(timeout: number = DEFAULT_RESPONSE_TIMEOUT): Promise<void> {
    const startTime = Date.now();

    // Count existing responses before waiting so we detect a NEW one
    const countBefore = await this.page.locator(SELECTORS.responseContainer).count();

    // Wait for a new response container to appear (count increases)
    await this.page.waitForFunction(
      ([selector, prevCount]: [string, number]) =>
        document.querySelectorAll(selector).length > prevCount,
      [SELECTORS.responseContainer, countBefore] as [string, number],
      { timeout: Math.min(timeout, 30_000) },
    );

    // Wait for the loading indicator to disappear
    try {
      await this.page.waitForSelector(SELECTORS.responseInProgress, {
        state: 'attached',
        timeout: 10_000,
      });
    } catch {
      // Loading indicator may have already appeared and disappeared
    }

    // Now wait for it to detach (streaming complete)
    const remaining = timeout - (Date.now() - startTime);
    if (remaining <= 0) {
      throw new Error(`Response timed out after ${timeout}ms`);
    }

    await this.page.waitForSelector(SELECTORS.responseInProgress, {
      state: 'detached',
      timeout: remaining,
    });

    // Stability check: wait for no DOM mutations in the response area
    await this.waitForStability();
  }

  private async waitForStability(): Promise<void> {
    await this.page.evaluate(([stabilityMs, selector]: [number, string]) => {
      return new Promise<void>((resolve) => {
        const target = document.querySelector(selector);
        if (!target) {
          resolve();
          return;
        }

        let timer: ReturnType<typeof setTimeout>;
        const observer = new MutationObserver(() => {
          clearTimeout(timer);
          timer = setTimeout(() => {
            observer.disconnect();
            resolve();
          }, stabilityMs);
        });

        observer.observe(target, {
          childList: true,
          subtree: true,
          characterData: true,
        });

        // Start the initial timer
        timer = setTimeout(() => {
          observer.disconnect();
          resolve();
        }, stabilityMs);
      });
    }, [STABILITY_PERIOD, SELECTORS.lastResponse] as [number, string]);
  }

  /**
   * Extract the text content of the last response in the chat.
   */
  async getLastResponse(): Promise<string> {
    const responseEl = this.page.locator(SELECTORS.lastResponse).locator(SELECTORS.responseBody).last();

    try {
      await responseEl.waitFor({ timeout: 5_000 });
    } catch {
      throw new Error(
        'No response found in chat panel. The model may not have responded.'
      );
    }

    const text = await responseEl.textContent();
    if (!text || text.trim().length === 0) {
      throw new Error('Response element found but contains no text.');
    }

    return text.trim();
  }

  /**
   * Convenience method: send a prompt and wait for the full response.
   */
  async sendAndRead(text: string, timeout?: number): Promise<string> {
    await this.sendPrompt(text);
    await this.waitForResponse(timeout);
    return this.getLastResponse();
  }
}
