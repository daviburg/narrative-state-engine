/**
 * Centralized selector registry for VS Code Chat panel automation.
 *
 * Each selector is annotated with the VS Code version it was tested against.
 * Prefer ARIA roles and data attributes over CSS classes for stability.
 */

// VS Code version: 1.100.x
export const SELECTORS = {
  /** The chat input textarea where prompts are typed. */
  chatInput: '[role="textbox"][aria-label*="Chat Input"]',

  /** The chat input textarea (fallback — class-based). */
  chatInputFallback: '.interactive-input-editor .monaco-editor textarea',

  /** The send button for submitting a chat prompt. */
  sendButton: '[aria-label="Send"]',

  /** The send button (fallback — title-based). */
  sendButtonFallback: '[title="Send"]',

  /** Container for individual chat response messages. */
  responseContainer: '.interactive-item-container',

  /** The last response element in the chat panel. */
  lastResponse: '.interactive-item-container:last-child',

  /** Response body content within a response container. */
  responseBody: '.interactive-response .rendered-markdown',

  /** Loading/streaming indicator shown while the model is generating. */
  streamingIndicator: '.codicon-loading',

  /** Progress indicator on the response (alternative detection). */
  responseInProgress: '.interactive-item-container:last-child .codicon-loading',

  /** The chat panel view container. */
  chatPanel: '[id="workbench.panel.chat"]',

  /** The chat panel (fallback — view-based). */
  chatPanelFallback: '.interactive-session',

  /** Agent picker / model picker dropdown trigger. */
  agentPicker: '.chat-input-toolbars .codicon-chevron-down',

  /** Agent picker menu items. */
  agentPickerItem: '.monaco-list-row',

  /** Command palette input. */
  commandPaletteInput: '.quick-input-widget input[type="text"]',

  /** The title bar — used to verify VS Code has loaded. */
  titleBar: '.titlebar-text',
} as const;
