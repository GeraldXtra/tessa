/**
 * A LAST-RESORT NET FOR PROMISE REJECTIONS NOBODY CAUGHT.
 *
 * Every known rejection path in this surface is now handled at its own call
 * site, which is where a useful message can be written. This exists for the
 * ones nobody thought of: without it an unhandled rejection in the renderer is
 * completely invisible, and in a packaged build console forwarding is off, so
 * it would not even reach a log.
 */
window.addEventListener('unhandledrejection', (e) => {
  console.log(`UNHANDLED REJECTION ${String((e.reason as Error)?.stack ?? e.reason)}`)
})
window.addEventListener('error', (e) => {
  console.log(`UNCAUGHT ERROR ${e.message} at ${e.filename}:${e.lineno}`)
})

import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import './styles/app.css'

const el = document.getElementById('root')
if (!el) throw new Error('#root missing')
createRoot(el).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
