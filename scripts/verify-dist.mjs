import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const distDir = resolve(root, 'dist');

function fail(message) {
    throw new Error(`[verify-dist] ${message}`);
}

function readJson(path) {
    return JSON.parse(readFileSync(path, 'utf8'));
}

function sri(bytes) {
    return `sha256-${createHash('sha256').update(bytes).digest('base64')}`;
}

const pkg = readJson(resolve(root, 'package.json'));
const pointer = readJson(resolve(distDir, 'mod.json'));
const registry = readJson(resolve(distDir, 'index.json'));

if (pointer.id !== 'video-player') fail(`unexpected mod id: ${pointer.id}`);
if (pointer.version !== pkg.version) fail(`package=${pkg.version}, dist/mod.json=${pointer.version}`);

const registryEntry = registry.find((entry) => entry.id === pointer.id);
if (!registryEntry) fail('dist/index.json has no video-player entry');
if (registryEntry.version !== pointer.version) {
    fail(`registry=${registryEntry.version}, dist/mod.json=${pointer.version}`);
}

const versionDir = resolve(distDir, `v${pointer.version}`);
const manifestPath = resolve(versionDir, 'manifest.json');
const lockPath = resolve(versionDir, 'lock.json');
const manifestBytes = readFileSync(manifestPath);
const manifest = JSON.parse(manifestBytes.toString('utf8'));
const lock = readJson(lockPath);

if (manifest.id !== pointer.id || lock.id !== pointer.id) fail('id mismatch in versioned artifacts');
if (manifest.version !== pointer.version || lock.version !== pointer.version) {
    fail('version mismatch in versioned artifacts');
}
if (lock.manifestIntegrity !== sri(manifestBytes)) fail('manifest integrity mismatch');

const manifestTypes = Object.keys(manifest.components).sort();
const lockTypes = Object.keys(lock.components).sort();
if (JSON.stringify(manifestTypes) !== JSON.stringify(lockTypes)) fail('manifest/lock component set mismatch');
if (JSON.stringify([...registryEntry.components].sort()) !== JSON.stringify(manifestTypes)) {
    fail('registry/manifest component set mismatch');
}

for (const type of lockTypes) {
    const locked = lock.components[type];
    const declared = manifest.components[type];
    if (locked.workerUrl !== declared.workerUrl) fail(`${type}: workerUrl mismatch`);
    if (JSON.stringify(locked.capabilities) !== JSON.stringify(declared.capabilities)) {
        fail(`${type}: capabilities mismatch`);
    }

    const workerPath = resolve(versionDir, locked.workerUrl);
    if (!workerPath.startsWith(`${versionDir}${sep}`)) fail(`${type}: workerUrl escapes version directory`);
    if (!existsSync(workerPath)) fail(`${type}: worker file not found (${locked.workerUrl})`);
    if (locked.integrity !== sri(readFileSync(workerPath))) fail(`${type}: worker integrity mismatch`);
}

console.log(`✅ video-player ${pointer.version}: ${lockTypes.length} workers and manifest verified`);
