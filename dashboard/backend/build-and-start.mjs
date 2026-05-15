/**
 * build-and-start.mjs
 * Builds the React frontend (vite build) then starts the backend server.
 * The backend server.js will spawn the Python bot automatically.
 *
 * Usage (from dashboard/backend/):
 *   npm run start:full
 */
import { execSync, spawn } from 'child_process';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.join(__dirname, '../frontend');
const distDir = path.join(frontendDir, 'dist');

console.log('');
console.log('╔══════════════════════════════════════════════════╗');
console.log('║              ZiSi Full Startup                   ║');
console.log('╚══════════════════════════════════════════════════╝');
console.log('');

// ── Step 1: Build frontend ────────────────────────────────────────────────────
console.log('📦  Building frontend (vite build)...');
try {
  execSync('npm run build', { cwd: frontendDir, stdio: 'inherit' });
  console.log('✅  Frontend built →', distDir);
} catch (err) {
  console.error('❌  Frontend build failed:', err.message);
  console.error('    Starting server anyway — dashboard will use dev server on :3000');
}

// ── Step 2: Hand off to server.js (which also starts the bot) ─────────────────
console.log('');
console.log('🚀  Starting backend server + bot...');
console.log('');

const server = spawn(process.execPath, ['server.js'], {
  cwd: __dirname,
  stdio: 'inherit',
  env: { ...process.env },
});

server.on('exit', (code) => process.exit(code ?? 0));
process.on('SIGINT',  () => server.kill('SIGINT'));
process.on('SIGTERM', () => server.kill('SIGTERM'));
