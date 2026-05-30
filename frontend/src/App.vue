<template>
  <div class="app">
    <!-- Hidden router-view to make vue-router work -->
    <router-view v-slot="{ Component }">
      <component :is="Component" v-show="false" />
    </router-view>

    <div v-if="showProgressPanel" class="activity-overlay">
      <div class="activity-card">
        <div class="activity-header">
          <span>Library activity</span>
          <span v-if="!wsConnected" class="activity-connection">Reconnecting...</span>
        </div>
        <div v-for="root in progressRoots" :key="root.rootId" class="activity-root" :class="root.toneClass">
          <div class="activity-root-title">{{ root.rootLabel }}</div>
          <div v-if="root.scanTarget" class="activity-root-target">{{ root.scanTarget }}</div>
          <div class="activity-phase-row">
            <span class="activity-phase">{{ root.phaseLabel }}</span>
            <span v-if="root.progressLabel" class="activity-progress-label">{{ root.progressLabel }}</span>
          </div>
          <div class="activity-bar" :class="{ 'activity-bar-indeterminate': !root.isDeterminate }">
            <div
              class="activity-bar-fill"
              :style="root.isDeterminate ? { width: `${root.progressPercent}%` } : undefined"
            ></div>
          </div>
          <div v-if="root.phaseDetail" class="activity-detail">{{ root.phaseDetail }}</div>
        </div>
      </div>
    </div>

    <div v-if="libraryUpdateMessage" class="library-update-toast">
      {{ libraryUpdateMessage }}
    </div>

    <!-- Persistent Header overlay - single instance -->
    <Header
      :current-view="headerCurrentView"
      :search-query="searchQuery"
      :mpc-be-connected="mpcBeConnected"
      :nav-row="1"
      :position="headerPosition"
      :style="headerStyle"
      @search="updateSearchQuery"
      @go-back="goBack"
    />

    <!-- Loading state -->
    <main v-if="loading" class="main-content main-content-no-hero">
      <div class="header-spacer"></div>
      <div class="loading">
        <div class="loading-spinner"></div>
        <p class="loading-text">Loading your media collection...</p>
      </div>
    </main>

    <!-- Error state -->
    <main v-else-if="error" class="main-content main-content-no-hero">
      <div class="header-spacer"></div>
      <div class="error">
        <div class="error-icon">⚠️</div>
        <h2 class="error-title">Failed to load media index</h2>
        <p class="error-message">{{ error }}</p>
        <button class="btn btn-primary" @click="reloadPage">Try Again</button>
      </div>
    </main>

    <div v-else class="page-slider">
      <div class="page-slider-track" :class="{ 'is-detail-open': isDetailOpen }">
        <!-- Browse/Search page (left panel) -->
        <main
          ref="browsePanelRef"
          class="main-content page-slider-panel"
          data-nav-scope="browse"
          @scroll.passive="handlePanelScroll('browse')"
        >
          <!-- Movies and Series views - both always rendered for smooth transitions -->
          <div v-if="!searchQuery" class="view-container">
            <Transition name="view-zoom" mode="out-in">
              <div v-if="currentView === 'movies'" key="movies" class="view-content">
                <!-- Hero for movies -->
                <CollageHero
                  v-if="movieCollageItems.length > 0"
                  :key="`movie-hero-${movieCollageItems.length}-${movieFeaturedItem?.id || 'none'}`"
                  :items="movieCollageItems"
                  :featured-item="movieFeaturedItem"
                  @select="showDetail"
                />
                <!-- Spacer for header overlay -->
                <div class="header-spacer"></div>
                <!-- Movie categories -->
                <template v-for="(category, categoryIndex) in moviesByGenre" :key="category.name">
                  <section class="media-section" v-if="category.items.length > 0">
                    <h2 class="section-title">{{ category.name }} ({{ category.items.length }})</h2>
                    <MediaRow
                      :items="category.items"
                      :row-index="categoryIndex + 2"
                      @select="showDetail"
                    />
                  </section>
                </template>
              </div>
              <div v-else-if="currentView === 'series'" key="series" class="view-content">
                <!-- Hero for series -->
                <CollageHero
                  v-if="seriesCollageItems.length > 0"
                  :key="`series-hero-${seriesCollageItems.length}-${seriesFeaturedItem?.id || 'none'}`"
                  :items="seriesCollageItems"
                  :featured-item="seriesFeaturedItem"
                  @select="showDetail"
                />
                <!-- Spacer for header overlay -->
                <div class="header-spacer"></div>
                <!-- Series categories -->
                <template v-for="(category, categoryIndex) in seriesByGenre" :key="category.name">
                  <section class="media-section" v-if="category.items.length > 0">
                    <h2 class="section-title">{{ category.name }} ({{ category.items.length }})</h2>
                    <MediaRow
                      :items="category.items"
                      :row-index="categoryIndex + 2"
                      @select="showDetail"
                    />
                  </section>
                </template>
              </div>
            </Transition>
          </div>

          <!-- Search results -->
          <template v-if="searchQuery">
            <template v-if="searchResults.length > 0">
              <!-- Hero with all results ranked by relevance -->
              <CollageHero
                :key="`search-hero-${searchCollageItems.length}-${searchFeaturedItem?.id || 'none'}`"
                :items="searchCollageItems"
                :featured-item="searchFeaturedItem"
                @select="showDetail"
              />
              <!-- Spacer for header overlay -->
              <div class="header-spacer"></div>
              <!-- Category sections -->
              <template v-for="(category, categoryIndex) in searchCategories" :key="category.name">
                <section class="media-section">
                  <h2 class="section-title">{{ category.name }} ({{ category.items.length }})</h2>
                  <MediaRow
                    :items="category.items"
                    :row-index="categoryIndex + 2"
                    @select="showDetail"
                  />
                </section>
              </template>
            </template>
            <template v-else-if="!isSearching">
              <!-- Empty hero area to maintain layout -->
              <div class="empty-hero"></div>
              <!-- Spacer for header overlay -->
              <div class="header-spacer"></div>
              <div class="no-results">
                <p>No results found for "{{ searchQuery }}"</p>
              </div>
            </template>
          </template>
        </main>

        <!-- Detail page (right panel) -->
        <main
          ref="detailPanelRef"
          class="main-content page-slider-panel page-slider-detail-panel"
          data-nav-scope="detail"
          @scroll.passive="handlePanelScroll('detail')"
        >
          <MediaDetail
            v-if="detailItemForRender"
            v-show="isDetailOpen"
            :item="detailItemForRender"
            :all-movies="mediaIndex?.movies ?? []"
            :focus-episode="focusEpisode"
            :has-resume-position="hasResumePosition"
            :get-root-name="getRootName"
            @close="closeDetail"
            @play="handlePlay"
            @open-folder="handleOpenFolder"
            @search-actor="handleActorSearch"
            @select-movie="handleSelectMovieFromDetail"
          />
        </main>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from "vue"
