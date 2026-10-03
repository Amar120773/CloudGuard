import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
// Served with the app (hashed and cached like any asset) instead of from Google
// Fonts: no render-blocking third-party request, and visitors' browsers never
// contact a font CDN. The variable font also renders the design's 550/650 weights.
import '@fontsource-variable/outfit'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
