import { createRoot } from 'react-dom/client';

import { App } from './App.tsx';
import './styles/app.css';

const container = document.getElementById('root');
if (!container) throw new Error('#root is missing from index.html');

// Deliberately NOT wrapped in <StrictMode>.
//
// StrictMode double-invokes effects in development. For most components that is
// a useful correctness check, but this app's mount effect creates a WebGL
// context and allocates particle buffers — so double-invocation means building,
// tearing down, and rebuilding a GPU context on every mount, on a machine whose
// GPU is the weakest link. The one class of bug StrictMode would catch here
// (missing cleanup) is covered directly: sphere-engine.ts has an explicit
// dispose() that cancels the loop, disposes geometry and material, and forces
// context loss.
createRoot(container).render(<App />);
