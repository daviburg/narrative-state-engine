import type { Page, Locator } from 'playwright';
import { SELECTORS } from '../selectors';

const DEFAULT_RESPONSE_TIMEOUT = 120_000; // 2 minutes
const STABILITY_PERIOD = 3_000; // 3 seconds of no DOM changes = streaming done

/** Returns the platform-appropriate modifier key (Meta on macOS, Control elsewhere). */
function platformModifier(): string {
  return process.platform === 'darwin' ? 'Meta' : 'Control';
}

export class ChatPanel {
  constructor(private page: Page) {}

  /**
   * Open the Chat panel via keyboard shortcut.
   * Tries Control+Alt+i (Meta+Alt+i on macOS) first, then the Toggle Chat
   * sparkle button, then falls back to the Command Palette.
   */
  async open(): Promise<void> {
    // The Send button uniquely identifies an open, ready chat panel
    const chatVisibleSelector =
      `${SELECTORS.chatInput}, ${SELECTORS.sendButton}`;

    // Try the keyboard shortcut for opening Chat
    await this.page.keyboard.press(`${platformModifier()}+Alt+i`);

    // Wait for the chat panel or input to appear
    try {
      await this.page.waitForSelector(chatVisibleSelector, { timeout: 5_000 });
      return;
    } catch {
      // Keyboard shortcut didn't work
    }

    // Fallback: click the Toggle Chat sparkle button in the title bar (VS Code >= 1.100)
    try {
      const sparkle = this.page.locator(SELECTORS.toggleChatButton).first();
      if (await sparkle.isVisible({ timeout: 2_000 })) {
        await sparkle.click();
        await this.page.waitForSelector(chatVisibleSelector, { timeout: 5_000 });
        return;
      }
    } catch {
      // Sparkle button didn't work
    }

    // Last resort: use the Command Palette
    await this.openViaCommandPalette();
  }

  private async openViaCommandPalette(): Promise<void> {
    await this.page.keyboard.press(`${platformModifier()}+Shift+p`);
    await this.page.waitForSelector(SELECTORS.commandPaletteInput, { timeout: 5_000 });

    const input = this.page.locator(SELECTORS.commandPaletteInput);
    await input.fill('Chat: Open Chat');
    await this.page.keyboard.press('Enter');

    await this.page.waitForSelector(
      `${SELECTORS.chatInput}, ${SELECTORS.sendButton}`,
      { timeout: 10_000 }
    );
  }

  /**
   * Find the chat input textbox, trying multiple selector strategies.
   * Returns the best-matching Locator for the chat input.
   */
  private async findChatInput(): Promise<Locator> {
    // Strategy 1: ARIA-based — Monaco editor textarea with known aria-label
    const primary = this.page.locator(SELECTORS.chatInput);
    const primaryCount = await primary.count();

    if (primaryCount === 1) {
      return primary;
    }

    if (primaryCount > 1) {
      // Multiple Monaco editors (files open) — the chat input is in the
      // secondary sidebar which comes last in DOM order.
      return primary.last();
    }

    // Strategy 2: CSS class-based fallback
    const fallback = this.page.locator(SELECTORS.chatInputFallback);
    if (await fallback.count() > 0) {
      return fallback.first();
    }

    // Strategy 3: Any textbox without an aria-label (very broad)
    const anyTextbox = this.page.locator('[role="textbox"]:not([aria-label])');
    if (await anyTextbox.count() > 0) {
      return anyTextbox.last();
    }

    throw new Error(
      'Could not find chat input textbox. Is the Chat panel open? ' +
      'Run the diagnostic test for details.'
    );
  }

  /**
   * Select an agent by typing @agentName in the chat input.
   */
  async selectAgent(name: string): Promise<void> {
    const input = await this.findChatInput();
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
    const input = await this.findChatInput();
    await input.waitFor({ timeout: 5_000 });
    await input.focus();

    await input.pressSequentially(text, { delay: 20 });

    // Submit with Enter
    await this.page.keyboard.press('Enter');
  }

