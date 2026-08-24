import { GlobalWorkerOptions, getDocument, TextLayer } from 'pdfjs-dist';

GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString();

export { getDocument, TextLayer };