import { useRouter, useRoute } from "vue-router"
import type {
  Movie,
  MovieUi,
  Series,
  SeriesUi,
  MediaItem,
  EpisodeWithSeries,
  TaskInfo,
} from "./types"
import {
  playMedia,
  openFolder,
  isMpcBeReachable,
  fetchResumePositions,
  normalizeMediaPath,
  getPlayerStatus,
  fetchRoots,
} from "./api"
import { useSettings } from "./composables/useSettings"
import { useKeyboardNavigation, setActiveNavigationScope } from "./composables/useKeyboardNavigation"
import { useMediaWebSocket } from "./composables/useMediaWebSocket"
import Header from "./components/Header.vue"
import CollageHero from "./components/CollageHero.vue"
import MediaRow from "./components/MediaRow.vue"
import MediaDetail from "./components/MediaDetail.vue"
import type { SearchResultItem, SearchResponseMessage } from "./search-worker"

// Initialize keyboard navigation
const { getFocusState, restoreFocusState, focusElement } = useKeyboardNavigation()

const router = useRouter()
const route = useRoute()

function normalizeSearchQuery(value: unknown): string {
  if (Array.isArray(value)) {
    return normalizeSearchQuery(value[0])
  }
  return typeof value === "string" ? value.trim() : ""
}

function getRouteSearchQuery() {
  if (route.name !== "search") {
    return ""
  }
  return normalizeSearchQuery(route.params.term)
}

function getSearchPath(term: string) {
  return router.resolve({ name: "search", params: { term } }).path
}

function getBrowsePath() {
  if (route.meta.view === "series") return "/series"
  if (route.meta.view === "movies") return "/movies"
  return searchReturnPath.value ?? "/movies"
}

function getBrowseViewFromPath(path: string): "movies" | "series" {
  return path === "/series" ? "series" : "movies"
}

function normalizeHistoryPath(value: string): string {
  const hashIndex = value.indexOf("#")
  const fromHash = hashIndex >= 0 ? value.slice(hashIndex + 1) : value
  const pathWithQuery = fromHash.startsWith("/") ? fromHash : `/${fromHash}`
  const queryIndex = pathWithQuery.indexOf("?")
  return queryIndex >= 0 ? pathWithQuery.slice(0, queryIndex) : pathWithQuery
}

// WebSocket-driven media index
const {
  mediaIndex,
  loading,
  error,
  connected: wsConnected,
  tasks,
  setActiveRoots,
} = useMediaWebSocket()

type RootTaskInfo = TaskInfo & { root_id: string }

interface ProgressRootState {
  rootId: string
  rootLabel: string
  scanTarget: string | null
  phaseLabel: string
  phaseDetail: string | null
  progressPercent: number
  progressLabel: string | null
  isDeterminate: boolean
  toneClass: string
}

const activeTasks = computed<RootTaskInfo[]>(() => Array.from(tasks.value.values()))

// Poll for active roots and connect WS to them
const rootStatuses = ref<
  Map<string, { name: string; path: string; status: string; snapshotLoaded: boolean }>
>(new Map())

function getRootName(rootId: string | null | undefined): string | null {
  if (!rootId) return null
  return rootStatuses.value.get(rootId)?.name || null
}

function normalizePosixPath(value: string): string {
  return value.replace(/\\/g, "/")
}

function extractScanPath(detail: string): string | null {
  if (!detail.startsWith("Scanning:")) return null
  let value = detail.replace(/^Scanning:\s*/i, "").trim()
  value = value.replace(/\s*\(\d+\s+found\)\s*$/i, "").trim()
  return value || null
}

function buildScanTarget(rootId: string, rootPath: string | null, detail: string): string | null {
  const rawPath = extractScanPath(detail)
  if (!rawPath) return null

  const posixRaw = normalizePosixPath(rawPath)
  const posixRoot = rootPath ? normalizePosixPath(rootPath) : null

  let relative = posixRaw
  if (posixRoot) {
    const lowRaw = posixRaw.toLowerCase()
    const lowRoot = posixRoot.toLowerCase()
    if (lowRaw === lowRoot) {
      relative = ""
    } else if (lowRaw.startsWith(`${lowRoot}/`)) {
      relative = posixRaw.slice(posixRoot.length).replace(/^\/+/, "")
    }
  }

  if (!relative) return rootId
  if (relative.toLowerCase().startsWith(`${rootId.toLowerCase()}/`)) return relative
  return `${rootId}/${relative}`
}

function describeRootProgress(
  rootId: string,
  rootPath: string | null,
  tasksForRoot: RootTaskInfo[],
  isInitialScanMode: boolean,
): ProgressRootState | null {
  const running = tasksForRoot.filter((task) => task.status === "running")
  const latestError = [...tasksForRoot].reverse().find((task) => task.status === "error") || null

  if (running.length === 0 && !latestError) return null

  const scanTask = running.find((task) => task.id.startsWith("scan-")) || null
  const showreelCount = running.filter((task) => task.id.startsWith("showreel-")).length
  const otherRunningCount = running.length - (scanTask ? 1 : 0) - showreelCount

  let phaseLabel = "Processing media"
  let phaseDetail: string | null = null
  let scanTarget: string | null = null
  let isDeterminate = false
  let progressPercent = 0
  let progressLabel: string | null = null
  let toneClass = ""

  if (scanTask) {
    const detail = (scanTask.detail || "").trim()
    scanTarget = buildScanTarget(rootId, rootPath, detail)
    if (detail.startsWith("Scanning:")) {
      phaseLabel = isInitialScanMode ? "Scanning folders" : "Checking for updates"
      phaseDetail = isInitialScanMode ? "Looking for new files" : "Running background scan"
    } else if (/^Processing\s+\d+\s+(items|movies|series)/i.test(detail)) {
      phaseLabel = "Preparing titles"
      phaseDetail = "Matching files and grouping releases"
    } else {
      phaseLabel = "Fetching metadata"
      phaseDetail = detail ? `Current title: ${detail}` : "Updating titles and artwork"
    }
    if (scanTask.progress > 0 && scanTask.progress <= 1) {
      isDeterminate = true
      progressPercent = Math.max(1, Math.round(scanTask.progress * 100))
      progressLabel = `${progressPercent}%`
    }
  } else if (showreelCount > 0) {
    phaseLabel = "Generating previews"
    phaseDetail = showreelCount === 1 ? "Building 1 preview reel" : `Building ${showreelCount} preview reels`
  } else if (otherRunningCount > 0) {
    phaseLabel = "Finalizing updates"
    phaseDetail = "Applying library changes"
  } else if (latestError) {
    phaseLabel = "Needs attention"
    phaseDetail = latestError.detail || "A background task failed"
    toneClass = "activity-root-error"
  }

  if (showreelCount > 0 && scanTask) {
    phaseDetail = phaseDetail
      ? `${phaseDetail}. Preview generation is running in parallel.`
      : "Preview generation is running in parallel"
  }

  return {
    rootId,
    rootLabel: rootId,
    scanTarget,
    phaseLabel,
    phaseDetail,
    progressPercent,
    progressLabel,
    isDeterminate,
    toneClass,
  }
}

