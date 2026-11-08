import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { tmpdir, homedir } from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const directory = await mkdtemp(path.join(tmpdir(), 'stack-development-check-'))
let server
try {
  await mkdir(path.join(directory, 'client'))
  await mkdir(path.join(directory, 'server'))
  const files = {
    'package.json': JSON.stringify({ name: 'local-stack-check', private: true, type: 'module' }),
    'client/index.html': '<div id="root"></div><script type="module" src="/main.tsx"></script>',
    'client/main.tsx': "import { createRoot } from 'react-dom/client'; createRoot(document.getElementById('root')!).render(<h1>Ambiente local</h1>);",
    'vite.client.config.ts': "import {defineConfig} from 'vite'; import react from '@vitejs/plugin-react'; export default defineConfig({root:'client',plugins:[react()]});",
    'tsconfig.json': JSON.stringify({ compilerOptions: { target: 'ES2022', module: 'ESNext', moduleResolution: 'Bundler', jsx: 'react-jsx', strict: true, esModuleInterop: true, skipLibCheck: true, noEmit: true, types: ['node'] }, include: ['client', 'server'] }),
    'server/index.ts': `import { Hono } from 'hono';
import pg from 'pg';
import { homedir } from 'node:os';
import { join } from 'node:path';
process.loadEnvFile(join(homedir(), '.config/local-development/postgresql.env'));
const pool = new pg.Pool({connectionString: process.env.DATABASE_URL, max: 1, allowExitOnIdle: true});
const app = new Hono();
app.get('/health', async (c) => {
  const result = await pool.query('SELECT 1 AS value, current_database() AS database');
  return c.json({ok: true, ...result.rows[0]});
});
export default app;
`,
  }
  for (const [filename, contents] of Object.entries(files)) {
    await writeFile(path.join(directory, filename), contents)
  }
  const run = (command, args) => execFileSync(command, args, { cwd: directory, stdio: 'inherit' })
  console.log('Validando React/Vite, TypeScript e Hono/Vite com PostgreSQL em diretório temporário...')
  run('npm', ['install', '--no-fund', 'react', 'react-dom', 'vite', '@vitejs/plugin-react', 'hono', '@hono/vite-dev-server', 'pg', 'typescript', '@types/node', '@types/react', '@types/react-dom', '@types/pg'])
  run(path.join(directory, 'node_modules/.bin/tsc'), ['--noEmit'])
  run(path.join(directory, 'node_modules/.bin/vite'), ['build', '--config', 'vite.client.config.ts'])
  const require = createRequire(path.join(directory, 'package.json'))
  const { createServer } = await import(pathToFileURL(require.resolve('vite')).href)
  const { default: devServer } = await import(pathToFileURL(require.resolve('@hono/vite-dev-server')).href)
  server = await createServer({
    configFile: false,
    root: directory,
    plugins: [devServer({ entry: path.join(directory, 'server/index.ts') })],
    server: { host: '127.0.0.1', port: 0 },
  })
  await server.listen()
  const { port } = server.httpServer.address()
  const response = await fetch(`http://127.0.0.1:${port}/health`, { signal: AbortSignal.timeout(15000) })
  assert.equal(response.status, 200)
  const result = await response.json()
  assert.deepEqual(result, { ok: true, value: 1, database: process.env.USER })
  console.log('STACK_CHECK_OK: React/Vite compilou; TypeScript passou; Hono/Vite respondeu HTTP 200 e consultou PostgreSQL por TCP com senha.')
} finally {
  await server?.close()
  await rm(directory, { recursive: true, force: true })
}
