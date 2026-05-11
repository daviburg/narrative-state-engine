/**
 * Chat metrics tracking for the VS Code automation bridge.
 *
 * Tracks turn count, character/token estimates per session.
 */

export interface ChatMetrics {
  turnCount: number;
  totalCharsSent: number;
  totalCharsReceived: number;
  /** Estimated tokens sent (chars / 4). */
  estimatedTokensSent: number;
  /** Estimated tokens received (chars / 4). */
  estimatedTokensReceived: number;
  /** ISO 8601 timestamp of when the session started. */
  sessionStartedAt: string;
}

export class ChatMetricsTracker {
  private turnCount = 0;
  private totalCharsSent = 0;
  private totalCharsReceived = 0;
  private sessionStartedAt: string;

  constructor() {
    this.sessionStartedAt = new Date().toISOString();
  }

  recordSend(chars: number): void {
    this.turnCount++;
    this.totalCharsSent += chars;
  }

  recordReceive(chars: number): void {
    this.totalCharsReceived += chars;
  }

  reset(): void {
    this.turnCount = 0;
    this.totalCharsSent = 0;
    this.totalCharsReceived = 0;
    this.sessionStartedAt = new Date().toISOString();
  }

  getMetrics(): ChatMetrics {
    return {
      turnCount: this.turnCount,
      totalCharsSent: this.totalCharsSent,
      totalCharsReceived: this.totalCharsReceived,
      estimatedTokensSent: Math.ceil(this.totalCharsSent / 4),
      estimatedTokensReceived: Math.ceil(this.totalCharsReceived / 4),
      sessionStartedAt: this.sessionStartedAt,
    };
  }
}