const progressRoots = computed<ProgressRootState[]>(() => {
  const byRoot = new Map<string, RootTaskInfo[]>()
  for (const task of activeTasks.value) {
    const list = byRoot.get(task.root_id) || []
    list.push(task)
    byRoot.set(task.root_id, list)
  }

  const rows: ProgressRootState[] = []
  const initial = isInitialScanMode.value
  for (const [rootId, rootTasks] of byRoot) {
    const rootPath = rootStatuses.value.get(rootId)?.path || null
    const row = describeRootProgress(rootId, rootPath, rootTasks, initial)
    if (row) rows.push(row)
  }
  return rows.sort((a, b) => a.rootLabel.localeCompare(b.rootLabel))
})

const isSettingsView = computed(() => route.path === "/settings")
const hasLibraryItems = computed(() => {
  if (!mediaIndex.value) return false
  return mediaIndex.value.movies.length > 0 || mediaIndex.value.series.length > 0
})
const hasAnySnapshotLoaded = computed(() =>
  Array.from(rootStatuses.value.values()).some((root) => root.snapshotLoaded),
)
const isInitialScanMode = computed(() => !hasLibraryItems.value && !hasAnySnapshotLoaded.value)

const showProgressPanel = computed(() => {
  if (isInitialScanMode.value) {
    return !wsConnected.value || progressRoots.value.length > 0
  }
  if (!isSettingsView.value) return false
  return !wsConnected.value || progressRoots.value.length > 0
})

const libraryUpdateMessage = ref<string | null>(null)
let libraryUpdateToastTimer: number | null = null
let seenTaskStates = new Map<string, string>()

function showLibraryUpdateToast(message: string) {
  libraryUpdateMessage.value = message
  if (libraryUpdateToastTimer !== null) {
    window.clearTimeout(libraryUpdateToastTimer)
  }
  libraryUpdateToastTimer = window.setTimeout(() => {
    libraryUpdateMessage.value = null
    libraryUpdateToastTimer = null
  }, 5500)
}

watch(
  activeTasks,
  (tasksNow) => {
    const nextSeen = new Map<string, string>()
    for (const task of tasksNow) {
      const key = `${task.root_id}:${task.id}`
      nextSeen.set(key, task.status)
      const previousStatus = seenTaskStates.get(key)
      if (task.id.startsWith("scan-") && task.status === "completed" && previousStatus !== "completed") {
        const detail = (task.detail || "").trim()
        const doneMatch = detail.match(/^Done\s+[\u2014-]\s+(\d+)\s+movies,\s+(\d+)\s+series$/i)
        if (doneMatch) {
          const movies = Number(doneMatch[1] || "0")
          const series = Number(doneMatch[2] || "0")
          if (movies > 0 || series > 0) {
            const rootName = getRootName(task.root_id) || task.root_id
            showLibraryUpdateToast(`Library updated in ${rootName}: ${movies} movies, ${series} series`)
          }
        }
      }
    }
    seenTaskStates = nextSeen
  },
  { deep: false },
)

async function refreshRoots() {
  try {
    const roots = await fetchRoots()
    const newMap = new Map<
      string,
      { name: string; path: string; status: string; snapshotLoaded: boolean }
    >()
    const activeIds: string[] = []
    for (const r of roots) {
      newMap.set(r.root_id, {
        name: r.root_id,
        path: r.path,
        status: r.status,
        snapshotLoaded: Boolean(r.snapshot_loaded),
      })
      if (r.status === "ready" || r.status === "scanning") {
        activeIds.push(r.root_id)
      }
    }
    rootStatuses.value = newMap
    setActiveRoots(activeIds)
  } catch (e) {
    console.error("Failed to fetch roots:", e)
  }
}

let rootsPollTimer: number | null = null
function startRootsPolling() {
  if (rootsPollTimer !== null) return
  void refreshRoots()
  rootsPollTimer = window.setInterval(refreshRoots, 5000)
}
function stopRootsPolling() {
  if (rootsPollTimer !== null) {
    window.clearInterval(rootsPollTimer)
    rootsPollTimer = null
  }
}

onMounted(() => {
  startRootsPolling()
})

onUnmounted(() => {
  stopRootsPolling()
  if (libraryUpdateToastTimer !== null) {
    window.clearTimeout(libraryUpdateToastTimer)
    libraryUpdateToastTimer = null
  }
})

const settings = useSettings()

const searchResults = ref<MediaItem[]>([])
const isSearching = ref(false)
const mpcBeConnected = ref(false)
const resumePositions = ref<Record<string, number>>({})
const searchQuery = ref(getRouteSearchQuery())
const searchReturnPath = ref<string | null>(null)
const browsePanelRef = ref<HTMLElement | null>(null)
const detailPanelRef = ref<HTMLElement | null>(null)
const browsePanelScrollTop = ref(0)
const detailPanelScrollTop = ref(0)
const MPC_BE_OPENING_GRACE_MS = 4000
const mpcBeOpeningUntil = ref(0)
let mpcBePollTimer: number | null = null

function handlePanelScroll(scope: "browse" | "detail") {
  if (scope === "browse") {
    browsePanelScrollTop.value = browsePanelRef.value?.scrollTop || 0
    return
  }
  detailPanelScrollTop.value = detailPanelRef.value?.scrollTop || 0
}

function isMpcFamilySelected(): boolean {
  // Empty port means Web UI is disabled
  if (settings.playerMpcPort === null) return false
  // If default is selected, we opportunistically try MPC-BE (backward compat)
  // If a specific player is selected, only poll for MPC family
  if (!settings.playerId || settings.playerId === "default") return true
  return settings.playerId === "mpc-be" || settings.playerId === "mpc-hc"
}

function isMpcBeGamepadCaptured() {
  return mpcBeConnected.value || Date.now() < mpcBeOpeningUntil.value
}

function stopMpcBePolling() {
  if (mpcBePollTimer !== null) {
    window.clearInterval(mpcBePollTimer)
    mpcBePollTimer = null
  }
}

async function refreshResumePositions() {
  resumePositions.value = await fetchResumePositions()
}

async function refreshPlayerStatus() {
  if (!isMpcFamilySelected()) {
    mpcBeConnected.value = false
    return
  }
  try {
    const status = await getPlayerStatus(settings.playerMpcPort)
    mpcBeConnected.value = status.remote
  } catch {
    mpcBeConnected.value = false
  }
}

