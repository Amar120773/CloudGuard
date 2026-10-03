import React, { Suspense, lazy, useCallback, useEffect, useState } from 'react'

import CommandPalette, { useCommandPalette } from './components/CommandPalette'
import { ChartSkeleton } from './components/Primitives'
import { MobileScrim, PAGES, Sidebar, TopBar } from './components/Shell'
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
// Read once: the welcome screen keeps the document's own title.
const HOME_TITLE = document.title

/** "/" is the welcome screen; every dashboard section is a page at its own path. */
function pathFor(page) {
  return page === HOME ? '/' : `/${page}`
}

function pageFromLocation() {
  // Links shared before sections had their own paths (#/cost) still land on
  // the right page; the address is then rewritten to the path form.
  const legacy = window.location.hash.replace(/^#\/?/, '')
  const path = legacy || window.location.pathname.replace(/^\/+|\/+$/g, '')
  if (!path) return HOME
  return VALID_PAGES.includes(path) ? path : 'overview'
}

function titleFor(page) {
  const meta = PAGES.find((p) => p.id === page)
  return meta ? `${meta.title} · CloudGuard` : HOME_TITLE
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

function Shell({ page, onNavigate, onHome }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const { open: paletteOpen, openPalette, closePalette } = useCommandPalette()

  useEffect(prefetchPages, [])

  return (
    <div className="app app-enter">
      <Sidebar
        page={page}
        onNavigate={onNavigate}
        onHome={onHome}
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
 * Each section is a real page: its own path, title and history entry, so it
 * can be bookmarked, opened in a new tab and reached with Back. The History
 * API does this without a router dependency; the static hosts rewrite page
 * paths to index.html (vercel.json, nginx.conf).
 */
export default function App() {
  const [page, setPage] = useState(pageFromLocation)

  useEffect(() => {
    // One address per page: legacy hash links and unknown paths are rewritten.
    const canonical = pathFor(pageFromLocation())
    if (window.location.pathname !== canonical || window.location.hash) {
      window.history.replaceState(null, '', canonical + window.location.search)
    }

    const onPopState = () => setPage(pageFromLocation())
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  useEffect(() => {
    document.title = titleFor(page)
  }, [page])

  const navigate = useCallback((next) => {
    const path = pathFor(next)
    if (window.location.pathname !== path) window.history.pushState(null, '', path)
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
        : <Shell page={page} onNavigate={navigate} onHome={() => navigate(HOME)} />}
    </DashboardProvider>
  )
}
