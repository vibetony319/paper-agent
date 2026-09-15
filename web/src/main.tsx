import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import './pdfjs';
import './styles.css';
import { App } from './App';

const rootElement = document.getElementById('root');

if (rootElement === null) {
  throw new Error('Root element is missing.');
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