function hasResumePosition(filePath: string | null) {
  if (!filePath) return false
  const normalizedPath = normalizeMediaPath(filePath)
  return Number(resumePositions.value[normalizedPath] || 0) > 0
}

function startMpcBePolling() {
  if (mpcBePollTimer !== null) return
  mpcBePollTimer = window.setInterval(async () => {
    const reachable = await isMpcBeReachable(settings.playerMpcPort)
    const wasConnected = mpcBeConnected.value
    mpcBeConnected.value = reachable
    if (!reachable) {
      stopMpcBePolling()
      if (wasConnected) {
        void refreshResumePositions()
      }
    }
  }, 3000)
}

async function tryConnectMpcBe(attempts = 8, delayMs = 400): Promise<boolean> {
  for (let i = 0; i < attempts; i++) {
    const reachable = await isMpcBeReachable(settings.playerMpcPort)
    if (reachable) return true
    if (i < attempts - 1) {
      await new Promise((resolve) => window.setTimeout(resolve, delayMs))
    }
  }
  return false
}

type GamepadAction = "up" | "down" | "left" | "right" | "select" | "back"

function onGamepadAction(event: Event) {
  const customEvent = event as CustomEvent<{ action?: GamepadAction }>
  const action = customEvent.detail?.action
  if (!action) return

  if (isMpcBeGamepadCaptured()) {
    event.preventDefault()
  }
}

// Focus episode info for navigating to series detail from search
const focusEpisode = ref<{ seasonNumber: number; episodeNumber: number } | null>(null)

const searchCategories = ref<{ name: string; items: MediaItem[] }[]>([])

// Focus state per page for Escape navigation
const focusStateMap = new Map<string, { row: number; col: number }>()
// Track the last viewed item ID to restore focus to the right card
const lastViewedItemId = ref<string | null>(null)

// Save current focus state for a page
function saveFocusForPage(page: string) {
  const state = getFocusState()
  if (state) {
    focusStateMap.set(page, state)
  }
}

// Restore focus state for a page, or find the last viewed item
function restoreFocusForPage(page: string) {
  // Don't steal focus from the search input while the user is typing
  const active = document.activeElement
  if (active?.closest(".header-search")) return

  // First try to find the last viewed item and focus it
  if (lastViewedItemId.value) {
    // Use nextTick + timeout to ensure DOM is updated after navigation
    setTimeout(() => {
      const itemId = lastViewedItemId.value
      // Find the element with matching item id
      const element = document.querySelector(`[data-item-id="${itemId}"]`) as HTMLElement | null
      if (element) {
        focusElement(element)
        lastViewedItemId.value = null
        return
      }
      // Fallback to saved focus state
      const state = focusStateMap.get(page)
      restoreFocusState(state || null)
      lastViewedItemId.value = null
    }, 100)
  } else {
    const state = focusStateMap.get(page)
    restoreFocusState(state || null)
  }
}

function restoreBrowseFocus(path: string) {
  window.setTimeout(() => {
    restoreFocusForPage(getBrowseViewFromPath(path))
  }, 100)
}

function getSearchExitTargetFromFocusedCard(): { path: "/movies" | "/series"; itemId: string } | null {
  const active = document.activeElement as HTMLElement | null
  const focusedCard = active?.closest("[data-item-id]") as HTMLElement | null
  if (!focusedCard) return null

  const itemId = focusedCard.getAttribute("data-item-id")
  const itemType = focusedCard.getAttribute("data-item-type")
  if (!itemId || !itemType) return null

  if (itemType === "movies") {
    return { path: "/movies", itemId }
  }
  if (itemType === "series") {
    return { path: "/series", itemId }
  }

  return null
}

function getDetailSearchPath(): string | null {
  if (!route.params.id) return null

  const stateSearchPathRaw = window.history.state?.searchPath
  if (typeof stateSearchPathRaw === "string") {
    const stateSearchPath = normalizeHistoryPath(stateSearchPathRaw)
    if (stateSearchPath.startsWith("/search/")) {
      return stateSearchPath
    }
  }

  const backPath =
    typeof window.history.state?.back === "string"
      ? normalizeHistoryPath(window.history.state.back)
      : ""
  if (backPath.startsWith("/search/")) {
    return backPath
  }

  return null
}

function clearSearch(options: { preferBack?: boolean; targetPath?: string } = {}) {
  const currentQuery = getRouteSearchQuery()
  const targetPath = options.targetPath ?? getBrowsePath()

  searchQuery.value = ""

  // If there's no active search query in the URL, just clear state and don't navigate.
  // This prevents unwanted navigation when the search is cleared reactively (e.g.
  // route changes to a detail page, which triggers the route watcher to clear
  // searchQuery, which flows through Header and back to updateSearchQuery).
  if (!currentQuery) {
    searchReturnPath.value = null
    return
  }

  const backPath =
    typeof window.history.state?.back === "string"
      ? normalizeHistoryPath(window.history.state.back)
      : ""
  const canRestoreWithBack =
    options.preferBack !== false &&
    searchReturnPath.value === targetPath &&
    backPath === targetPath

  searchReturnPath.value = null

  if (canRestoreWithBack) {
    router.back()
    restoreBrowseFocus(targetPath)
  } else {
    void router.replace({ path: targetPath }).finally(() => {
      restoreBrowseFocus(targetPath)
    })
  }
}

function updateSearchQuery(nextValue: string) {
  const nextQuery = normalizeSearchQuery(nextValue)
  const currentQuery = getRouteSearchQuery()
  const browsePath = getBrowsePath()

  if (!nextQuery) {
    clearSearch({ preferBack: true })
    return
  }

  searchQuery.value = nextQuery

  if (!currentQuery) {
    if (!route.params.id) {
      saveFocusForPage(currentView.value)
    }
    searchReturnPath.value = browsePath
    void router.push(getSearchPath(nextQuery))
    return
  }

  void router.replace(getSearchPath(nextQuery))
}

// Handle Escape key for navigation hierarchy
function handleEscapeKey(event: KeyboardEvent) {
  if (event.key !== "Escape") return

  // Ignore if typing in an input
  const target = event.target as HTMLElement
  if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable) {
    return
  }

  const path = route.path
  const detailSearchPath = getDetailSearchPath()

  if (route.params.id && detailSearchPath) {
    event.preventDefault()
    void router.push(detailSearchPath).finally(() => {
      restoreFocusForPage("movies")
    })
    return
  }

  // From movie detail -> movies list
  if (path.startsWith("/movies/") && route.params.id) {
    event.preventDefault()
    router.push("/movies")
    restoreFocusForPage("movies")
    return
  }

  // From series detail -> series list
  if (path.startsWith("/series/") && route.params.id) {
    event.preventDefault()
    router.push("/series")
    restoreFocusForPage("series")
    return
  }

  if (getRouteSearchQuery()) {
    event.preventDefault()
    const target = getSearchExitTargetFromFocusedCard()
    if (target) {
      lastViewedItemId.value = target.itemId
      clearSearch({ preferBack: false, targetPath: target.path })
      return
    }
    clearSearch({ preferBack: true })
    return
  }

  // From series list -> movies list
  if (path === "/series") {
    event.preventDefault()
    saveFocusForPage("series")
    router.push("/movies")
    restoreFocusForPage("movies")
    return
  }

  // From movies list -> do nothing (stop here)
}

