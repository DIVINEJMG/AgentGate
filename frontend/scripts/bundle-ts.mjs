// Compiles TypeScript modules from src/ into one ES module for Node scripts and tests, without a dev server.
// Packages stay external and load from node_modules by file URL, so React in the bundle is the
// same instance a test imports. Stylesheets are left to Vite (dropped for the server build).
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {build} from 'vite';

const root = path.resolve(import.meta.dirname, '..');
const require = createRequire(path.join(root, 'package.json'));

/** bundle({name: 'src/site/contract/validate.ts'}) -> {name: module namespace} */
export async function bundle(entries) {
  const id = path.join(root, 'site-test-entry.ts');
  const source = Object.entries(entries).map(([name, file]) => `export * as ${name} from ${JSON.stringify(path.join(root, file))};`).join('\n');
  const result = await build({
    root, configFile: false, logLevel: 'error',
    plugins: [
      {name: 'test-entry', resolveId: s => (s === id ? id : undefined), load: s => (s === id ? source : undefined)},
      {
        name: 'external-packages', enforce: 'pre',
        resolveId(source) {
          if (source === id || source.startsWith('.') || source.startsWith('/') || source.startsWith('\0') || path.isAbsolute(source)) return null;
          if (source.startsWith('node:')) return {id: source, external: true};
          let resolved;
          try {resolved = require.resolve(source);} catch {return null;}
          if (resolved.endsWith('.css')) return null;
          return {id: pathToFileURL(resolved).href, external: true};
        },
      },
    ],
    build: {ssr: id, write: false, minify: false, rollupOptions: {output: {format: 'es', inlineDynamicImports: true}}},
  });
  const chunk = result.output.find(item => item.type === 'chunk' && item.isEntry);
  return import('data:text/javascript;base64,' + Buffer.from(chunk.code).toString('base64'));
}
