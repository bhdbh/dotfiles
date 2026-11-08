const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const readline = require('node:readline');

async function preserveClipboard() {
  const child = spawn('/usr/bin/python3', [path.join(__dirname, 'clipboard.py')], {
    stdio: ['pipe', 'pipe', 'pipe'],
    detached: true,
  });
  let error = '';
  child.once('error', exception => { error = exception.message; child.stdout.destroy(); });
  child.stderr.on('data', data => { error = (error + data).slice(-2000); });
  const lines = readline.createInterface({ input: child.stdout })[Symbol.asyncIterator]();
  async function expect(value) {
    const timeout = setTimeout(() => child.kill(), 10000);
    try {
      const line = await lines.next();
      if (line.value !== value) throw new Error(error.trim() || 'Could not preserve the clipboard.');
    } finally {
      clearTimeout(timeout);
    }
  }
  await expect('ready');
  return {
    async restore() {
      child.stdin.end('restore\n');
      await expect('restored');
      // The helper owns the restored formats until the next clipboard change.
      child.stdin.destroy();
      child.stdout.destroy();
      child.stderr.destroy();
      child.unref();
    },
  };
}

const actions = {
  moveFileToTrash: 'trash',
  deleteFile: 'delete',
  'localYazi.manage': 'manage',
};
const supportedActions = new Set(Object.values(actions));

function createIntegration(vscode, context, launch = spawn, snapshotClipboard = preserveClipboard) {
  let selectionQueue = Promise.resolve();
  const output = vscode.window.createOutputChannel('Yazi File Actions');
  context.subscriptions.push(output);

  async function resources(action, resource, selected) {
    if (Array.isArray(selected) && selected.length) return selected;
    if (vscode.Uri.isUri(resource)) return [resource];
    // The public API has no Explorer selection getter. This built-in command
    // resolves the focused selection; restore the clipboard immediately.
    const readSelection = async () => {
      const snapshot = await snapshotClipboard();
      try {
        await vscode.env.clipboard.writeText('');
        await vscode.commands.executeCommand('copyFilePath');
        const text = await vscode.env.clipboard.readText();
        return text.split('\n').filter(p => path.isAbsolute(p)).map(p => vscode.Uri.file(p));
      } finally {
        await snapshot.restore();
      }
    };
    const result = selectionQueue.then(readSelection);
    selectionQueue = result.catch(() => {});
    const uris = await result;
    if (uris.length) return uris;
    const fallback = vscode.workspace.workspaceFolders?.[0]?.uri;
    if (action === 'manage') {
      return [fallback || vscode.Uri.file(os.homedir())];
    }
    return [];
  }

  async function run(action, resource, selected) {
    if (!supportedActions.has(action)) {
      throw new Error('This action is handled by VS Code.');
    }
    const uris = await resources(action, resource, selected);
    if (!uris.length) throw new Error('Select a file or folder in Explorer.');
    if (uris.some(uri => uri.scheme !== 'file')) {
      throw new Error('Local Yazi can only manage local files.');
    }
    const targets = [...new Set(uris.map(uri => path.resolve(uri.fsPath)))];
    const stats = await Promise.all(targets.map(target => fs.lstat(target)));
    if (action === 'trash' || action === 'delete') {
      const dirty = vscode.workspace.textDocuments.find(doc => doc.isDirty &&
        targets.some(target => doc.uri.fsPath === target || doc.uri.fsPath.startsWith(target + path.sep)));
      if (dirty) throw new Error('Save your changes before managing this file in Yazi.');
    }
    const cwd = action === 'manage' && stats[0].isDirectory() ? targets[0] : path.dirname(targets[0]);
    const request = { action, targets, cwd };
    const requestDir = await fs.mkdtemp(path.join(os.tmpdir(), 'vscode-yazi-'));
    const requestFile = path.join(requestDir, 'request.json');
    await fs.writeFile(requestFile, JSON.stringify(request), { mode: 0o600 });
    await new Promise((resolve, reject) => {
      const child = launch(path.join(os.homedir(), '.local/libexec/yazi-dialog'), [
        'action', '--request-file', requestFile,
      ], {
        cwd,
        env: { ...process.env },
        stdio: ['ignore', 'ignore', 'pipe'],
      });
      let error = '';
      child.stderr?.on('data', data => { error = (error + data).slice(-4000); });
      child.once('error', error => {
        fs.rm(requestDir, { recursive: true, force: true }).catch(() => {});
        reject(error);
      });
      child.once('spawn', resolve);
      child.once('exit', code => {
        fs.rm(requestDir, { recursive: true, force: true }).catch(() => {});
        if (code) vscode.window.showErrorMessage(`Yazi: ${error.trim() || `The dialog exited with code ${code}.`}`);
        vscode.commands.executeCommand('workbench.files.action.refreshFilesExplorer').catch(() => {});
      });
    });
    output.appendLine(`${action}: ${JSON.stringify(targets)}`);
  }

  for (const [command, action] of Object.entries(actions)) {
    context.subscriptions.push(vscode.commands.registerCommand(command,
      (...args) => run(action, ...args).catch(error => vscode.window.showErrorMessage(`Yazi: ${error.message}`))));
  }
  output.appendLine('Integration active: deletion uses the Yazi desktop-dialog plugin; other actions use VS Code.');
  return { run };
}

function activate(context) {
  const vscode = require('vscode');
  if (process.platform !== 'linux' || vscode.env.remoteName) return;
  createIntegration(vscode, context);
}

module.exports = { activate, createIntegration, actions };
