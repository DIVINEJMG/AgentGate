import {build} from 'vite';
import path from 'node:path';
import {readFile,writeFile,unlink} from 'node:fs/promises';
const root=path.resolve(import.meta.dirname,'..');
await build({root,build:{manifest:true}});
const template=await readFile(path.join(root,'dist/index.html'));
await build({root,build:{ssr:'src/public/entry-server.tsx',outDir:'server-dist',rollupOptions:{output:{entryFileNames:'public-site.mjs'}}}});
// Vercel serves existing static files before rewrites. Keep the SSR template private
// so requests to / always reach the renderer rather than an unrendered SPA shell.
await writeFile(path.join(root,'server-dist/template.html'),template);
await unlink(path.join(root,'dist/index.html'));
