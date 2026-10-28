import { fileURLToPath } from 'node:url';
import svelte from '@astrojs/svelte';
import { defineConfig, envField } from 'astro/config';

// Star Ground's accent lives at the repository root, beside the logo.
const repo = fileURLToPath(new URL('..', import.meta.url));

export default defineConfig({
  site: 'https://star-ground.jacksonferguson.me',
  output: 'static',
  trailingSlash: 'always',
  integrations: [svelte()],
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
    server: { fs: { allow: [repo] } },
  },
});
