/**
 * Centralized selector registry for VS Code Chat panel automation.
 *
 * Selectors marked [VERIFIED] were confirmed against VS Code 1.100.x DOM snapshot.
 * Selectors marked [UNVERIFIED] need validation via the diagnostic test.
 *
 * KEY FIX (2025-05): .chat-editor-overflow is the chat INPUT overflow container,
 * NOT the response area. Response containers use .interactive-item-container /
 * .interactive-response. The old selectors were looking in the wrong place.
 */

// VS Code version: 1.100.x (tested 2025-05)
export const SELECTORS = {
  /**
   * [VERIFIED] Chat input textbox — Monaco editor textarea.
   * The Monaco textarea's aria-label starts with "The editor is not accessible".
   * NOTE: Also matches file editor textareas; use last() to get the chat one
   * (secondary sidebar comes last in DOM order).
   */
  chatInput: '[role="textbox"][aria-label^="The editor is not accessible"]',

  /**
   * Chat input fallback — CSS class-based (may break on VS Code updates).
   */
  chatInputFallback: '.interactive-input-editor .monaco-editor textarea',

  /**
   * [VERIFIED] Send button.
   * Full label: "Send [Alt] Send to New Chat (Ctrl+Shift+Enter)".
   * Disabled when input is empty.
   */
  sendButton: 'button[aria-label^="Send"]',

  /** [VERIFIED] Broader Send button match. */
  sendButtonFallback: 'button[aria-label*="Send"]',

  /**
   * [VERIFIED] Container for individual chat messages (request + response).
   * Each turn has two: .interactive-request and .interactive-response.
   */
  responseContainer: '.interactive-item-container',

  /** [VERIFIED] Last message container in the chat panel. */
  lastResponse: '.interactive-item-container:last-child',

  /** [VERIFIED] Response body — rendered markdown content inside a container. */
  responseBody: '.rendered-markdown',

  /** [VERIFIED] Loading spinner icon — appears during streaming. */
  streamingIndicator: '.codicon-loading',

  /** [VERIFIED] Stop/Cancel button — appears during streaming. */
  stopButton: 'button[aria-label*="Stop"], button[aria-label*="Cancel"]',

  /** [VERIFIED] Loading spinner scoped to last response — streaming in progress. */
  responseInProgress: '.interactive-item-container:last-child .codicon-loading',

  /** [VERIFIED] Chat panel view container. */
  chatPanel: '.interactive-session',

  /** [VERIFIED] Chat panel fallback — session container. */
  chatPanelFallback: '.interactive-session',

  /** [VERIFIED] Toggle Chat sparkle button in title bar Command Center. */
  toggleChatButton: '[aria-label="Toggle Chat"]',

  /**
   * [VERIFIED] Agent picker button.
   * Full label: "Set Agent (Ctrl+.) - Agent".
   */
  agentPicker: 'button[aria-label*="Set Agent"]',

  /** Agent picker dropdown list items. */
  agentPickerItem: '.monaco-list-row',

  /** [VERIFIED] Command palette input. */
  commandPaletteInput: '.quick-input-widget input[type="text"]',

  /** [VERIFIED] Title bar — confirms VS Code has loaded. */
  titleBar: '.window-title',

  /** Title bar fallback — part container. */
  titleBarFallback: '#workbench\\.parts\\.titlebar',

  /** Onboarding "Continue without Signing In" button. */
  onboardingSkipSignIn: 'text="Continue without Signing In"',

  /** Onboarding close button (X). */
  onboardingClose: '.dialog-shadow .codicon-close, .onboarding-a-close',

  /** Onboarding Continue/Next buttons. */
  onboardingContinue: 'button:has-text("Continue"), button:has-text("Next")',

  /** Onboarding dialog container. */
  onboardingContainer: '.onboarding-a-dialog',
} as const;