  /**
   * Wait for the streaming response to complete.
   *
   * Strategy (count-based, uses verified selectors):
   * 1. Count .interactive-item-container elements before (caller should have
   *    counted before sending — here we just wait for a new one to appear)
   * 2. Wait for .codicon-loading inside the last container to disappear
   * 3. Final stability wait
   */
  async waitForResponse(timeout: number = DEFAULT_RESPONSE_TIMEOUT): Promise<void> {
    const deadline = Date.now() + timeout;

    // Phase 1: Wait for a new response container to appear
    // We detect this by waiting for .interactive-response (the assistant's reply)
    // to have the .chat-most-recent-response class, indicating VS Code has attached it.
    await this.page.waitForTimeout(1_000);

    // Phase 2: Wait for streaming indicator to appear then disappear
    const streamingSelector = `${SELECTORS.responseInProgress}, ${SELECTORS.stopButton}`;
    let indicatorFound = false;

    try {
      await this.page.waitForSelector(streamingSelector, {
        state: 'attached',
        timeout: Math.min(30_000, Math.max(deadline - Date.now(), 5_000)),
      });
      indicatorFound = true;
    } catch {
      // Fast response — indicator may have appeared and disappeared already
      console.warn('Streaming indicator not detected — using content stability fallback.');
    }

    if (indicatorFound) {
      const remaining = Math.max(deadline - Date.now(), 5_000);
      try {
        await this.page.waitForSelector(streamingSelector, {
          state: 'detached',
          timeout: remaining,
        });
      } catch {
        console.warn('Streaming indicator detach timed out — proceeding.');
      }
    } else {
      // Fallback: poll the accessibility tree for content stability
      await this.waitForContentStability(deadline);
    }

    // Phase 3: Final stability wait
    await this.page.waitForTimeout(STABILITY_PERIOD);
  }

  /**
   * Fallback response detection: poll the chat panel's text content until it
   * stops changing, indicating the response has finished streaming.
   */
  private async waitForContentStability(deadline: number): Promise<void> {
    const POLL_INTERVAL = 2_000;
    const REQUIRED_STABLE = 3;
    let lastText = '';
    let stableCount = 0;

    while (Date.now() < deadline) {
      const text = await this.getChatPanelText();

      if (text === lastText) {
        stableCount++;
        if (stableCount >= REQUIRED_STABLE) return;
      } else {
        stableCount = 0;
        lastText = text;
      }

      await this.page.waitForTimeout(POLL_INTERVAL);
    }

    console.warn('Content stability check timed out — proceeding anyway.');
  }

  /**
   * Get the text content of the chat panel area (for stability comparison).
   */
  private async getChatPanelText(): Promise<string> {
    return this.page.evaluate(() => {
      const sendBtn = document.querySelector('button[aria-label^="Send"]');
      if (!sendBtn) return '';
      let container: Element | null = sendBtn;
      for (let i = 0; i < 6 && container?.parentElement; i++) {
        container = container.parentElement;
      }
      return (container as HTMLElement)?.innerText ?? '';
    });
  }

  /**
   * Extract the text content of the last response in the chat.
   *
   * Uses verified selectors: find the last .interactive-item-container,
   * then extract .rendered-markdown text inside it.
   */
  async getLastResponse(): Promise<string> {
    // Get the last interactive-item-container (should be the response)
    const containers = this.page.locator(SELECTORS.responseContainer);
    const count = await containers.count();

    if (count === 0) {
      throw new Error(
        'No response containers found in chat panel. ' +
        'Is the Chat panel open? Run the diagnostic test for details.'
      );
    }

    // Walk backwards to find the last container with rendered markdown
    for (let i = count - 1; i >= 0; i--) {
      const container = containers.nth(i);
      const markdown = container.locator(SELECTORS.responseBody);
      const mdCount = await markdown.count();

      if (mdCount > 0) {
        const text = await markdown.last().textContent();
        if (text?.trim()) return text.trim();
      }
    }

    // Fallback: try getting text from the last container directly
    const lastContainer = containers.last();
    const text = await lastContainer.textContent();
    if (text?.trim()) return text.trim();

    throw new Error(
      'No response text found in chat panel. ' +
      'Run the diagnostic test to verify selectors: ' +
      'BRIDGE_DIAGNOSTIC=1 npx playwright test tests/diagnostic.test.ts'
    );
  }

