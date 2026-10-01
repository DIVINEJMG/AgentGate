import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import ScrollControl from './components/ScrollControl';
import './index.css';
import './scroll-system.css';
import './input-focus.css';

createRoot(document.getElementById('root')!).render(
    <StrictMode>
        <App />
        <ScrollControl />
    </StrictMode>
);
