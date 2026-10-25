import { fileURLToPath } from 'node:url';
import svelte from '@astrojs/svelte';
import { defineConfig } from 'astro/config';

// house-style is vendored once at the repository root (`just sync-house-style`);
// the site reads that copy rather than keeping its own.
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
        '@house': `${repo}house`,
        '@branding': `${repo}branding`,
      },
    },
    server: { fs: { allow: [repo] } },
  },
});
