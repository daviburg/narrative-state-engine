import * as child_process from 'child_process';

/** Check if VS Code is already running (would block Electron launch). */
export function isVSCodeRunning(): boolean {
  try {
    if (process.platform === 'win32') {
      const out = child_process.execSync(
        'tasklist /FI "IMAGENAME eq Code.exe" /NH',
        { encoding: 'utf8' },
      );
      return out.includes('Code.exe');
    }
    if (process.platform === 'darwin') {
      // macOS: the process name is "Electron" launched from Code.app
      const out = child_process.execSync(
        'pgrep -af "Visual Studio Code|Code Helper"',
        { encoding: 'utf8' },
      );
      return out.trim().length > 0;
    }
    // Linux: process name is "code" (lowercase)
    const out = child_process.execSync('pgrep -x code', { encoding: 'utf8' });
    return out.trim().length > 0;
  } catch {
    return false;
  }
}
