import { fileURLToPath } from 'node:url';
import svelte from '@astrojs/svelte';
import { defineConfig } from 'astro/config';

// Star Ground's accent lives at the repository root, beside the logo.
const repo = fileURLToPath(new URL('..', import.meta.url));

export default defineConfig({
  site: 'https://star-ground.jacksonferguson.me',
  output: 'static',
  trailingSlash: 'always',
  integrations: [svelte()],
  devToolbar: { enabled: false },
  vite: {
    resolve: {
      alias: {
        '@branding': `${repo}branding`,
      },
    },
    server: { fs: { allow: [repo] } },
  },
});
