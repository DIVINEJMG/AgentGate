import { StrictMode } from 'react';
import { createRoot, hydrateRoot } from 'react-dom/client';
import App from './App';
import {readBootstrap,routePath} from './public/content/runtime';
import {getPendingInvitationCode} from './lib/commercialApi';
import ScrollControl from './components/ScrollControl';
import './index.css';
import './scroll-system.css';
import './input-focus.css';

const application=(
    <StrictMode>
        <App />
        <ScrollControl />
    </StrictMode>
);
const root=document.getElementById('root')!;
// Legacy public hashes and pending invitations can select another screen than
// the server rendered. Preserve those client entry points without mismatched hydration.
const bootstrap=readBootstrap();
if(bootstrap?.path===routePath(window.location)&&!getPendingInvitationCode())hydrateRoot(root,application);
else createRoot(root).render(application);
