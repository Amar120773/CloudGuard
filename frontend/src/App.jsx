import React, { Suspense, lazy, useCallback, useEffect, useState } from 'react'

import CommandPalette, { useCommandPalette } from './components/CommandPalette'
import { ChartSkeleton } from './components/Primitives'
import { MobileScrim, Sidebar, TopBar } from './components/Shell'
import { DashboardProvider } from './state/DashboardContext'

// Route-level code splitting: the first paint only needs the Overview bundle.
// The other four pages (and the chart code only they use) load on navigation.
const Overview = lazy(() => import('./pages/Overview'))
const CostIntelligence = lazy(() => import('./pages/CostIntelligence'))
const SecurityAnalytics = lazy(() => import('./pages/SecurityAnalytics'))
const CloudResources = lazy(() => import('./pages/CloudResources'))
const AiInsights = lazy(() => import('./pages/AiInsights'))

const VALID_PAGES = ['overview', 'cost', 'security', 'resources', 'insights']

function pageFromHash() {
  const hash = window.location.hash.replace('#/', '').replace('#', '')
  return VALID_PAGES.includes(hash) ? hash : 'overview'
}

/** Shown while a route chunk is in flight; shaped like the content it replaces. */
function PageFallback() {
  return (
    <div className="stack" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading page…</span>
      <div className="tile-grid">
        {Array.from({ length: 4 }).map((_, i) => (
          <div className="tile" key={i}>
            <div className="skeleton skeleton-text" style={{ width: '45%' }} />
            <div className="skeleton skeleton-value" />
            <div className="skeleton skeleton-text" style={{ width: '70%' }} />
          </div>
        ))}
      </div>
      <ChartSkeleton height={280} />
    </div>
  )
}

/**
 * Hash routing keeps the app a single static bundle (no router dependency, no
 * server rewrite rules) while still giving each view a shareable URL.
 */
function Shell() {
  const [page, setPage] = useState(pageFromHash)
  const [menuOpen, setMenuOpen] = useState(false)
  const { open: paletteOpen, openPalette, closePalette } = useCommandPalette()

  useEffect(() => {
    const onHashChange = () => setPage(pageFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const navigate = useCallback((next) => {
    window.location.hash = `#/${next}`
    setPage(next)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [])

  return (
    <div className="app">
      <Sidebar
        page={page}
        onNavigate={navigate}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
      />
      <MobileScrim open={menuOpen} onClose={() => setMenuOpen(false)} />

      <div className="main">
        <TopBar
          page={page}
          onToggleMenu={() => setMenuOpen((v) => !v)}
          onOpenPalette={openPalette}
        />
        <main className="content">
          <Suspense fallback={<PageFallback />}>
            {page === 'overview' && <Overview onNavigate={navigate} />}
            {page === 'cost' && <CostIntelligence />}
            {page === 'security' && <SecurityAnalytics />}
            {page === 'resources' && <CloudResources />}
            {page === 'insights' && <AiInsights />}
          </Suspense>
        </main>
      </div>

      <CommandPalette
        open={paletteOpen}
        onClose={closePalette}
        onNavigate={navigate}
        currentPage={page}
      />
    </div>
  )
}

export default function App() {
  return (
    <DashboardProvider>
      <Shell />
    </DashboardProvider>
  )
}
