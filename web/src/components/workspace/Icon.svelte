<script lang="ts">
  // house-style's icons for Svelte islands, inlined like its Icon.astro so they
  // take the text's colour. Decoration only: the label carries the meaning.
  import arrowLeft from 'house-style/icons/arrow-left.svg?raw';
  import arrowRight from 'house-style/icons/arrow-right.svg?raw';
  import arrowUpRight from 'house-style/icons/arrow-up-right.svg?raw';
  import close from 'house-style/icons/close.svg?raw';

  const icons = {
    'arrow-left': arrowLeft,
    'arrow-right': arrowRight,
    'arrow-up-right': arrowUpRight,
    close,
  } as const;

  let { name, size = 16 }: { name: keyof typeof icons; size?: number } = $props();

  const svg = $derived(
    icons[name].replace(
      /<svg([^>]*)>/,
      (_, attrs: string) =>
        `<svg${attrs} width="${size}" height="${size}" aria-hidden="true" focusable="false">`,
    ),
  );
</script>

<!-- eslint-disable-next-line svelte/no-at-html-tags -- house-style's own SVG files -->
{@html svg}
