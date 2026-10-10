import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react(),{
    name:'audoryn-public-runtime',
    configureServer(server) {
      server.middlewares.use(async(request,response,next)=>{
        const path=(request.url||'/').split('?')[0];
        if(path.startsWith('/src/')||path.startsWith('/@')||path.startsWith('/node_modules/')||/\.[a-z0-9]+$/i.test(path)&&!['/sitemap.xml','/robots.txt'].includes(path))return next();
        try{
          const module=await server.ssrLoadModule('/src/public/entry-server.tsx');
          const {readFile}=await import('node:fs/promises');
          const template=await server.transformIndexHtml(request.url||'/',await readFile(new URL('./index.html',import.meta.url),'utf8'));
          const result=await module.publicResponse({url:request.url||'/',env:{...loadEnv(server.config.mode,server.config.root,''),...process.env},template});
          response.statusCode=result.status;for(const [name,value]of Object.entries(result.headers))response.setHeader(name,value as string);response.end(result.body);
        }catch(error){server.ssrFixStacktrace(error as Error);next(error);}
      });
    },
  }],
  base: '/',
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      maxParallelFileOps: 128,
    },
  },
});
