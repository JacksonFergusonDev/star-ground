import { readdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import svelte from '@astrojs/svelte';
import { defineConfig, envField } from 'astro/config';
import { buildBoard } from './src/lib/circuit-field/layout';

// Star Ground's accent lives at the repository root, beside the logo.
const repo = fileURLToPath(new URL('..', import.meta.url));
const proxy = { '/api': 'http://127.0.0.1:4501' };

/**
 * Makes each built page under /embed/ one file another site can fetch and
 * show: its stylesheets go into the page, and the fonts and images they and
 * the page name go in as data. Those pages run no scripts and load nothing,
 * and a build that breaks either fails here.
 */
async function selfContained(dist) {
  const read = (path) => readFile(new URL(`.${path}`, dist));
  const types = { woff2: 'font/woff2', svg: 'image/svg+xml', png: 'image/png', webp: 'image/webp' };
  // A file of the site, as a data: URI.
  const data = async (path) => {
    const type = types[path.split('.').pop()];
    if (!type) throw new Error(`An embed page names ${path}, which can't be written into it`);
    return `data:${type};base64,${(await read(path)).toString('base64')}`;
  };
  // Every `url(/…)` in a stylesheet, quoted or not.
  const inlineUrls = async (css) => {
    for (const [, path] of css.matchAll(/url\(\s*['"]?(\/[^)'"\s]+)['"]?\s*\)/g)) {
      css = css.replaceAll(path, await data(path));
    }
    return css;
  };
  const embeds = new URL('embed/', dist);
  const pages = (await readdir(embeds, { recursive: true })).filter((f) => f.endsWith('.html'));
  for (const name of pages) {
    const page = new URL(name, embeds);
    let html = await readFile(page, 'utf8');
    if (/<script\b/.test(html)) throw new Error(`embed/${name} has a script`);
    // A frame never shows its page's icon.
    html = html.replace(/<link rel="icon"[^>]*>/, '');
    for (const [link, href] of html.matchAll(/<link rel="stylesheet" href="([^"]+)"[^>]*>/g)) {
      const css = await inlineUrls(String(await read(href)));
      html = html.replace(link, () => `<style>${css}</style>`);
    }
    html = await inlineUrls(html);
    for (const [, path] of html.matchAll(/<img\b[^>]*\ssrc="(\/[^"]+)"/g)) {
      html = html.replaceAll(`src="${path}"`, `src="${await data(path)}"`);
    }
    if (/<link\b|\/_astro\/|\ssrc="\/|url\(\s*['"]?\//.test(html)) {
      throw new Error(`embed/${name} still loads a file`);
    }
    await writeFile(page, html);
  }
}

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
    {
      name: 'self-contained-embeds',
      hooks: {
        'astro:build:done': ({ dir }) => selfContained(dir),
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
    plugins: [
      {
        name: 'circuit-board',
        resolveId(id) {
          if (id === 'virtual:circuit-board') return '\0virtual:circuit-board';
        },
        load(id) {
          if (id === '\0virtual:circuit-board') {
            // Routing is deterministic and independent of the viewport. Do it
            // at build time, rather than making every visitor wait for it.
            return `export default ${JSON.stringify(buildBoard())};`;
          }
        },
      },
    ],
    resolve: {
      alias: {
        '@branding': `${repo}branding`,
      },
    },
    server: { strictPort: true, proxy, fs: { allow: [repo] } },
    preview: { strictPort: true, proxy },
  },
});
