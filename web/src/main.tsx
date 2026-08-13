import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import './styles.css';

function App() {
  return <main>Paper Reading Workbench</main>;
}

const rootElement = document.getElementById('root');

if (rootElement === null) {
  throw new Error('Root element is missing.');
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
