import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import './i18n/config'; // Initialize i18n
import { installApiAuthInterceptor } from './apiAuth';
import App from './App';

// Patch fetch to attach the API key before any component issues requests
installApiAuthInterceptor();

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

