import { VSCodeApp, type VSCodeLaunchOptions } from './page-objects/vscode-app';
import { ChatPanel } from './page-objects/chat-panel';
import { ChatMetricsTracker, type ChatMetrics } from './chat-metrics';

export { VSCodeApp, type VSCodeLaunchOptions } from './page-objects/vscode-app';
export { ChatPanel } from './page-objects/chat-panel';
export { SELECTORS } from './selectors';
export { ChatMetricsTracker, type ChatMetrics } from './chat-metrics';

export interface BridgeOptions extends VSCodeLaunchOptions {
  /** Default timeout for waiting on responses, in milliseconds. */
  defaultTimeout?: number;
}

export interface VSCodeBridge {
  /** Send a prompt to the specified agent and return the full response text. */
  sendPrompt(agent: string, prompt: string): Promise<string>;
  /** Start a new chat session, resetting conversation context. */
  newChat(): Promise<void>;
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
  const { bridge } = await buildBridge(options);
  return bridge;
}

/**
 * Create a VS Code automation bridge with access to chat metrics.
 *
 * Returns both the bridge and a metrics accessor function, used by
 * the HTTP server to expose /chat/metrics.
 */
export function createBridgeWithMetrics(options?: BridgeOptions): {
  bridge: Promise<VSCodeBridge>;
  getMetrics: () => ChatMetrics;
} {
  const metrics = new ChatMetricsTracker();
  return {
    bridge: buildBridge(options, metrics).then(r => r.bridge),
    getMetrics: () => metrics.getMetrics(),
  };
}

async function buildBridge(
  options?: BridgeOptions,
  metrics?: ChatMetricsTracker,
): Promise<{ bridge: VSCodeBridge }> {
  const tracker = metrics ?? new ChatMetricsTracker();
  const vscode = new VSCodeApp();
  const page = await vscode.launch(options);
  const chat = new ChatPanel(page);

  // Open the chat panel once during setup
  await chat.open();

  const defaultTimeout = options?.defaultTimeout ?? 120_000;

  const bridge: VSCodeBridge = {
    async sendPrompt(agent: string, prompt: string): Promise<string> {
      tracker.recordSend(prompt.length);
      await chat.selectAgent(agent);
      const response = await chat.sendAndRead(prompt, defaultTimeout);
      tracker.recordReceive(response.length);
      return response;
    },

    async newChat(): Promise<void> {
      await chat.newChat();
      tracker.reset();
    },

    async close(): Promise<void> {
      await vscode.close();
    },
  };

  return { bridge };
}
