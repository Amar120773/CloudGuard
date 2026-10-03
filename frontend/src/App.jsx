import React, { Suspense, lazy, useCallback, useEffect, useState } from 'react'

import CommandPalette, { useCommandPalette } from './components/CommandPalette'
import { ChartSkeleton } from './components/Primitives'
import { MobileScrim, Sidebar, TopBar } from './components/Shell'
// Eager, not lazy: the welcome screen is the first paint, and a loading
// fallback in front of it would defeat the point of having one.
import Landing from './pages/Landing'
import { DashboardProvider } from './state/DashboardContext'

// Route-level code splitting: the first paint only needs the Overview bundle.
// The other four pages (and the chart code only they use) load on navigation.
const PAGE_LOADERS = {
  overview: () => import('./pages/Overview'),
  cost: () => import('./pages/CostIntelligence'),
  security: () => import('./pages/SecurityAnalytics'),
  resources: () => import('./pages/CloudResources'),
  insights: () => import('./pages/AiInsights'),
}
const Overview = lazy(PAGE_LOADERS.overview)
const CostIntelligence = lazy(PAGE_LOADERS.cost)
const SecurityAnalytics = lazy(PAGE_LOADERS.security)
const CloudResources = lazy(PAGE_LOADERS.resources)
const AiInsights = lazy(PAGE_LOADERS.insights)

const HOME = 'home'
const VALID_PAGES = Object.keys(PAGE_LOADERS)

/** No hash is the welcome screen; any other hash is a dashboard page. */
function pageFromHash() {
  const hash = window.location.hash.replace('#/', '').replace('#', '')
  if (!hash) return HOME
  return VALID_PAGES.includes(hash) ? hash : 'overview'
}

/**
 * Fetch every page chunk once the browser is idle. Each page is small, and
 * having them in memory is what keeps navigation from flashing a skeleton.
 */
function prefetchPages() {
  const load = () => Object.values(PAGE_LOADERS).forEach((loader) => loader())
  if ('requestIdleCallback' in window) window.requestIdleCallback(load, { timeout: 3000 })
  else setTimeout(load, 1200)
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

function Shell({ page, onNavigate }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const { open: paletteOpen, openPalette, closePalette } = useCommandPalette()

  useEffect(prefetchPages, [])

  return (
    <div className="app app-enter">
      <Sidebar
        page={page}
        onNavigate={onNavigate}
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
            {/* Keyed by page so each one fades in as it arrives. */}
            <div key={page} className="page-enter">
              {page === 'overview' && <Overview onNavigate={onNavigate} />}
              {page === 'cost' && <CostIntelligence />}
              {page === 'security' && <SecurityAnalytics />}
              {page === 'resources' && <CloudResources />}
              {page === 'insights' && <AiInsights />}
            </div>
          </Suspense>
        </main>
      </div>

      <CommandPalette
        open={paletteOpen}
        onClose={closePalette}
        onNavigate={onNavigate}
        currentPage={page}
      />
    </div>
  )
}

/**
 * Hash routing keeps the app a single static bundle (no router dependency, no
 * server rewrite rules) while still giving each view a shareable URL.
 */
export default function App() {
  const [page, setPage] = useState(pageFromHash)

  useEffect(() => {
    const onHashChange = () => setPage(pageFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const navigate = useCallback((next) => {
    window.location.hash = next === HOME ? '#/' : `#/${next}`
    setPage(next)
    // Instant, not smooth: the new page animates in on its own, and scrolling
    // smoothly across content that is being replaced reads as a jolt.
    window.scrollTo({ top: 0 })
  }, [])

  // The provider sits above the welcome screen on purpose: its first requests
  // wake a sleeping API while the visitor is still reading.
  return (
    <DashboardProvider>
      {page === HOME
        ? <Landing onEnter={() => navigate('overview')} />
        : <Shell page={page} onNavigate={navigate} />}
    </DashboardProvider>
  )
}
