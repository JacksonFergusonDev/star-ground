import { fileURLToPath } from 'node:url';
import svelte from '@astrojs/svelte';
import { defineConfig, envField } from 'astro/config';

// Star Ground's accent lives at the repository root, beside the logo.
const repo = fileURLToPath(new URL('..', import.meta.url));
const proxy = { '/api': 'http://127.0.0.1:4501' };

export default defineConfig({
  site: 'https://star-ground.jacksonferguson.me',
  output: 'static',
  server: ({ command }) => ({
    host: '127.0.0.1',
    port: command === 'dev' ? 4500 : 4502,
  }),
  trailingSlash: 'always',
  integrations: [
    svelte(),
    {
      name: 'local-api-proxy',
      hooks: {
        'astro:config:setup': ({ command, updateConfig }) => {
          // Astro checks slashes before Vite's proxy; let FastAPI route local API requests.
          if (command === 'dev' || command === 'preview') {
            updateConfig({ trailingSlash: 'ignore' });
          }
        },
      },
    },
  ],
  env: {
    schema: {
      // Where the API lives: empty when the Docker image serves the site and
      // the API together; the Cloud Run service's URL for GitHub Pages.
      PUBLIC_API_URL: envField.string({ context: 'client', access: 'public', default: '' }),
    },
  },
  devToolbar: { enabled: false },
  vite: {
    resolve: {
      alias: {
        '@branding': `${repo}branding`,
      },
    },
    server: { strictPort: true, proxy, fs: { allow: [repo] } },
    preview: { strictPort: true, proxy },
  },
});