onMounted(() => {
  void refreshPlayerStatus()
  void refreshResumePositions()
  browsePanelScrollTop.value = browsePanelRef.value?.scrollTop || 0
  detailPanelScrollTop.value = detailPanelRef.value?.scrollTop || 0
  setActiveNavigationScope(isDetailOpen.value ? "detail" : "browse")
  document.addEventListener("keydown", handleEscapeKey)
  window.addEventListener("mediahive:gamepad-action", onGamepadAction as EventListener)
})

onUnmounted(() => {
  document.removeEventListener("keydown", handleEscapeKey)
  window.removeEventListener("mediahive:gamepad-action", onGamepadAction as EventListener)
  stopMpcBePolling()
})

// Handle back navigation (Escape key or Back button)
function goBack() {
  const path = route.path
  const detailSearchPath = getDetailSearchPath()

  if (route.params.id && detailSearchPath) {
    void router.push(detailSearchPath).finally(() => {
      restoreFocusForPage("movies")
    })
    return
  }

  // From movie detail -> movies list with focus restoration
  if (path.startsWith("/movies/") && route.params.id) {
    router.push("/movies")
    restoreFocusForPage("movies")
    return
  }

  // From series detail -> series list with focus restoration
  if (path.startsWith("/series/") && route.params.id) {
    router.push("/series")
    restoreFocusForPage("series")
    return
  }

  // Default: just go back
  router.back()
}

// Derive currentView from route
const currentView = computed(() => {
  return (route.meta.view as "movies" | "series") || "movies"
})

const headerCurrentView = computed<"movies" | "series" | "search">(() => {
  return selectedItem.value && getDetailSearchPath() ? "search" : currentView.value
})

// Header position based on current page
const headerPosition = computed(() => {
  if (selectedItem.value) {
    // Detail pages - position after the header section
    return selectedItem.value.type === "series" ? "after-series-hero" : "after-movie-header"
  }
  if (loading.value || error.value) {
    return "top"
  }
  // Search page always uses after-hero position for consistent layout
  if (searchQuery.value) {
    return "after-hero"
  }
  return "after-hero"
})

// Derive selectedItem from route params
const selectedItem = computed(() => {
  const id = route.params.id as string | undefined
  if (!id || !mediaIndex.value) return null

  // Check route path to determine type, not currentView (which could be 'search')
  const path = route.path
  if (path.startsWith("/movies/")) {
    const movie = mediaIndex.value.movies.find((m) => m.id === id)
    return movie ? movieToMediaItem(movie) : null
  } else if (path.startsWith("/series/")) {
    const series = mediaIndex.value.series.find((s) => s.id === id)
    return series ? seriesToMediaItem(series) : null
  }
  return null
})

const isDetailOpen = computed(() => selectedItem.value !== null)
const lastDetailItem = ref<MediaItem | null>(null)
const detailItemForRender = computed(() => selectedItem.value ?? lastDetailItem.value)

const activePanelScrollTop = computed(() => {
  if (loading.value || error.value) return 0
  return isDetailOpen.value ? detailPanelScrollTop.value : browsePanelScrollTop.value
})

const headerStyle = computed(() => {
  if (route.path === "/settings") {
    return {}
  }
  return {
    transform: `translateY(${-activePanelScrollTop.value}px)`,
  }
})

watch(selectedItem, (item) => {
  if (item) {
    lastDetailItem.value = item
  }
})

// Show detail by navigating to URL
function showDetail(item: MediaItem) {
  // Save the item ID to restore focus when returning
  lastViewedItemId.value = item.id

  // Save focus state before navigating to detail
  const currentPage = route.path === "/series" ? "series" : "movies"
  saveFocusForPage(currentPage)

  // Check if there are matched episodes to focus on
  if (item.type === "series" && item.searchMatchInfo?.matchedEpisodes?.length) {
    const firstMatch = item.searchMatchInfo.matchedEpisodes[0]
    focusEpisode.value = {
      seasonNumber: firstMatch.seasonNumber,
      episodeNumber: firstMatch.episodeNumber,
    }
  } else {
    focusEpisode.value = null
  }

  if (item.type === "episode") {
    // For episodes, play directly if possible, otherwise show the series
    const epData = item.data as EpisodeWithSeries
    const playableFile = Object.values(epData.episode.files || {})[0]?.playable_file
    if (playableFile) {
      handlePlay(playableFile)
    } else {
      const searchPath = searchQuery.value ? getSearchPath(searchQuery.value) : null
      router.push({ path: `/series/${epData.series.id}`, state: searchPath ? { searchPath } : undefined })
    }
  } else {
    const searchPath = searchQuery.value ? getSearchPath(searchQuery.value) : null
    router.push({ path: `/${item.type}/${item.id}`, state: searchPath ? { searchPath } : undefined })
  }
}

// Close detail by navigating back to list
function closeDetail() {
  const detailSearchPath = getDetailSearchPath()
  const targetPath = getBrowsePath()
  if (detailSearchPath) {
    router.push(detailSearchPath)
  } else {
    router.push(targetPath)
  }
}

function handleActorSearch(actorName: string) {
  updateSearchQuery(actorName)
}

function handleSelectMovieFromDetail(movieId: string) {
  const searchPath = searchQuery.value ? getSearchPath(searchQuery.value) : null
  router.push({ path: `/movies/${movieId}`, state: searchPath ? { searchPath } : undefined })
}

function focusDetailEntryTarget(item: MediaItem): boolean {
  const detailPanel = document.querySelector('[data-nav-scope="detail"]') as HTMLElement | null
  if (!detailPanel) return false

  let target: HTMLElement | null = null

  if (item.type === "movies") {
    // Best release is rendered first and mapped to row 2 / col 0.
    target = detailPanel.querySelector(
      '[data-nav-release-item="true"][data-nav-row="2"][data-nav-col="0"][data-nav-focusable="true"]',
    ) as HTMLElement | null
  } else if (item.type === "series") {
    // Initial episode tile (first season, first episode) maps to row 2 / col 0.
    target = detailPanel.querySelector(
      '.episode-tile[data-nav-row="2"][data-nav-col="0"][data-nav-focusable="true"]',
    ) as HTMLElement | null
  }

  if (target) {
    focusElement(target)
    return true
  }

  const navButtons = Array.from(document.querySelectorAll<HTMLElement>(".header-nav-item.active"))
  const detailsNav = navButtons.find((button) => button.textContent?.trim() === "Details")
  if (detailsNav?.hasAttribute("data-nav-focusable")) {
    focusElement(detailsNav)
    return true
  }

  const firstFocusable = detailPanel.querySelector('[data-nav-focusable="true"]') as HTMLElement | null
  if (firstFocusable) {
    focusElement(firstFocusable)
    return true
  }

  return false
}

