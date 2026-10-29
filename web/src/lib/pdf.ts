// Drawing a build document's pages with pdf.js. The library is about a
// megabyte, so it loads only when a builder opens a document to check.

import type { PDFDocumentProxy } from 'pdfjs-dist';

let library: Promise<typeof import('pdfjs-dist')> | null = null;

function pdfjs(): Promise<typeof import('pdfjs-dist')> {
  library ??= Promise.all([
    import('pdfjs-dist'),
    import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
  ]).then(([lib, worker]) => {
    lib.GlobalWorkerOptions.workerSrc = (worker as { default: string }).default;
    return lib;
  });
  return library;
}

/** Opens a PDF from its bytes. */
export async function openPdf(data: ArrayBuffer): Promise<PDFDocumentProxy> {
  const lib = await pdfjs();
  // pdf.js takes ownership of the buffer it's given, so it gets a copy.
  return lib.getDocument({ data: new Uint8Array(data.slice(0)) }).promise;
}

/** A page's size in points, as the parser measures its boxes. */
export interface PageSize {
  width: number;
  height: number;
}

/** Each page's size, in order. */
export async function pageSizes(pdf: PDFDocumentProxy): Promise<PageSize[]> {
  const sizes: PageSize[] = [];
  for (let n = 1; n <= pdf.numPages; n++) {
    const { width, height } = (await pdf.getPage(n)).getViewport({ scale: 1 });
    sizes.push({ width, height });
  }
  return sizes;
}

/**
 * Draws one page (counting from 0) to fill a canvas's width.
 *
 * @returns Cancels the drawing, for a page scrolled away or a newer size.
 */
export function drawPage(
  pdf: PDFDocumentProxy,
  index: number,
  canvas: HTMLCanvasElement,
  width: number,
): () => void {
  let task: { cancel: () => void } | null = null;
  let cancelled = false;
  void pdf.getPage(index + 1).then((page) => {
    if (cancelled) return;
    const base = page.getViewport({ scale: 1 });
    const ratio = window.devicePixelRatio || 1;
    const viewport = page.getViewport({ scale: (width / base.width) * ratio });
    canvas.width = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    const render = page.render({ canvas, viewport });
    task = render;
    render.promise.catch(() => {
      // Cancelled, or the page couldn't be drawn: the page stays blank.
    });
  });
  return () => {
    cancelled = true;
    task?.cancel();
  };
}
