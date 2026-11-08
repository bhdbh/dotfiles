const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const { createIntegration, actions } = require('./extension');

async function main() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'test-yazi-extension-'));
  const calls = [], commands = new Map(), children = [];
  const nativeCommands = [
    'renameFile', 'explorer.newFile', 'explorer.newFolder',
    'workbench.action.files.newFile', 'filesExplorer.copy', 'filesExplorer.cut',
    'filesExplorer.paste', 'filesExplorer.cancelCut', 'revealFileInOS',
    'workbench.action.files.revealActiveFileInWindows',
  ];
  const originals = new Map(nativeCommands.map(id => [id, () => 'native']));
  originals.forEach((handler, id) => commands.set(id, handler));
  let clipboard = 'texto do usuário', selection = '';
  class Uri {
    constructor(fsPath, scheme = 'file') { Object.assign(this, { fsPath, scheme }); }
    static file(p) { return new Uri(p); }
    static isUri(p) { return p instanceof Uri; }
  }
  const vscode = {
    Uri,
    window: {
      createOutputChannel: () => ({ appendLine() {}, dispose() {} }),
      showErrorMessage: message => { throw new Error(message); },
    },
    workspace: { workspaceFolders: [{ uri: Uri.file(root) }], textDocuments: [] },
    env: { clipboard: {
      readText: async () => clipboard,
      writeText: async value => { clipboard = value; },
    } },
    commands: {
      registerCommand: (id, fn) => { commands.set(id, fn); return { dispose() {} }; },
      executeCommand: async id => { if (id === 'copyFilePath') clipboard = selection; },
    },
  };
  const launch = (command, args, options) => {
    const child = new EventEmitter();
    child.stderr = new EventEmitter();
    children.push(child);
    calls.push({ command, args, options });
    process.nextTick(() => child.emit('spawn'));
    return child;
  };
  const snapshot = async () => {
    const previous = clipboard;
    return { restore: async () => { clipboard = previous; } };
  };
  const { run } = createIntegration(vscode, { subscriptions: [] }, launch, snapshot);
  async function lastRequest() {
    return JSON.parse(await fs.readFile(calls.at(-1).args[2], 'utf8'));
  }
  try {
    const a = path.join(root, "espaço 'aspas' $literal.txt");
    const b = path.join(root, 'b.txt');
    await fs.writeFile(a, 'a');
    await fs.writeFile(b, 'b');
    await run('trash', Uri.file(a), [Uri.file(a), Uri.file(b)]);
    assert.deepEqual((await lastRequest()).targets, [a, b]);
    assert.equal((await lastRequest()).action, 'trash');
    assert.equal(calls.at(-1).command, path.join(os.homedir(), '.local/libexec/yazi-dialog'));
    assert.deepEqual(calls.at(-1).args.slice(0, 2), ['action', '--request-file']);
    assert(!calls.at(-1).args.some(arg => arg.includes('foot') || arg.includes('.cargo')));
    assert.equal(calls.at(-1).options.shell, undefined);
    assert(!calls.at(-1).args.includes(a)); // Paths are data, never shell code.
    console.log('PASS: context menu preserves multi-selection and literal filenames');

    selection = a;
    await run('trash');
    assert.deepEqual((await lastRequest()).targets, [a]);
    assert.equal(clipboard, 'texto do usuário');
    selection = '';
    clipboard = b; // A stale path on the clipboard must never become a target.
    await assert.rejects(run('trash'), /Select a file/);
    assert.equal(clipboard, b);
    console.log('PASS: keyboard resolves selection, restores text clipboard, rejects missing selection');

    const previousNativeCalls = calls.length;
    for (const action of ['rename', 'copy', 'cut', 'paste', 'create', 'mkdir', 'reveal', 'active']) {
      await assert.rejects(run(action, Uri.file(a)), /handled by VS Code/);
    }
    for (const [id, handler] of originals) assert.equal(commands.get(id), handler, id);
    assert.equal(calls.length, previousNativeCalls);
    const manifest = require('./package.json');
    assert(!manifest.contributes.keybindings.some(binding => binding.key === 'ctrl+v'));
    assert(!manifest.contributes.commands.some(command => command.command === 'localYazi.paste'));
    console.log('PASS: rename, move, create, copy, cut and paste keep their native handlers and shortcuts');

    vscode.workspace.textDocuments = [{ uri: Uri.file(a), isDirty: true }];
    const previousCalls = calls.length;
    await assert.rejects(run('delete', Uri.file(a)), /Save your changes/);
    await assert.rejects(run('trash', new Uri(a, 'vscode-remote')), /local files/);
    await assert.rejects(run('trash', Uri.file(path.join(root, 'missing'))), /ENOENT/);
    assert.equal(calls.length, previousCalls);
    for (const command of Object.keys(actions)) assert(commands.has(command));
    console.log('PASS: dirty, remote and missing files cannot launch a destructive action');
  } finally {
    children.forEach(child => child.emit('exit', 0));
    await fs.rm(root, { recursive: true, force: true });
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
