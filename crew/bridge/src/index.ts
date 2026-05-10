import { VSCodeApp, type VSCodeLaunchOptions } from './page-objects/vscode-app';
import { ChatPanel } from './page-objects/chat-panel';

export { VSCodeApp, type VSCodeLaunchOptions } from './page-objects/vscode-app';
export { ChatPanel } from './page-objects/chat-panel';
export { SELECTORS } from './selectors';

export interface BridgeOptions extends VSCodeLaunchOptions {
  /** Default timeout for waiting on responses, in milliseconds. */
  defaultTimeout?: number;
}

export interface VSCodeBridge {
  /** Send a prompt to the specified agent and return the full response text. */
  sendPrompt(agent: string, prompt: string): Promise<string>;
  /** Close VS Code and clean up. */
  close(): Promise<void>;
}

/**
 * Create a VS Code automation bridge.
 *
 * Launches VS Code, opens the Chat panel, and returns an interface
 * for sending prompts to agents and reading responses.
 *
 * @example
 * ```ts
 * const bridge = await createBridge({ workspacePath: '/path/to/project' });
 * const response = await bridge.sendPrompt('developer', 'What is 2+2?');
 * console.log(response);
 * await bridge.close();
 * ```
 */
export async function createBridge(options?: BridgeOptions): Promise<VSCodeBridge> {
  const vscode = new VSCodeApp();
  const page = await vscode.launch(options);
  const chat = new ChatPanel(page);

  // Open the chat panel once during setup
  await chat.open();

  const defaultTimeout = options?.defaultTimeout ?? 120_000;

  return {
    async sendPrompt(agent: string, prompt: string): Promise<string> {
      await chat.selectAgent(agent);
      const response = await chat.sendAndRead(prompt, defaultTimeout);
      return response;
    },

    async close(): Promise<void> {
      await vscode.close();
    },
  };
}
