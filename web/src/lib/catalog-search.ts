// Searching the catalog from the add box: the catalog in brief
// (`/catalog/index.json`), and the builds a search names, best first.

/** A catalog build, as the add box lists it. */
export interface CatalogItem {
  /** '<publisher>/<name>', the build's address in the catalog. */
  id: string;
  title: string;
  /** The publisher as the catalog names it ('PedalPCB'). */
  publisher: string;
  /** Its kinds of effect as the catalog names them ('Fuzz'). */
  effects: string[];
  /** Parts in its largest build. */
  parts: number;
  /** The build document's link, which adding it reads. */
  document: string;
  /** Whether it's a good first build (`isFirstBuild`). */
  first: boolean;
}

/** Letters and digits only, in lower case: 'Fuzz-Face' and 'fuzz face' read alike. */
const fold = (text: string): string =>
  text
    .normalize('NFKD')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim();

/**
 * The builds whose name, publisher or effects hold every word searched for:
 * names that start with the search first, then names that hold it, then the
 * rest, each in the catalog's order.
 *
 * @param items The catalog, in its order.
 * @param query What the builder typed.
 * @param limit How many to return at most.
 */
export function searchCatalog(items: CatalogItem[], query: string, limit = 8): CatalogItem[] {
  const words = fold(query).split(' ').filter(Boolean);
  if (!words.length) return [];
  const whole = words.join(' ');
  const scored = items.flatMap((item) => {
    const title = fold(item.title);
    const all = `${title} ${fold(item.publisher)} ${item.effects.map(fold).join(' ')}`;
    if (!words.every((w) => all.includes(w))) return [];
    const rank = title.startsWith(whole) ? 0 : title.includes(whole) ? 1 : 2;
    return [{ item, rank }];
  });
  return scored
    .sort((a, b) => a.rank - b.rank)
    .slice(0, limit)
    .map((s) => s.item);
}