// Convert raw data to MediaItem format
function movieToMediaItem(movie: MovieUi): MediaItem {
  // Get resolution from first torrent if available
  const files = Object.values(movie.files || {})
  const resolution = files.length > 0 ? files[0].resolution : null

  return {
    id: movie.id,
    title: movie.title || "Unknown",
    year: movie.year,
    cover_path: movie.cover_path,
    showreel_images: movie.showreel_images,
    showreel_source_sets: movie.showreel_source_sets,
    type: "movies",
    resolution: resolution,
    data: movie,
    root_id: movie.root_id,
  }
}

function seriesToMediaItem(series: SeriesUi): MediaItem {
  // For series, collect reel images from all episodes
  const reelImages: string[] = []
  const reelSourceSets: string[][] = []
  for (const season of series.seasons || []) {
    for (const episode of season.episodes || []) {
      if (episode.reel_sources && episode.reel_sources.length > 0) {
        reelImages.push(episode.reel_sources[0])
        reelSourceSets.push(episode.reel_sources)
      } else if (episode.reel_image) {
        reelImages.push(episode.reel_image)
        reelSourceSets.push([episode.reel_image])
      }
    }
  }

  return {
    id: series.id,
    title: series.title || "Unknown",
    year: null,
    cover_path: series.cover_path,
    showreel_images: reelImages.length > 0 ? reelImages : null,
    showreel_source_sets: reelSourceSets.length > 0 ? reelSourceSets : null,
    type: "series",
    data: series,
    root_id: series.root_id,
  }
}

// Genre categories for display order (array position determines UI order)
// priority: lower number = higher matching priority (movies assigned to highest priority match)
// exclude: if item has any of these genres, it won't match this category (negative match)
const GENRE_CATEGORIES = [
  { name: "Action", keywords: ["Action", "Adventure"], priority: 40, exclude: [] },
  { name: "Comedy", keywords: ["Comedy"], priority: 30, exclude: ["Drama"] },
  { name: "Romance", keywords: ["Romance"], priority: 20, exclude: [] },
  { name: "Drama", keywords: ["Drama"], priority: 50, exclude: [] },
  { name: "Crime", keywords: ["Crime"], priority: 45, exclude: [] },
  { name: "Thriller", keywords: ["Thriller", "Mystery"], priority: 20, exclude: [] },
  { name: "Horror", keywords: ["Horror"], priority: 10, exclude: [] },
  { name: "Science Fiction", keywords: ["Science Fiction", "Sci-Fi"], priority: 15, exclude: [] },
  { name: "Animation", keywords: ["Animation"], priority: 8, exclude: [] },
  { name: "Family", keywords: ["Family"], priority: 10, exclude: [] },
  { name: "Sports", keywords: ["Sports"], priority: 30, exclude: [] },
  { name: "Documentary", keywords: ["Documentary"], priority: 20, exclude: [] },
  { name: "War/History", keywords: ["War", "History"], priority: 30, exclude: ["Fantasy"] },
  { name: "Music", keywords: ["Music", "Musical"], priority: 15, exclude: [] },
] as const

// Categories sorted by priority for matching (lowest priority number first)
const GENRE_CATEGORIES_BY_PRIORITY = [...GENRE_CATEGORIES].sort((a, b) => a.priority - b.priority)

// Helper to sort by rating (highest first)
function sortByRating(items: MediaItem[]): MediaItem[] {
  return [...items].sort((a, b) => {
    let ratingA = 0
    let ratingB = 0

    if (a.type === "episode") {
      const epData = a.data as EpisodeWithSeries
      ratingA = epData.episode.rating ?? epData.series.info?.rating ?? 0
    } else {
      ratingA = (a.data as Movie | Series).info?.rating ?? 0
    }

    if (b.type === "episode") {
      const epData = b.data as EpisodeWithSeries
      ratingB = epData.episode.rating ?? epData.series.info?.rating ?? 0
    } else {
      ratingB = (b.data as Movie | Series).info?.rating ?? 0
    }

    return ratingB - ratingA
  })
}

// Categorize movies by genre
const moviesByGenre = computed(() => {
  if (!mediaIndex.value) return []

  const allMovies = mediaIndex.value.movies.map(movieToMediaItem)
  const assignedIds = new Set<string>()
  const categories: { name: string; items: MediaItem[] }[] = []

  // Apply search filter
  const searchFilter = (m: MediaItem) => {
    if (!searchQuery.value) return true
    return (m.title || "").toLowerCase().includes(searchQuery.value.toLowerCase())
  }

  // Assign movies to categories by matching priority (lowest priority number first)
  const categoryMap = new Map<string, MediaItem[]>()
  for (const category of GENRE_CATEGORIES_BY_PRIORITY) {
    for (const movie of allMovies) {
      if (assignedIds.has(movie.id)) continue
      if (!searchFilter(movie)) continue

      const movieData = movie.data as Movie
      const genres = movieData.info?.genres || []

      // Check if movie matches this category (has keyword and no excluded genres)
      const hasKeyword = category.keywords.some((keyword) =>
        genres.some((g) => g.toLowerCase().includes(keyword.toLowerCase())),
      )
      const hasExcluded =
        category.exclude.length > 0 &&
        category.exclude.some((excl) =>
          genres.some((g) => g.toLowerCase().includes(excl.toLowerCase())),
        )

      if (hasKeyword && !hasExcluded) {
        if (!categoryMap.has(category.name)) {
          categoryMap.set(category.name, [])
        }
        categoryMap.get(category.name)!.push(movie)
        assignedIds.add(movie.id)
      }
    }
  }

  // Build categories in display order (GENRE_CATEGORIES array order)
  for (const category of GENRE_CATEGORIES) {
    const categoryMovies = categoryMap.get(category.name)
    if (categoryMovies && categoryMovies.length > 0) {
      categories.push({
        name: category.name,
        items: sortByRating(categoryMovies),
      })
    }
  }

  // Collect remaining movies into Miscellaneous
  const miscMovies: MediaItem[] = []
  for (const movie of allMovies) {
    if (assignedIds.has(movie.id)) continue
    if (!searchFilter(movie)) continue
    miscMovies.push(movie)
  }

  if (miscMovies.length > 0) {
    categories.push({
      name: "Miscellaneous",
      items: sortByRating(miscMovies),
    })
  }

  return categories
})

