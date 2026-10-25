import { defineConfig } from '@hey-api/openapi-ts';

// The API's types, generated from the committed schema (`just openapi` refreshes both).
export default defineConfig({
  input: '../openapi.json',
  output: 'src/lib/api',
  plugins: ['@hey-api/typescript'],
});
