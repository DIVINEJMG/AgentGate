import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import {Preview} from './Preview';

import type {DefaultPage} from '../content/defaults';

const query = new URLSearchParams(window.location.search);
const page = (query.get('page') || 'home') as DefaultPage;
const site = query.get('site') === 'limits' ? 'limits' : undefined;
createRoot(document.getElementById('root')!).render(<StrictMode><Preview page={page} site={site} /></StrictMode>);
