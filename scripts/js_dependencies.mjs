// Only used by maintainers. Builds and consumers do not run JavaScript tooling.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { init, parse } from 'es-module-lexer';

await init;
const { files, entrypoints } = JSON.parse(readFileSync(0, 'utf8'));
const visited = new Set();

function resolve(specifier, importer) {
  let target;
  if (specifier === 'three') {
    target = 'build/three.module.js';
  } else if (specifier.startsWith('three/addons/')) {
    target = 'examples/jsm/' + specifier.slice('three/addons/'.length);
  } else if (specifier.startsWith('./') || specifier.startsWith('../')) {
    target = path.posix.join(path.posix.dirname(importer), specifier);
  } else {
    throw new Error(`Unsupported module ${JSON.stringify(specifier)} in ${importer}`);
  }
  if (!/^(build|examples\/jsm)\/.+\.js$/.test(target) ||
      target.split('/').some(part => part === '..' || part === '.') ||
      /[\\?#%]/.test(target)) {
    throw new Error(`Unsafe or unsupported module path: ${target}`);
  }
  return target;
}

function visit(filename) {
  if (visited.has(filename)) return;
  if (!Object.hasOwn(files, filename)) throw new Error(`Missing module: ${filename}`);
  visited.add(filename);
  const [imports] = parse(files[filename], filename);
  for (const entry of imports) {
    if (entry.type === 'import-meta') continue;
    if (typeof entry.specifier !== 'string') {
      throw new Error(`Nonliteral dynamic import in ${filename}; manual review required`);
    }
    visit(resolve(entry.specifier, filename));
  }
}

for (const entrypoint of entrypoints) visit(entrypoint);
process.stdout.write(JSON.stringify([...visited].sort()));