  /**
   * Convenience method: send a prompt and wait for the full response.
   */
  async sendAndRead(text: string, timeout?: number): Promise<string> {
    await this.sendPrompt(text);
    await this.waitForResponse(timeout);
    return this.getLastResponse();
  }

  /**
   * Capture diagnostic information about the chat panel DOM.
   * Saves screenshots, accessibility tree, CSS selector probe results,
   * and raw HTML to the specified output directory.
   */
  async captureDiagnostics(outputDir: string): Promise<void> {
    const fs = await import('fs');
    const path = await import('path');
    fs.mkdirSync(outputDir, { recursive: true });

    // 1. Screenshot
    await this.page.screenshot({
      path: path.join(outputDir, 'screenshot.png'),
      fullPage: true,
    });

    // 2. Accessibility tree (may not be available in all Playwright versions)
    try {
      const a11y = await (this.page as any).accessibility?.snapshot({ interestingOnly: false });
      if (a11y) {
        fs.writeFileSync(
          path.join(outputDir, 'accessibility-tree.json'),
          JSON.stringify(a11y, null, 2),
        );
      }
    } catch {
      console.warn('page.accessibility.snapshot() not available — skipping a11y tree capture.');
    }

    // 3. Selector probe — test which CSS selectors match DOM elements
    const diagnosticProbeSelectors = [
      '.interactive-item-container',
      '.interactive-response',
      '.interactive-request',
      '.rendered-markdown',
      '.chat-markdown-part.rendered-markdown',
      '.codicon-loading',
      '.interactive-session',
      '.chat-editor-overflow',
    ];

    const selectorResults = await this.page.evaluate((candidates) => {
      const results: Record<string, { count: number; samples: string[] }> = {};
      for (const sel of candidates) {
        try {
          const els = document.querySelectorAll(sel);
          results[sel] = {
            count: els.length,
            samples: Array.from(els).slice(0, 3).map(el => {
              const tag = el.tagName.toLowerCase();
              const cls = el.className && typeof el.className === 'string'
                ? '.' + el.className.split(/\s+/).join('.') : '';
              const text = (el.textContent ?? '').substring(0, 100);
              return `<${tag}${cls}> ${text}`;
            }),
          };
        } catch (e: any) {
          results[sel] = { count: -1, samples: [e.message ?? 'error'] };
        }
      }
      return results;
    }, diagnosticProbeSelectors);

    fs.writeFileSync(
      path.join(outputDir, 'selector-probe.json'),
      JSON.stringify(selectorResults, null, 2),
    );

    // 4. CSS class census — all unique classes in the chat panel area
    const classCensus = await this.page.evaluate(() => {
      const sendBtn = document.querySelector('button[aria-label^="Send"]');
      if (!sendBtn) return { error: 'Send button not found' };

      // Walk up ~8 levels to find a reasonable ancestor encompassing the chat area
      let container: Element | null = sendBtn;
      for (let i = 0; i < 8 && container?.parentElement; i++) {
        container = container.parentElement;
      }
      if (!container) return { error: 'Could not find container' };

      const classMap: Record<string, number> = {};
      const walk = (el: Element) => {
        if (el.classList) {
          el.classList.forEach(cls => {
            classMap[cls] = (classMap[cls] || 0) + 1;
          });
        }
        for (const child of Array.from(el.children)) {
          walk(child);
        }
      };
      walk(container);
      return classMap;
    });

    fs.writeFileSync(
      path.join(outputDir, 'css-class-census.json'),
      JSON.stringify(classCensus, null, 2),
    );

    // 5. Raw HTML of the chat area (truncated to 500KB)
    const chatHtml = await this.page.evaluate(() => {
      const sendBtn = document.querySelector('button[aria-label^="Send"]');
      if (!sendBtn) return '<no-send-button-found />';

      let container: Element | null = sendBtn;
      for (let i = 0; i < 8 && container?.parentElement; i++) {
        container = container.parentElement;
      }
      const html = container?.outerHTML ?? '<no-container />';
      return html.length > 500_000
        ? html.substring(0, 500_000) + '\n<!-- TRUNCATED -->'
        : html;
    });

    fs.writeFileSync(path.join(outputDir, 'chat-panel.html'), chatHtml);

    console.log(`Diagnostics saved to ${outputDir}`);
  }
}