// Categorize series by genre
const seriesByGenre = computed(() => {
  if (!mediaIndex.value) return []

  const allSeries = mediaIndex.value.series.map(seriesToMediaItem)
  const assignedIds = new Set<string>()
  const categories: { name: string; items: MediaItem[] }[] = []

  // Apply search filter
  const searchFilter = (s: MediaItem) => {
    if (!searchQuery.value) return true
    return (s.title || "").toLowerCase().includes(searchQuery.value.toLowerCase())
  }

  // Assign series to categories by matching priority (lowest priority number first)
  const categoryMap = new Map<string, MediaItem[]>()
  for (const category of GENRE_CATEGORIES_BY_PRIORITY) {
    for (const series of allSeries) {
      if (assignedIds.has(series.id)) continue
      if (!searchFilter(series)) continue

      const seriesData = series.data as Series
      const genres = seriesData.info?.genres || []

      // Check if series matches this category (has keyword and no excluded genres)
      const hasKeyword = category.keywords.some((keyword) =>
        genres.some((g) => g.toLowerCase().includes(keyword.toLowerCase())),
      )
      const hasExcluded =
        category.exclude.length > 0 &&
        category.exclude.some((excl) =>
          genres.some((g) => g.toLowerCase().includes(excl.toLowerCase())),
        )

      if (hasKeyword && !hasExcluded) {
        if (!categoryMap.has(category.name)) {
          categoryMap.set(category.name, [])
        }
        categoryMap.get(category.name)!.push(series)
        assignedIds.add(series.id)
      }
    }
  }

  // Build categories in display order (GENRE_CATEGORIES array order)
  for (const category of GENRE_CATEGORIES) {
    const categorySeries = categoryMap.get(category.name)
    if (categorySeries && categorySeries.length > 0) {
      categories.push({
        name: category.name,
        items: sortByRating(categorySeries),
      })
    }
  }

  // Collect remaining series into Miscellaneous
  const miscSeries: MediaItem[] = []
  for (const series of allSeries) {
    if (assignedIds.has(series.id)) continue
    if (!searchFilter(series)) continue
    miscSeries.push(series)
  }

  if (miscSeries.length > 0) {
    categories.push({
      name: "Miscellaneous",
      items: sortByRating(miscSeries),
    })
  }

  return categories
})

// Search worker
let searchWorker: Worker | null = null
let pendingSearchId = 0
let workerHasIndex = false

function getSearchWorker(): Worker {
  if (!searchWorker) {
    searchWorker = new Worker(new URL("./search-worker.ts", import.meta.url), {
      type: "module",
    })
    searchWorker.onmessage = (event: MessageEvent<SearchResponseMessage>) => {
      const { id, results, categories } = event.data
      // Ignore stale results
      if (id !== pendingSearchId) return
      searchResults.value = results.map(rehydrateSearchResult)
      searchCategories.value = categories.map((cat) => ({
        name: cat.name,
        items: cat.items.map(rehydrateSearchResult),
      }))
      isSearching.value = false
    }
  }
  return searchWorker
}

function syncWorkerIndex() {
  if (!mediaIndex.value) return
  const worker = getSearchWorker()
  worker.postMessage({
    type: "index",
    movies: JSON.parse(JSON.stringify(mediaIndex.value.movies)),
    series: JSON.parse(JSON.stringify(mediaIndex.value.series)),
  })
  workerHasIndex = true
}

// Rehydrate a lightweight SearchResultItem back into a full MediaItem
function rehydrateSearchResult(result: SearchResultItem): MediaItem {
  const base: MediaItem = {
    id: result.id,
    title: result.title,
    year: result.year,
    cover_path: result.cover_path,
    showreel_images: result.showreel_images,
    showreel_source_sets: result.showreel_source_sets,
    type: result.type,
    resolution: result.resolution,
    data: {} as Movie | Series, // placeholder; lookup on demand if needed
    root_id: result.root_id,
    searchMatchInfo: result.searchMatchInfo,
  }
  return base
}

// Watch for detail page entry/exit to manage focus
watch(selectedItem, (item, oldItem) => {
  if (item && !oldItem) {
    // Entering detail page - constrain navigation to detail panel immediately,
    // then shift focus into it while the slide transition runs.
    setActiveNavigationScope("detail")
    setTimeout(() => {
      focusDetailEntryTarget(item)
    }, 20)
  } else if (!item && oldItem) {
    setActiveNavigationScope("browse")
    // Leaving detail page (browser back, Escape, etc.) - restore focus to the item card
    const page = currentView.value === "series" ? "series" : "movies"
    restoreFocusForPage(page)
  }
})

// Focus search input by default on initial movies page load
let initialFocusDone = false
watch([mediaIndex, currentView, searchQuery, selectedItem], ([index, view, query, item]) => {
  if (!initialFocusDone && index && view === "movies" && !query && !item) {
    initialFocusDone = true
    // Focus search input on first movies page load
    setTimeout(() => {
      const searchInput = document.querySelector(".search-input") as HTMLInputElement
      if (searchInput) {
        searchInput.focus()
      }
    }, 100)
  }
})

watch(
  () => route.fullPath,
  () => {
    const nextQuery = getRouteSearchQuery()
    if (nextQuery !== searchQuery.value) {
      searchQuery.value = nextQuery
    }
    if (!nextQuery && route.name !== "search") {
      searchReturnPath.value = null
    }
  },
  { immediate: true },
)

function runSearch() {
  const query = searchQuery.value
  if (!query || !mediaIndex.value) {
    searchResults.value = []
    searchCategories.value = []
    isSearching.value = false
    pendingSearchId += 1
    return
  }

  if (!workerHasIndex) {
    syncWorkerIndex()
  }

  isSearching.value = true
  pendingSearchId += 1
  const id = pendingSearchId

  const worker = getSearchWorker()
  worker.postMessage({
    type: "query",
    id,
    query: query.toLowerCase(),
  })
}

watch(searchQuery, runSearch)
watch(mediaIndex, () => {
  workerHasIndex = false
  if (searchQuery.value) {
    syncWorkerIndex()
    runSearch()
  }
})

// Sort by newest timestamp (descending)
function sortByNewest(items: MediaItem[]): MediaItem[] {
  return [...items].sort((a, b) => {
    const aNewest = (a.data as Movie | Series).newest ?? 0
    const bNewest = (b.data as Movie | Series).newest ?? 0
    return bNewest - aNewest
  })
}

// Newest items from all genre categories for showcase
const newestMovies = computed(() => {
  const allItems: MediaItem[] = []
  for (const category of moviesByGenre.value) {
    allItems.push(...category.items)
  }
  return sortByNewest(allItems).slice(0, 20)
})

const newestSeries = computed(() => {
  const allItems: MediaItem[] = []
  for (const category of seriesByGenre.value) {
    allItems.push(...category.items)
  }
  return sortByNewest(allItems).slice(0, 20)
})

