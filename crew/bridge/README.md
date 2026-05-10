# @nse/vscode-bridge

Playwright-based automation bridge for VS Code's Chat panel. Enables programmatic interaction with Copilot agents (e.g., `@developer`) for CrewAI integration.

## Setup

```bash
cd crew/bridge
npm install
npm run build
```

## Usage

```typescript
// Local import — this package is not published to npm
import { createBridge } from '../bridge/src/index';

const bridge = await createBridge({
  workspacePath: '/path/to/project',
});

const response = await bridge.sendPrompt('developer', 'Explain this codebase');
console.log(response);

await bridge.close();
```

## Running the Smoke Test

The smoke test requires VS Code and GitHub Copilot to be installed locally:

```bash
BRIDGE_SMOKE=1 npx playwright test tests/smoke.test.ts
```

On Windows (PowerShell):

```powershell
$env:BRIDGE_SMOKE = "1"
npx playwright test tests/smoke.test.ts
```

## Selectors

All DOM selectors are centralized in `src/selectors.ts` with VS Code version annotations. When VS Code updates break automation, update selectors there.

## Architecture

```
src/
├── index.ts              # High-level createBridge() API
├── selectors.ts          # Centralized DOM selector registry
└── page-objects/
    ├── vscode-app.ts     # VS Code Electron lifecycle management
    └── chat-panel.ts     # Chat panel interaction (open, send, read)
```
