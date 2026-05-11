/**
 * HTTP server for the VS Code automation bridge.
 *
 * Exposes REST endpoints for CrewAI (Python) to control VS Code's
 * Copilot chat via Playwright automation.
 *
 * Binds to 127.0.0.1 only — no network exposure.
 * Uses Node's built-in http module — no external dependencies.
 */

import * as http from 'http';
import { createBridge, type VSCodeBridge, type BridgeOptions } from './index';
import type { ChatMetrics } from './chat-metrics';

// --- Types ---

interface StartBody {
  workspacePath?: string;
  defaultTimeout?: number;
}

interface SendBody {
  agent: string;
  prompt: string;
  timeout?: number;
}

interface JsonResponse {
  status?: string;
  response?: string;
  error?: string;
  [key: string]: unknown;
}

// --- State ---

let bridge: VSCodeBridge | null = null;
let metricsAccessor: (() => ChatMetrics) | null = null;

// --- Helpers ---

function jsonResponse(res: http.ServerResponse, statusCode: number, body: JsonResponse | ChatMetrics): void {
  const payload = JSON.stringify(body);
  res.writeHead(statusCode, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(payload),
  });
  res.end(payload);
}

function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    const MAX_BODY = 1024 * 1024; // 1 MB limit

    req.on('data', (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_BODY) {
        req.destroy();
        reject(new Error('Request body too large'));
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

function parseJson<T>(raw: string): T {
  if (!raw.trim()) return {} as T;
  return JSON.parse(raw) as T;
}

// --- Route handlers ---

async function handleSessionStart(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  if (bridge) {
    jsonResponse(res, 409, { error: 'Session already active. Close it first.' });
    return;
  }

  const body = parseJson<StartBody>(await readBody(req));
  const options: BridgeOptions = {};
  if (body.workspacePath) options.workspacePath = body.workspacePath;
  if (body.defaultTimeout) options.defaultTimeout = body.defaultTimeout;

  // createBridge returns a VSCodeBridge; we also need the metrics accessor.
  // Import createBridgeWithMetrics which we'll add to index.ts.
  const { createBridgeWithMetrics } = await import('./index');
  const result = createBridgeWithMetrics(options);
  bridge = await result.bridge;
  metricsAccessor = result.getMetrics;

  jsonResponse(res, 200, { status: 'ok' });
}

async function handleChatSend(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  if (!bridge) {
    jsonResponse(res, 503, { error: 'No active session. Call /session/start first.' });
    return;
  }

  const body = parseJson<SendBody>(await readBody(req));
  if (!body.agent || typeof body.agent !== 'string') {
    jsonResponse(res, 400, { error: 'Missing or invalid "agent" field.' });
    return;
  }
  if (!body.prompt || typeof body.prompt !== 'string') {
    jsonResponse(res, 400, { error: 'Missing or invalid "prompt" field.' });
    return;
  }

  const response = await bridge.sendPrompt(body.agent, body.prompt);
  jsonResponse(res, 200, { response });
}

async function handleChatNew(_req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  if (!bridge) {
    jsonResponse(res, 503, { error: 'No active session. Call /session/start first.' });
    return;
  }

  await bridge.newChat();
  jsonResponse(res, 200, { status: 'ok' });
}

function handleChatMetrics(_req: http.IncomingMessage, res: http.ServerResponse): void {
  if (!bridge || !metricsAccessor) {
    jsonResponse(res, 503, { error: 'No active session. Call /session/start first.' });
    return;
  }

  jsonResponse(res, 200, metricsAccessor());
}

async function handleSessionClose(_req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  if (!bridge) {
    jsonResponse(res, 200, { status: 'ok' }); // idempotent
    return;
  }

  await bridge.close();
  bridge = null;
  metricsAccessor = null;
  jsonResponse(res, 200, { status: 'ok' });
}

function handleHealth(_req: http.IncomingMessage, res: http.ServerResponse): void {
  jsonResponse(res, 200, { status: 'ok' });
}

// --- Router ---

type RouteHandler = (req: http.IncomingMessage, res: http.ServerResponse) => Promise<void> | void;

const routes: Record<string, Record<string, RouteHandler>> = {
  'POST': {
    '/session/start': handleSessionStart,
    '/chat/send': handleChatSend,
    '/chat/new': handleChatNew,
    '/session/close': handleSessionClose,
  },
  'GET': {
    '/chat/metrics': handleChatMetrics,
    '/health': handleHealth,
  },
};

async function handleRequest(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  const method = req.method ?? 'GET';
  const url = req.url ?? '/';

  const handler = routes[method]?.[url];
  if (!handler) {
    jsonResponse(res, 404, { error: `Not found: ${method} ${url}` });
    return;
  }

  try {
    await handler(req, res);
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`Error handling ${method} ${url}:`, message);
    jsonResponse(res, 500, { error: message });
  }
}

// --- Server lifecycle ---

function parsePort(args: string[]): number {
  const portEnv = process.env.PORT;
  if (portEnv) {
    const parsed = parseInt(portEnv, 10);
    if (!isNaN(parsed) && parsed > 0 && parsed < 65536) return parsed;
  }

  const portIdx = args.indexOf('--port');
  if (portIdx !== -1 && args[portIdx + 1]) {
    const parsed = parseInt(args[portIdx + 1], 10);
    if (!isNaN(parsed) && parsed > 0 && parsed < 65536) return parsed;
  }

  return 7400;
}

export function startServer(port?: number): http.Server {
  const resolvedPort = port ?? parsePort(process.argv);

  const server = http.createServer((req, res) => {
    handleRequest(req, res).catch((err) => {
      console.error('Unhandled error:', err);
      if (!res.headersSent) {
        jsonResponse(res, 500, { error: 'Internal server error' });
      }
    });
  });

  server.listen(resolvedPort, '127.0.0.1', () => {
    console.log(`NSE Bridge server listening on http://127.0.0.1:${resolvedPort}`);
  });

  return server;
}

// --- Graceful shutdown ---

function setupShutdownHandlers(server: http.Server): void {
  let shuttingDown = false;

  const shutdown = async (signal: string) => {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log(`\n${signal} received — shutting down...`);

    // Close active bridge session
    if (bridge) {
      try {
        await bridge.close();
      } catch (err) {
        console.error('Error closing bridge during shutdown:', err);
      }
      bridge = null;
      metricsAccessor = null;
    }

    server.close(() => {
      console.log('Server closed.');
      process.exit(0);
    });

    // Force exit after 10s if graceful close hangs
    setTimeout(() => {
      console.error('Forced exit after timeout.');
      process.exit(1);
    }, 10_000).unref();
  };

  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
}

// --- Main entry point ---

if (require.main === module) {
  const server = startServer();
  setupShutdownHandlers(server);
}