// Items for the collage hero - separate for movies and series to enable smooth transitions
const movieCollageItems = computed(() => {
  const items = newestMovies.value
  const withCovers = items.filter((m) => m.cover_path)
  const withoutCovers = items.filter((m) => !m.cover_path)
  return [...withCovers, ...withoutCovers].slice(0, 100)
})

const seriesCollageItems = computed(() => {
  const items = newestSeries.value
  const withCovers = items.filter((m) => m.cover_path)
  const withoutCovers = items.filter((m) => !m.cover_path)
  return [...withCovers, ...withoutCovers].slice(0, 100)
})

// Featured items for hero - separate for movies and series
const movieFeaturedItem = computed(() => {
  const withCovers = newestMovies.value.filter((m) => m.cover_path)
  return withCovers[0] || newestMovies.value[0] || null
})

const seriesFeaturedItem = computed(() => {
  const withCovers = newestSeries.value.filter((s) => s.cover_path)
  return withCovers[0] || newestSeries.value[0] || null
})

// Search results collage items (already ranked by relevance)
const searchCollageItems = computed(() => {
  const items = searchResults.value
  const withCovers = items.filter((m) => m.cover_path)
  const withoutCovers = items.filter((m) => !m.cover_path)
  return [...withCovers, ...withoutCovers].slice(0, 100)
})

// Search featured item (highest relevance with cover)
const searchFeaturedItem = computed(() => {
  const withCovers = searchResults.value.filter((m) => m.cover_path)
  return withCovers[0] || searchResults.value[0] || null
})

function reloadPage() {
  window.location.reload()
}

function findRootIdForPath(filePath: string): string | null {
  if (!mediaIndex.value) return null
  for (const movie of mediaIndex.value.movies) {
    for (const torrent of Object.values(movie.files || {})) {
      if (torrent.playable_file === filePath) {
        return torrent.root_id || movie.root_id
      }
    }
  }
  for (const series of mediaIndex.value.series) {
    for (const season of series.seasons || []) {
      for (const episode of season.episodes || []) {
        for (const torrent of Object.values(episode.files || {})) {
          if (torrent.playable_file === filePath) {
            return torrent.root_id || series.root_id
          }
        }
      }
    }
  }
  return null
}

async function handlePlay(filePath: string) {
  const rootId = findRootIdForPath(filePath)
  if (!rootId) {
    console.error("Cannot play: unknown root for path", filePath)
    return
  }
  if (isMpcFamilySelected()) {
    mpcBeOpeningUntil.value = Date.now() + MPC_BE_OPENING_GRACE_MS
  }
  try {
    await playMedia(rootId, filePath, settings.playerId, settings.playerCustomCmd)
    if (isMpcFamilySelected()) {
      const connected = await tryConnectMpcBe()
      if (connected) {
        mpcBeConnected.value = true
        startMpcBePolling()
      }
    }
  } catch (e) {
    console.error("Failed to play media:", e)
  }
}

async function handleOpenFolder(folderPath: string, explicitRootId?: string | null) {
  const rootId = explicitRootId || findRootIdForPath(folderPath)
  if (!rootId) {
    console.error("Cannot open folder: unknown root for path", folderPath)
    return
  }
  try {
    await openFolder(rootId, folderPath)
  } catch (e) {
    console.error("Failed to open folder:", e)
  }
}
</script>

<style scoped>
.app {
  height: 100vh;
  min-height: 100vh;
  overflow: hidden;
}

/* Unified library activity panel */
.activity-overlay {
  position: fixed;
  bottom: 12px;
  right: 12px;
  z-index: 9999;
  pointer-events: none;
}

.library-update-toast {
  position: fixed;
  bottom: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10000;
  max-width: min(680px, calc(100vw - 24px));
  padding: 9px 14px;
  border-radius: 999px;
  border: 1px solid rgba(52, 211, 153, 0.45);
  background: rgba(6, 95, 70, 0.92);
  color: rgba(255, 255, 255, 0.95);
  font-size: 12px;
  font-weight: 600;
  text-align: center;
  pointer-events: none;
  backdrop-filter: blur(8px);
}

.activity-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: min(440px, calc(100vw - 24px));
  padding: 12px;
  background: rgba(0, 0, 0, 0.86);
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 10px;
  backdrop-filter: blur(10px);
}

.activity-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  color: rgba(255, 255, 255, 0.9);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.activity-connection {
  color: #f59e0b;
  font-weight: 600;
}

.activity-root {
  display: grid;
  gap: 6px;
  padding: 10px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
}

.activity-root-error {
  border-color: rgba(248, 113, 113, 0.45);
}

.activity-root-title {
  font-size: 12px;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.95);
}

.activity-root-target {
  font-size: 12px;
  color: rgba(147, 197, 253, 0.95);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.activity-phase-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
}

.activity-phase {
  color: rgba(255, 255, 255, 0.82);
}

.activity-progress-label {
  color: #60a5fa;
  font-weight: 600;
}

.activity-bar {
  position: relative;
  height: 6px;
  border-radius: 99px;
  background: rgba(255, 255, 255, 0.14);
  overflow: hidden;
}

.activity-bar-fill {
  position: absolute;
  inset: 0 auto 0 0;
  width: 100%;
  background: linear-gradient(90deg, #3b82f6, #22d3ee);
  border-radius: inherit;
}

.activity-bar-indeterminate .activity-bar-fill {
  width: 45%;
  animation: activity-indeterminate 1.3s ease-in-out infinite;
}

.activity-detail {
  color: rgba(255, 255, 255, 0.58);
  font-size: 11px;
  line-height: 1.35;
}

@keyframes activity-indeterminate {
  0% {
    transform: translateX(-120%);
  }
  100% {
    transform: translateX(250%);
  }
}

.empty-hero {
  height: 70vh;
  min-height: 450px;
  max-height: 600px;
  background: var(--bg-primary);
}

.no-results {
  display: flex;
  justify-content: center;
  align-items: center;
  margin-top: 5rem;
  padding: 60px 20px;
  color: var(--text-secondary);
  font-size: 2rem;
}

.page-slider {
  width: 100vw;
  height: 100vh;
  overflow: hidden;
}

.page-slider-track {
  display: flex;
  width: 200vw;
  height: 100vh;
  transition: transform 420ms cubic-bezier(0.22, 0.61, 0.36, 1);
  will-change: transform;
}

.page-slider-track.is-detail-open {
  transform: translate3d(-100vw, 0, 0);
}

.page-slider-panel {
  flex: 0 0 100vw;
  width: 100vw;
  min-width: 100vw;
  height: 100vh;
  overflow-y: auto;
  overflow-x: hidden;
  overscroll-behavior: contain;
  -webkit-overflow-scrolling: touch;
}

.page-slider-detail-panel {
  background: var(--bg-primary);
}

@media (prefers-reduced-motion: reduce) {
  .page-slider-track {
    transition: none;
  }
}
</style>
