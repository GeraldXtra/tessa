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
