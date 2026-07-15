import { existsSync, readdirSync, rmdirSync, unlinkSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptsDirectory = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(scriptsDirectory, '..');
const buildRoot = resolve(frontendRoot, 'dist', 'build');
const webOutput = resolve(frontendRoot, 'dist', 'build', 'h5');

if (dirname(webOutput) !== buildRoot) {
    throw new Error(`refusing to clean unexpected Web output: ${webOutput}`);
}

function removeTree(directory) {
    if (!existsSync(directory)) return;
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
        const child = resolve(directory, entry.name);
        if (entry.isDirectory() && !entry.isSymbolicLink()) {
            removeTree(child);
        } else {
            unlinkSync(child);
        }
    }
    rmdirSync(directory);
}

removeTree(webOutput);
if (existsSync(webOutput)) {
    throw new Error(`failed to clean Web output; check for a process using: ${webOutput}`);
}
