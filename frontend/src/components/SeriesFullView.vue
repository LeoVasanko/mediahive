<template>
  <div ref="seriesRootRef" class="series-fullscreen">
    <!-- Hero section with backdrop or season collage -->
    <section class="series-hero">
      <div class="hero-bg">
        <!-- Use backdrop if available, otherwise create collage from season posters -->
        <img
          v-if="backdropUrl"
          :src="backdropUrl"
          class="hero-img"
          :alt="series.title || 'Unknown'"
        />
        <div v-else class="hero-collage">
          <div
            v-for="(season, i) in seasonsWithPosters.slice(0, 5)"
            :key="i"
            class="collage-slice"
            :style="getCollageSliceStyle(season, i)"
          ></div>
        </div>
      </div>
      <div class="hero-gradient"></div>
      <div class="hero-content">
        <h1 class="series-title">{{ series.title }}</h1>
        <div class="series-meta">
          <span v-if="series.info?.rating" class="meta-rating" :class="ratingClass"
            >★ {{ series.info.rating.toFixed(1) }}</span
          >
          <span v-if="series.info?.number_of_seasons" class="meta-item"
            >{{ series.info.number_of_seasons }} Seasons</span
          >
          <span v-if="series.info?.status" class="meta-badge">{{ series.info.status }}</span>
          <span v-if="series.info?.genres?.length" class="meta-genres">{{
            series.info.genres.slice(0, 3).join(" • ")
          }}</span>
        </div>
        <p v-if="series.info?.overview" class="series-overview">{{ series.info.overview }}</p>
      </div>
    </section>

    <!-- Spacer for header overlay -->
    <div class="header-spacer"></div>

    <!-- Season selector jukebox: one season rendered at a time -->
    <section class="seasons-container">
      <div
        ref="seasonStageRef"
        class="season-stage"
        :style="{
          '--poster-w': `${stageMetrics.posterWidth}px`,
          '--info-shift': `${stageMetrics.infoShift}px`,
        }"
      >
        <button
          v-for="(season, sIndex) in series.seasons"
          :key="sIndex"
          type="button"
          class="season-poster-card"
          :class="{ 'season-poster-card--selected': sIndex === selectedSeasonIndex }"
          :style="getSeasonCardStyle(sIndex)"
          v-bind="navAttrs(2, sIndex)"
          @click="selectSeason(sIndex)"
          @focusin="selectSeason(sIndex)"
          @keydown.enter.prevent="focusFirstEpisode"
        >
          <img
            v-if="getSeasonPoster(season)"
            :src="getSeasonPoster(season)"
            class="season-poster-card-img"
            :alt="season.name || `Season ${season.season_number}`"
            loading="lazy"
            decoding="async"
          />
          <div v-else class="season-poster-card-placeholder">
            <span class="poster-num">{{ season.season_number }}</span>
          </div>
          <div class="season-poster-card-tag">
            <span class="season-poster-card-tag-name">{{
              season.name || `Season ${season.season_number}`
            }}</span>
            <span class="season-poster-card-tag-count"
              >{{ season.episode_count ?? season.episodes.length }} Episodes</span
            >
          </div>
        </button>

        <!-- Season info floats into the space reserved beside the center poster -->
        <div v-if="selectedSeason" class="season-info">
          <Transition name="season-info-swap" mode="out-in">
            <div :key="selectedSeasonIndex" class="season-info-inner">
              <h2 class="season-info-name">
                {{ selectedSeason.name || `Season ${selectedSeason.season_number}` }}
              </h2>
              <div class="season-info-line">
                <span v-if="formatDate(selectedSeason.air_date)">{{
                  formatDate(selectedSeason.air_date)
                }}</span>
                <span
                  >{{ selectedSeason.episode_count ?? selectedSeason.episodes.length }}
                  Episodes</span
                >
              </div>
              <p v-if="selectedSeason.overview" class="season-info-overview">
                {{ selectedSeason.overview }}
              </p>
            </div>
          </Transition>
        </div>
      </div>

      <!-- Episodes grid (selected season only) -->
      <div class="episodes-grid">
        <div
          v-for="(episode, eIndex) in selectedSeason?.episodes || []"
          :key="`${selectedSeasonIndex}-${episode.episode_number}`"
          class="episode-tile"
          :class="{ 'episode-tile--ahead': isEpisodeAhead(eIndex) }"
          v-bind="getEpisodeNavAttrs(selectedSeasonIndex, eIndex)"
          @click="handlePlay(episode)"
          @focusin="handleEpisodeFocusIn($event, eIndex)"
          @keydown.enter.prevent="handleEpisodeEnter($event, episode)"
          @mouseenter="handleEpisodeHover($event, `${eIndex}`, true)"
          @mouseleave="handleEpisodeHover(`${eIndex}`, false)"
          @contextmenu="handleContextMenu($event, episode)"
        >
          <!-- SVG focus outline -->
          <svg class="tile-focus-outline" viewBox="0 0 100 100" preserveAspectRatio="none">
            <rect x="0" y="0" width="100" height="100" />
          </svg>

          <!-- Episode preview media -->
          <div class="tile-media">
            <div class="tile-placeholder"></div>
            <img
              v-if="getEpisodeStill(episode)"
              :src="getEpisodeStill(episode)"
              class="tile-still"
              :alt="episode.name || `Episode ${episode.episode_number}`"
              loading="lazy"
              decoding="async"
            />
            <video
              v-if="getEpisodeVideoSources(episode).length > 0"
              :ref="(el) => setVideoRef(el as HTMLVideoElement, `${eIndex}`)"
              :autoplay="false"
              :preload="episodeMediaReady ? 'auto' : 'none'"
              loop
              muted
              playsinline
              @playing="handleVideoPlaying($event)"
            >
              <source
                v-for="source in getEpisodeVideoSources(episode)"
                :key="source.src"
                :src="source.src"
                :type="source.type"
                :codecs="source.codecs"
              />
            </video>
            <span class="ep-number">{{ episode.episode_number }}</span>
            <div class="tile-play">▶</div>
          </div>

          <!-- Episode text info -->
          <div class="tile-info">
            <span class="ep-name">{{ episode.name || `Episode ${episode.episode_number}` }}</span>
            <span class="ep-meta">
              <template v-if="formatDate(episode.air_date)">{{
                formatDate(episode.air_date)
              }}</template>
              <template v-if="episode.runtime"> · {{ formatRuntime(episode.runtime) }}</template>
              <template v-if="episode.rating"> · ★ {{ episode.rating.toFixed(1) }}</template>
            </span>
            <p v-if="episode.overview" class="ep-overview">{{ episode.overview }}</p>
          </div>
        </div>
        <div v-if="(selectedSeason?.episodes.length || 0) === 0" class="episodes-empty">
          No episodes in library
        </div>
      </div>

      <div v-if="matchingSeriesMovies.length > 0" class="linked-movies-section">
        <h2 class="linked-movies-title">Related Movies In Library</h2>
        <div class="linked-movies-grid">
          <button
            v-for="(movie, movieIndex) in matchingSeriesMovies"
            :key="movie.id"
            type="button"
            class="linked-movie-card"
            v-bind="navAttrs(linkedMoviesNavRow, movieIndex)"
            @click="emit('selectMovie', movie.id)"
          >
            <img
              v-if="movie.cover_path"
              :src="getCoverUrl(movie.cover_path, movie.root_id)"
              class="linked-movie-poster"
              :alt="movie.title || 'Movie'"
              loading="lazy"
              decoding="async"
            />
            <div v-else class="linked-movie-poster linked-movie-poster-fallback"></div>
            <div class="linked-movie-meta">
              <span class="linked-movie-name">{{ movie.title }}</span>
              <span class="linked-movie-sub">
                {{ movie.year || movie.info?.release_date?.slice(0, 4) || "Unknown Year" }}
                <template v-if="movie.info?.rating">
                  • ★ {{ movie.info.rating.toFixed(1) }}</template
                >
              </span>
            </div>
          </button>
        </div>
      </div>
    </section>

    <!-- Episode release menu -->
    <Teleport to="body">
      <div
        v-if="episodeReleaseMenu.visible"
        class="episode-release-menu-backdrop"
        @click="closeEpisodeReleaseMenu"
        @contextmenu.prevent="closeEpisodeReleaseMenu"
      ></div>
      <EpisodeReleaseMenu
        :visible="episodeReleaseMenu.visible"
        :x="episodeReleaseMenu.x"
        :y="episodeReleaseMenu.y"
        :episode-name="
          episodeReleaseMenu.episode?.name ||
          `Episode ${episodeReleaseMenu.episode?.episode_number}`
        "
        :releases="episodeReleaseMenuReleases"
        :has-resume-position="props.hasResumePosition"
        @play="handlePlayVersion"
        @open-folder="handleOpenFolderFromMenu"
        @close="closeEpisodeReleaseMenu"
      />
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, nextTick, watch, onMounted, onUnmounted } from "vue"
import type { Series, Season, Episode, MovieUi } from "../types"
import { getCoverUrl, getVideoPreviewUrl, getVideoSourceAttributes, isSafariBrowser } from "../api"
import { navAttrs, setModalOpen } from "../composables/useKeyboardNavigation"
import { useIdlePreviewPlayback } from "../composables/useIdlePreviewPlayback"
import EpisodeReleaseMenu from "./EpisodeReleaseMenu.vue"
import { sortTorrentsByPreference } from "../composables/useSettings"

const props = defineProps<{
  series: Series & { root_id?: string | null }
  allMovies: MovieUi[]
  focusEpisode?: { seasonNumber: number; episodeNumber: number } | null
  hasResumePosition: (filePath: string | null) => boolean
  getRootName: (rootId: string | null | undefined) => string | null
}>()

const emit = defineEmits<{
  close: []
  play: [string]
  openFolder: [string, string | null | undefined]
  selectMovie: [string]
}>()

const seriesRootRef = ref<HTMLElement | null>(null)
const episodeNavCoords = ref<Map<string, { row: number; col: number }>>(new Map())
const linkedMoviesNavRow = ref(3)
let navLayoutFrame: number | null = null

function getInitialSeasonIndex(): number {
  const seasonNumber = props.focusEpisode?.seasonNumber
  if (typeof seasonNumber === "number") {
    const index = props.series.seasons.findIndex((s) => s.season_number === seasonNumber)
    if (index >= 0) return index
  }
  return 0
}

const selectedSeasonIndex = ref(getInitialSeasonIndex())

const selectedSeason = computed<Season | null>(
  () => props.series.seasons[selectedSeasonIndex.value] || null,
)

function selectSeason(index: number) {
  if (index < 0 || index >= props.series.seasons.length) return
  if (selectedSeasonIndex.value === index) return
  selectedSeasonIndex.value = index
  episodeCursorIndex.value = null
  scheduleEpisodeMediaReady()
  syncSeasonVideoPlayback()
  nextTick(() => {
    scheduleEpisodeNavLayoutRecompute()
  })
}

// Episode media settle gating: videos mount with preload="none" and stay
// paused until the jukebox animation has settled, so switching seasons does
// not trigger a burst of video loads mid-transition. The episode still image
// underneath provides the preview picture in the meantime.
const episodeMediaReady = ref(false)
const EPISODE_MEDIA_SETTLE_MS = 600
let episodeMediaReadyTimer: ReturnType<typeof setTimeout> | null = null

function scheduleEpisodeMediaReady() {
  episodeMediaReady.value = false
  if (episodeMediaReadyTimer !== null) {
    clearTimeout(episodeMediaReadyTimer)
  }
  episodeMediaReadyTimer = setTimeout(() => {
    episodeMediaReadyTimer = null
    episodeMediaReady.value = true
    syncSeasonVideoPlayback()
  }, EPISODE_MEDIA_SETTLE_MS)
}

// Spoiler avoidance: episodes ahead of the cursor (keyboard/gamepad focus, or
// mouse hover via the focus it triggers) are dimmed with their synopsis hidden.
const episodeCursorIndex = ref<number | null>(null)

function isEpisodeAhead(episodeIndex: number): boolean {
  return episodeCursorIndex.value !== null && episodeIndex > episodeCursorIndex.value
}

function handleEpisodeFocusIn(event: FocusEvent, episodeIndex: number) {
  episodeCursorIndex.value = episodeIndex
  // Updating the cursor re-renders this tile's :class binding, and Vue's class
  // patch rewrites the whole class attribute, clobbering the "nav-focused"
  // class that the keyboard-navigation composable adds imperatively during
  // this same focusin dispatch. Re-add it once Vue has settled.
  const el = event.currentTarget as HTMLElement | null
  nextTick(() => {
    if (el && el === document.activeElement) {
      el.classList.add("nav-focused")
    }
  })
}

function getEpisodeStill(episode: Episode): string | undefined {
  if (episode.still_path) {
    return getCoverUrl(episode.still_path, props.series.root_id)
  }
  return undefined
}

function handleVideoPlaying(event: Event) {
  ;(event.target as HTMLVideoElement | null)?.classList.add("is-playing")
}

// Jukebox stage: cards are placed at constant angular steps on a semicircular
// arc of radius R around the selection — x = R·sin(θ), depth = R·(1−cos θ) —
// so the browser's perspective does a true 3D ring. Every season stays fully
// opaque and visible; extreme cards pile up nearly edge-on at the arc cap but
// are never hidden. The selected card is shifted left of center, leaving the
// right side free for the floating season-info panel.
const seasonStageRef = ref<HTMLElement | null>(null)
const stageWidth = ref(1280)

function measureStageWidth() {
  stageWidth.value = seasonStageRef.value?.clientWidth || window.innerWidth
}

// Keep in sync with .season-stage { perspective } in the styles.
const STAGE_PERSPECTIVE_PX = 1200

const stageMetrics = computed(() => {
  const width = stageWidth.value
  if (width <= 600) {
    // Info panel floats below the poster here, so the ring stays centered.
    return { posterWidth: 170, radius: 900, stepDeg: 16, rightStartDeg: 16, infoShift: 0, maxDeg: 80 }
  }
  if (width <= 900) {
    return { posterWidth: 230, radius: 1100, stepDeg: 14, rightStartDeg: 28, infoShift: 160, maxDeg: 80 }
  }
  return { posterWidth: 290, radius: 1300, stepDeg: 13, rightStartDeg: 32, infoShift: 200, maxDeg: 80 }
})

function getSeasonCardStyle(index: number): Record<string, string> {
  const offset = index - selectedSeasonIndex.value
  const absOffset = Math.abs(offset)
  const metrics = stageMetrics.value

  // Angular position on the arc. The right side starts past the opening
  // reserved for the season-info panel; the cap keeps extreme cards from
  // turning fully edge-on.
  let thetaDeg: number
  if (offset === 0) {
    thetaDeg = 0
  } else if (offset > 0) {
    thetaDeg = Math.min(metrics.rightStartDeg + (absOffset - 1) * metrics.stepDeg, metrics.maxDeg)
  } else {
    thetaDeg = -Math.min(metrics.stepDeg * absOffset, metrics.maxDeg)
  }

  const theta = (thetaDeg * Math.PI) / 180
  const depth = metrics.radius * (1 - Math.cos(theta))
  const visualWidth =
    metrics.posterWidth * (STAGE_PERSPECTIVE_PX / (STAGE_PERSPECTIVE_PX + depth))

  // Keep every card's (perspective-shrunk) edge inside the stage.
  const xLimit = Math.max(0, stageWidth.value / 2 - visualWidth / 2 - 8)
  const rawX = -metrics.infoShift + metrics.radius * Math.sin(theta)
  const translateX = Math.max(-xLimit, Math.min(rawX, xLimit))

  return {
    transform: `translateX(-50%) translateX(${translateX}px) translateZ(${-depth}px) rotateY(${-thetaDeg}deg)`,
    zIndex: String(100 - absOffset),
  }
}

function handleStageResize() {
  measureStageWidth()
  scheduleEpisodeNavLayoutRecompute()
}

function focusFirstEpisode() {
  const firstTile = seriesRootRef.value?.querySelector<HTMLElement>(".episode-tile")
  firstTile?.focus()
}

const EPISODE_NAV_FIRST_ROW = 3

function getEpisodeKey(episodeIndex: number): string {
  return `${episodeIndex}`
}

function getEpisodeNavAttrs(seasonIndex: number, episodeIndex: number) {
  const key = getEpisodeKey(episodeIndex)
  const coords = episodeNavCoords.value.get(key) || {
    row: EPISODE_NAV_FIRST_ROW,
    col: episodeIndex,
  }
  return {
    ...navAttrs(coords.row, coords.col),
    "data-season-index": seasonIndex,
    "data-episode-index": episodeIndex,
  }
}

function recomputeEpisodeNavLayout() {
  const root = seriesRootRef.value
  if (!root) return

  const nextCoords = new Map<string, { row: number; col: number }>()
  let currentRow = EPISODE_NAV_FIRST_ROW

  const tiles = Array.from(root.querySelectorAll<HTMLElement>(".episode-tile")).sort((a, b) => {
    const aIndex = parseInt(a.getAttribute("data-episode-index") || "0", 10)
    const bIndex = parseInt(b.getAttribute("data-episode-index") || "0", 10)
    return aIndex - bIndex
  })

  if (tiles.length === 0) {
    const fallbackCount = selectedSeason.value?.episodes?.length || 0
    for (let episodeIndex = 0; episodeIndex < fallbackCount; episodeIndex += 1) {
      nextCoords.set(getEpisodeKey(episodeIndex), { row: currentRow, col: episodeIndex })
    }
    if (fallbackCount > 0) {
      currentRow += 1
    }
  } else {
    let lastTop: number | null = null
    let rowOffset = -1
    let colInRow = 0

    for (const tile of tiles) {
      const episodeIndex = parseInt(tile.getAttribute("data-episode-index") || "-1", 10)
      if (episodeIndex < 0) continue

      const { top } = tile.getBoundingClientRect()
      if (lastTop === null || Math.abs(top - lastTop) > 4) {
        lastTop = top
        rowOffset += 1
        colInRow = 0
      }

      nextCoords.set(getEpisodeKey(episodeIndex), {
        row: currentRow + rowOffset,
        col: colInRow,
      })
      colInRow += 1
    }

    if (rowOffset >= 0) {
      currentRow += rowOffset + 1
    }
  }

  episodeNavCoords.value = nextCoords
  linkedMoviesNavRow.value = currentRow
}

function scheduleEpisodeNavLayoutRecompute() {
  if (navLayoutFrame !== null) return
  navLayoutFrame = window.requestAnimationFrame(() => {
    navLayoutFrame = null
    recomputeEpisodeNavLayout()
  })
}

watch(
  () => props.series.seasons.map((season) => season.episodes.length),
  () => {
    nextTick(() => {
      scheduleEpisodeNavLayoutRecompute()
    })
  },
  { immediate: true },
)

// Focus on matched episode when provided
watch(
  () => props.focusEpisode,
  (ep) => {
    if (!ep) return
    const seasonIndex =
      props.series.seasons?.findIndex((s) => s.season_number === ep.seasonNumber) ?? -1
    if (seasonIndex < 0) return
    selectedSeasonIndex.value = seasonIndex
    // Delay to ensure DOM is fully rendered after season switch / route transition
    nextTick(() => {
      setTimeout(() => {
        const episodeIndex =
          props.series.seasons?.[seasonIndex]?.episodes?.findIndex(
            (e) => e.episode_number === ep.episodeNumber,
          ) ?? -1
        if (episodeIndex < 0) return
        const selector = `.episode-tile[data-season-index="${seasonIndex}"][data-episode-index="${episodeIndex}"]`
        const element = seriesRootRef.value?.querySelector(selector) as HTMLElement | null
        if (element) {
          element.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" })
          element.focus()
        }
      }, 150)
    })
  },
  { immediate: true },
)

// Episode release menu state (single-layer menu for all releases)
const episodeReleaseMenu = ref<{
  visible: boolean
  x: number
  y: number
  episode: Episode | null
}>({
  visible: false,
  x: 0,
  y: 0,
  episode: null,
})

const releaseMenuOriginElement = ref<HTMLElement | null>(null)

const episodeReleaseMenuReleases = computed(() => {
  if (!episodeReleaseMenu.value.episode) return []
  return sortTorrentsByPreference(Object.values(episodeReleaseMenu.value.episode.files || {}))
})

function normalizeMatchText(value: string | null | undefined): string {
  return (value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
}

const seriesMatchNeedles = computed(() => {
  const candidates = new Set<string>()
  const push = (value: string | null | undefined) => {
    const normalized = normalizeMatchText(value)
    if (normalized.length >= 3) {
      candidates.add(normalized)
    }
  }

  push(props.series.title)
  push(props.series.info?.title)
  push(props.series.info?.original_title)
  for (const alt of props.series.alternative_titles || []) {
    push(alt)
  }
  for (const alt of props.series.info?.alternative_titles || []) {
    push(alt)
  }

  return Array.from(candidates)
})

const matchingSeriesMovies = computed(() => {
  const needles = seriesMatchNeedles.value
  if (needles.length === 0) return []

  return (props.allMovies || [])
    .filter((movie) => {
      const keywords = (movie.info?.keywords || []).map((k) => normalizeMatchText(k))
      if (!keywords.includes("based on tv series")) {
        return false
      }

      const movieTitle = normalizeMatchText(movie.title || movie.info?.title || "")
      if (!movieTitle) {
        return false
      }

      return needles.some((needle) => movieTitle.includes(needle))
    })
    .sort((a, b) => {
      const yearA = a.year || Number(a.info?.release_date?.slice(0, 4) || 0)
      const yearB = b.year || Number(b.info?.release_date?.slice(0, 4) || 0)
      if (yearA !== yearB) return yearA - yearB

      const dateA = a.info?.release_date || ""
      const dateB = b.info?.release_date || ""
      if (dateA !== dateB) return dateA.localeCompare(dateB)

      return (a.title || "").localeCompare(b.title || "")
    })
})

// Show episode release menu on right-click (single-layer menu)
function handleContextMenu(event: MouseEvent, episode: Episode) {
  event.preventDefault()
  releaseMenuOriginElement.value = event.currentTarget as HTMLElement | null
  openEpisodeReleaseMenu(episode, event.clientX, event.clientY)
}

function openEpisodeReleaseMenu(episode: Episode, x: number, y: number) {
  setModalOpen(true)
  episodeReleaseMenu.value = {
    visible: true,
    x,
    y,
    episode,
  }
  // Add Escape key listener as safety net (capture phase)
  nextTick(() => {
    document.addEventListener("keydown", handleEpisodeMenuEscape, true)
  })
}

function openEpisodeReleaseMenuFromElement(episode: Episode, element: HTMLElement | null) {
  releaseMenuOriginElement.value = element
  if (!element) {
    openEpisodeReleaseMenu(episode, window.innerWidth / 2, window.innerHeight / 2)
    return
  }
  const rect = element.getBoundingClientRect()
  openEpisodeReleaseMenu(episode, rect.left + rect.width / 2, rect.top + rect.height / 2)
}

// Capture-phase Escape handler as safety net for episode release menu
function handleEpisodeMenuEscape(event: KeyboardEvent) {
  if (!episodeReleaseMenu.value.visible) return
  if (event.key === "Escape") {
    event.preventDefault()
    event.stopPropagation()
    closeEpisodeReleaseMenu()
  }
}

// Close episode release menu
function closeEpisodeReleaseMenu() {
  episodeReleaseMenu.value.visible = false
  episodeReleaseMenu.value.episode = null
  document.removeEventListener("keydown", handleEpisodeMenuEscape, true)
  setModalOpen(false)
  nextTick(() => {
    releaseMenuOriginElement.value?.focus()
  })
}

function handleGamepadAction(event: Event) {
  const actionEvent = event as CustomEvent<{ action?: string }>
  if (actionEvent.detail?.action !== "menu") return

  const active = document.activeElement as HTMLElement | null
  if (!active) return

  // Episode tile on series page
  if (active.classList.contains("episode-tile")) {
    const seasonIndex = parseInt(active.getAttribute("data-season-index") || "-1", 10)
    const episodeIndex = parseInt(active.getAttribute("data-episode-index") || "-1", 10)
    if (seasonIndex < 0 || episodeIndex < 0) return

    const season = props.series.seasons?.[seasonIndex]
    const episode = season?.episodes?.[episodeIndex]
    if (!episode) return

    actionEvent.preventDefault()
    openEpisodeReleaseMenuFromElement(episode, active)
    return
  }
}

// Play specific version
function handlePlayVersion(filePath: string | null) {
  if (filePath) {
    emit("play", filePath)
  }
  closeEpisodeReleaseMenu()
}

// Open folder for a version from the episode release menu
function handleOpenFolderFromMenu(filePath: string) {
  if (!filePath) return
  const torrent = episodeReleaseMenu.value.episode?.files
    ? Object.values(episodeReleaseMenu.value.episode.files).find(
        (t) => t.playable_file === filePath,
      )
    : undefined
  const rootId = torrent?.root_id ?? props.series.root_id ?? null
  emit("openFolder", filePath, rootId)
  closeEpisodeReleaseMenu()
}

// Video refs for hover effects (key = episode index; only one season is mounted)
const videoRefs = ref<Map<string, HTMLVideoElement>>(new Map())
const safariAutoplay = isSafariBrowser()
const SEASON_VIDEO_STARTUP_STEP_MS = 500
const seasonStartupTimers = new Map<string, ReturnType<typeof setTimeout>>()
let seasonStartupToken = 0

// Episode videos the IntersectionObserver has reported as off-screen. This is
// a blocklist, not an allowlist: a video may play until reported otherwise, so
// playback degrades to always-on if observation fails or lags.
const offscreenEpisodeKeys = new Set<string>()
let episodeVisibilityObserver: IntersectionObserver | null = null

const { stopped: previewPlaybackStopped } = useIdlePreviewPlayback({
  onStop: stopEpisodePreviews,
  onRestart: () => syncSeasonVideoPlayback(),
})

function parseEpisodeIndex(key: string): number | null {
  const episodeIndex = parseInt(key, 10)
  return Number.isFinite(episodeIndex) ? episodeIndex : null
}

function clearSeasonStartupTimers() {
  for (const timeoutId of seasonStartupTimers.values()) {
    clearTimeout(timeoutId)
  }
  seasonStartupTimers.clear()
}

function getEpisodeVisibilityObserver(): IntersectionObserver {
  if (!episodeVisibilityObserver) {
    episodeVisibilityObserver = new IntersectionObserver(
      (entries) => {
        let changed = false
        for (const entry of entries) {
          const key = (entry.target as HTMLElement).dataset.previewKey
          if (!key) continue
          if (entry.isIntersecting) {
            if (offscreenEpisodeKeys.delete(key)) {
              changed = true
            }
          } else if (!offscreenEpisodeKeys.has(key)) {
            offscreenEpisodeKeys.add(key)
            changed = true
          }
        }
        if (changed) {
          syncSeasonVideoPlayback()
        }
      },
      { rootMargin: "100px 0px" },
    )
  }
  return episodeVisibilityObserver
}

function stopEpisodePreviews() {
  seasonStartupToken += 1
  const token = seasonStartupToken
  clearSeasonStartupTimers()
  clearEpisodeHoverAudioIdleTimer()
  hoveredEpisodeAudioKey = null
  for (const interval of volumeFadeIntervals.values()) {
    clearInterval(interval)
  }
  volumeFadeIntervals.clear()

  // Stagger the stop instead of pausing everything at once
  let stopIndex = 0
  for (const [key, video] of videoRefs.value.entries()) {
    if (!video.paused && !video.ended) {
      const timeoutId = setTimeout(() => {
        seasonStartupTimers.delete(key)
        if (token !== seasonStartupToken) return
        pauseEpisodeVideo(video)
      }, stopIndex * SEASON_VIDEO_STARTUP_STEP_MS)
      seasonStartupTimers.set(key, timeoutId)
      stopIndex += 1
    } else {
      pauseEpisodeVideo(video)
    }
  }
}

function pauseEpisodeVideo(video: HTMLVideoElement) {
  video.pause()
  video.classList.remove("is-playing")
  if (video.readyState >= 1) {
    video.currentTime = 0
  }
  video.muted = true
  video.volume = 0
}

function syncSeasonVideoPlayback(priorityKey?: string) {
  seasonStartupToken += 1
  const token = seasonStartupToken
  clearSeasonStartupTimers()
  for (const interval of volumeFadeIntervals.values()) {
    clearInterval(interval)
  }
  volumeFadeIntervals.clear()

  const videosToStart: Array<{ key: string; episodeIndex: number; video: HTMLVideoElement }> = []
  const videosToStop: Array<{ key: string; episodeIndex: number; video: HTMLVideoElement }> = []

  for (const [key, video] of videoRefs.value.entries()) {
    const episodeIndex = parseEpisodeIndex(key)
    if (episodeIndex === null) {
      pauseEpisodeVideo(video)
      continue
    }

    // Only on-screen tiles may play, and only while the user is active.
    const eligible =
      episodeMediaReady.value &&
      !isEpisodeAhead(episodeIndex) &&
      !offscreenEpisodeKeys.has(key) &&
      !previewPlaybackStopped.value

    if (!eligible) {
      if (!video.paused && !video.ended) {
        videosToStop.push({ key, episodeIndex, video })
      } else {
        pauseEpisodeVideo(video)
      }
      continue
    }

    // Already playing and still eligible: leave it running so visibility
    // updates (scrolling, idle resume) don't restart it from the beginning.
    if (!video.paused && !video.ended) {
      continue
    }

    pauseEpisodeVideo(video)
    videosToStart.push({
      key,
      episodeIndex,
      video,
    })
  }

  videosToStart.sort((a, b) => {
    if (priorityKey) {
      if (a.key === priorityKey) return -1
      if (b.key === priorityKey) return 1
    }
    return a.episodeIndex - b.episodeIndex
  })

  for (let i = 0; i < videosToStart.length; i += 1) {
    const { key, video } = videosToStart[i]
    const delayMs = priorityKey
      ? i === 0
        ? 0
        : i * SEASON_VIDEO_STARTUP_STEP_MS
      : i * SEASON_VIDEO_STARTUP_STEP_MS
    const timeoutId = setTimeout(() => {
      if (token !== seasonStartupToken || previewPlaybackStopped.value) return
      if (safariAutoplay && video.readyState >= 1) {
        video.currentTime = 0.001
      }
      video.play().catch(() => {})
      seasonStartupTimers.delete(key)
    }, delayMs)
    seasonStartupTimers.set(key, timeoutId)
  }

  // Stop no-longer-eligible videos with the same stagger instead of all at once
  videosToStop.sort((a, b) => a.episodeIndex - b.episodeIndex)
  for (let i = 0; i < videosToStop.length; i += 1) {
    const { key, video } = videosToStop[i]
    const timeoutId = setTimeout(() => {
      seasonStartupTimers.delete(key)
      if (token !== seasonStartupToken) return
      pauseEpisodeVideo(video)
    }, i * SEASON_VIDEO_STARTUP_STEP_MS)
    seasonStartupTimers.set(key, timeoutId)
  }
}

function cleanupVideo(video: HTMLVideoElement | null | undefined) {
  if (!video) return
  video.pause()
  video.src = ""
  video.load()
}

// Track mounted videos and sync playback.
function setVideoRef(el: HTMLVideoElement | null, key: string) {
  if (el) {
    const existing = videoRefs.value.get(key)
    if (existing === el) {
      return
    }
    if (existing && existing !== el) {
      cleanupVideo(existing)
    }

    videoRefs.value.set(key, el)
    // Observe the video itself (it fills the tile, so same visibility box).
    // Note: el.closest() is unreliable here — ref callbacks can fire before
    // the element's ancestors are attached.
    el.dataset.previewKey = key
    getEpisodeVisibilityObserver().observe(el)
    syncSeasonVideoPlayback()
  } else {
    const timeoutId = seasonStartupTimers.get(key)
    if (timeoutId) {
      clearTimeout(timeoutId)
      seasonStartupTimers.delete(key)
    }
    const old = videoRefs.value.get(key)
    if (old) {
      cleanupVideo(old)
    }
    videoRefs.value.delete(key)
  }
}

// Volume fade animation tracking
const volumeFadeIntervals = new Map<string, ReturnType<typeof setInterval>>()
const AUDIO_FADE_STEP = 0.04
const AUDIO_FADE_INTERVAL_MS = 40
const AUDIO_IDLE_FADE_DELAY_MS = 1600
const AUDIO_LEAVE_FADE_DELAY_MS = 350
const AUDIO_HOVER_TARGET_VOLUME = 0.5
let hoveredEpisodeAudioKey: string | null = null
let hoverEpisodeAudioIdleTimer: ReturnType<typeof setTimeout> | null = null

function clearEpisodeHoverAudioIdleTimer() {
  if (hoverEpisodeAudioIdleTimer !== null) {
    clearTimeout(hoverEpisodeAudioIdleTimer)
    hoverEpisodeAudioIdleTimer = null
  }
}

function rampEpisodeVolume(key: string, targetVolume: number) {
  const video = videoRefs.value.get(key)
  if (!video) return

  const existingInterval = volumeFadeIntervals.get(key)
  if (existingInterval) {
    clearInterval(existingInterval)
    volumeFadeIntervals.delete(key)
  }

  const clampedTarget = Math.max(0, Math.min(1, targetVolume))
  if (clampedTarget > 0 && video.muted) {
    video.volume = 0
  }
  if (clampedTarget > 0) {
    video.muted = false
  }

  const fadeInterval = setInterval(() => {
    const delta = clampedTarget - video.volume
    if (Math.abs(delta) <= AUDIO_FADE_STEP) {
      video.volume = clampedTarget
      if (clampedTarget <= 0.001) {
        video.muted = true
      }
      clearInterval(fadeInterval)
      volumeFadeIntervals.delete(key)
      return
    }

    video.volume += delta > 0 ? AUDIO_FADE_STEP : -AUDIO_FADE_STEP
  }, AUDIO_FADE_INTERVAL_MS)

  volumeFadeIntervals.set(key, fadeInterval)
}

function scheduleEpisodeHoverAudioIdleFade(
  key: string,
  delayMs: number = AUDIO_IDLE_FADE_DELAY_MS,
) {
  clearEpisodeHoverAudioIdleTimer()
  hoverEpisodeAudioIdleTimer = setTimeout(() => {
    rampEpisodeVolume(key, 0)
    if (hoveredEpisodeAudioKey === key) {
      hoveredEpisodeAudioKey = null
    }
  }, delayMs)
}

function handleEpisodeHoverAudioMouseMove() {
  if (!document.documentElement.classList.contains("mouse-active")) return
  if (!hoveredEpisodeAudioKey) return
  scheduleEpisodeHoverAudioIdleFade(hoveredEpisodeAudioKey)
}

// Handle hover-based audio fade in/out for episode videos
function handleEpisodeHover(
  eventOrKey: MouseEvent | string,
  keyOrIsEntering: string | boolean,
  maybeIsEntering?: boolean,
) {
  const event = typeof eventOrKey === "string" ? null : eventOrKey
  const key = typeof eventOrKey === "string" ? eventOrKey : (keyOrIsEntering as string)
  const isEntering =
    typeof eventOrKey === "string" ? Boolean(keyOrIsEntering) : Boolean(maybeIsEntering)

  if (!document.documentElement.classList.contains("mouse-active")) return

  const video = videoRefs.value.get(key)
  if (!video) return

  if (isEntering) {
    if (event?.currentTarget instanceof HTMLElement) {
      event.currentTarget.focus({ preventScroll: true })
    }
    syncSeasonVideoPlayback(key)
    hoveredEpisodeAudioKey = key
    videoRefs.value.forEach((_, k) => {
      if (k !== key) {
        rampEpisodeVolume(k, 0)
      }
    })
    rampEpisodeVolume(key, AUDIO_HOVER_TARGET_VOLUME)
    scheduleEpisodeHoverAudioIdleFade(key)
  } else {
    if (hoveredEpisodeAudioKey === key) {
      hoveredEpisodeAudioKey = null
    }
    scheduleEpisodeHoverAudioIdleFade(key, AUDIO_LEAVE_FADE_DELAY_MS)
  }
}

// Backdrop URL - only use backdrop_path, fall back to collage (handled in template)
const backdropUrl = computed(() => {
  if (props.series.backdrop_path) {
    return getCoverUrl(props.series.backdrop_path, props.series.root_id)
  }
  return null
})

// Seasons that have poster images
const seasonsWithPosters = computed(() => {
  return props.series.seasons.filter((s) => s.poster_path)
})

// Rating class
const ratingClass = computed(() => {
  if (!props.series.info?.rating) return ""
  if (props.series.info.rating >= 7.5) return "rating-high"
  if (props.series.info.rating >= 6) return "rating-medium"
  return "rating-low"
})

// Get season poster
function getSeasonPoster(season: Season): string | undefined {
  if (season.poster_path) {
    return getCoverUrl(season.poster_path, props.series.root_id)
  }
  return undefined
}

function getEpisodeVideoSources(
  episode: Episode,
): Array<{ src: string; type: string; codecs: string }> {
  const sources =
    episode.reel_sources && episode.reel_sources.length > 0
      ? episode.reel_sources
      : episode.reel_image
        ? [episode.reel_image]
        : []

  return sources.map((path) => ({
    src: getVideoPreviewUrl(getCoverUrl(path, props.series.root_id)),
    ...getVideoSourceAttributes(path),
  }))
}

// Collage slice style for season posters
function getCollageSliceStyle(season: Season, index: number) {
  const posterUrl = season.poster_path
    ? getCoverUrl(season.poster_path, props.series.root_id)
    : null
  const totalSlices = Math.min(seasonsWithPosters.value.length, 5)
  const sliceWidth = 100 / totalSlices
  const actualWidth = sliceWidth + 5 // overlap in container percentage points
  // The slant (horizontal offset from top to bottom) should equal the overlap
  // measured relative to each slice's own width.
  const slant = (5 / actualWidth) * 100

  let clipPath: string
  if (totalSlices === 1) {
    clipPath = "polygon(0 0, 100% 0, 100% 100%, 0 100%)"
  } else if (index === 0) {
    // First slice: straight left edge, slanted right edge
    clipPath = `polygon(0 0, 100% 0, ${100 - slant}% 100%, 0 100%)`
  } else if (index === totalSlices - 1) {
    // Last slice: slanted left edge, straight right edge
    clipPath = `polygon(${slant}% 0, 100% 0, 100% 100%, 0 100%)`
  } else {
    // Middle slices: slanted on both sides
    clipPath = `polygon(${slant}% 0, 100% 0, ${100 - slant}% 100%, 0 100%)`
  }

  return {
    backgroundImage: posterUrl
      ? `url('${posterUrl}')`
      : "linear-gradient(135deg, #1a1a2e, #16213e)",
    left: `${index * sliceWidth}%`,
    width: `${actualWidth}%`,
    clipPath,
  }
}

function formatDate(value: string | null | undefined): string | null {
  if (!value) return null
  const parsedDate = new Date(value)
  if (Number.isNaN(parsedDate.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(parsedDate)
}

function formatRuntime(minutes: number): string {
  const hours = Math.floor(minutes / 60)
  const mins = minutes % 60
  if (hours === 0) return `${mins}m`
  return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`
}

// Handle play
function handlePlay(episode: Episode) {
  const playableFile = Object.values(episode.files || {})[0]?.playable_file
  if (playableFile) {
    emit("play", playableFile)
  }
}

function handleEpisodeEnter(event: KeyboardEvent, episode: Episode) {
  if (event.altKey || event.metaKey || event.ctrlKey) {
    openEpisodeReleaseMenuFromElement(episode, event.currentTarget as HTMLElement | null)
    return
  }
  handlePlay(episode)
}

onMounted(() => {
  window.addEventListener("mediahive:gamepad-action", handleGamepadAction as EventListener)
  window.addEventListener("resize", handleStageResize, { passive: true })
  window.addEventListener("mousemove", handleEpisodeHoverAudioMouseMove, { passive: true })
  nextTick(() => {
    measureStageWidth()
    scheduleEpisodeMediaReady()
    scheduleEpisodeNavLayoutRecompute()
  })
})

onUnmounted(() => {
  window.removeEventListener("mediahive:gamepad-action", handleGamepadAction as EventListener)
  window.removeEventListener("resize", handleStageResize)
  window.removeEventListener("mousemove", handleEpisodeHoverAudioMouseMove)
  clearEpisodeHoverAudioIdleTimer()
  clearSeasonStartupTimers()
  if (episodeMediaReadyTimer !== null) {
    clearTimeout(episodeMediaReadyTimer)
    episodeMediaReadyTimer = null
  }
  episodeVisibilityObserver?.disconnect()
  episodeVisibilityObserver = null
  offscreenEpisodeKeys.clear()
  if (navLayoutFrame !== null) {
    window.cancelAnimationFrame(navLayoutFrame)
    navLayoutFrame = null
  }
  // Clear all volume fade intervals
  for (const interval of volumeFadeIntervals.values()) {
    clearInterval(interval)
  }
  volumeFadeIntervals.clear()
  // Pause and unload all video elements
  for (const video of videoRefs.value.values()) {
    cleanupVideo(video)
  }
  videoRefs.value.clear()
})

watch(
  () => props.series.seasons.length,
  () => {
    if (selectedSeasonIndex.value >= props.series.seasons.length) {
      selectedSeasonIndex.value = Math.max(0, props.series.seasons.length - 1)
    }
    syncSeasonVideoPlayback()
  },
)

// Episodes ahead of the cursor are spoiler-faded; stop their playback too.
watch(episodeCursorIndex, () => {
  syncSeasonVideoPlayback()
})
</script>

<style scoped>
.series-fullscreen {
  min-height: calc(100vh - 60px); /* Account for header height */
  background: #0a0a0a;
  overflow-x: hidden;
}

.linked-movies-section {
  margin: 40px 30px 20px;
}

.linked-movies-title {
  margin: 0 0 14px;
  font-size: 1.2rem;
  font-weight: 700;
}

.linked-movies-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 14px;
}

.linked-movie-card {
  appearance: none;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 12px;
  padding: 0;
  background: rgba(255, 255, 255, 0.03);
  color: inherit;
  cursor: pointer;
  overflow: hidden;
  text-align: left;
}

.linked-movie-card:hover,
.linked-movie-card:focus-visible {
  border-color: rgba(255, 255, 255, 0.4);
}

.linked-movie-poster {
  width: 100%;
  aspect-ratio: 2 / 3;
  object-fit: cover;
  display: block;
}

.linked-movie-poster-fallback {
  background: linear-gradient(135deg, #272b34, #181c24);
}

.linked-movie-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px;
}

.linked-movie-name {
  font-size: 0.92rem;
  font-weight: 600;
  line-height: 1.3;
}

.linked-movie-sub {
  font-size: 0.8rem;
  color: rgba(255, 255, 255, 0.75);
}

/* Hero section */
.series-hero {
  position: relative;
  height: 70vh;
  min-height: 450px;
  max-height: 600px;
  overflow: hidden;
}

.hero-bg {
  position: absolute;
  inset: 0;
}

.hero-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center top;
}

.hero-collage {
  position: relative;
  width: 100%;
  height: 100%;
}

.collage-slice {
  position: absolute;
  top: 0;
  height: 100%;
  background-size: cover;
  background-position: center;
}

.hero-gradient {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    to bottom,
    rgba(10, 10, 10, 0.3) 0%,
    rgba(10, 10, 10, 0.5) 50%,
    rgba(10, 10, 10, 1) 100%
  );
}

.hero-content {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 40px 60px;
  z-index: 10;
}

.series-title {
  font-size: clamp(2rem, 5vw, 3.5rem);
  font-weight: 800;
  margin: 0 0 16px 0;
  text-shadow: 0 4px 20px rgba(0, 0, 0, 0.8);
  letter-spacing: -1px;
}

.series-meta {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.meta-rating {
  font-weight: 700;
  font-size: 1.1rem;
  padding: 4px 12px;
  background: rgba(0, 0, 0, 0.6);
  border-radius: 6px;
}

.rating-high {
  color: #46d369;
}
.rating-medium {
  color: #f9a825;
}
.rating-low {
  color: #e53935;
}

.meta-item {
  color: rgba(255, 255, 255, 0.8);
  font-size: 0.95rem;
}

.meta-badge {
  background: rgba(255, 255, 255, 0.15);
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 600;
  text-transform: uppercase;
}

.meta-genres {
  color: rgba(255, 255, 255, 0.7);
  font-size: 0.9rem;
}

.series-overview {
  max-width: 700px;
  font-size: 1rem;
  line-height: 1.6;
  color: rgba(255, 255, 255, 0.85);
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 10;
  line-clamp: 10;
  max-height: calc(1.6em * 10);
}

/* Seasons container */
.seasons-container {
  padding: 0 0 60px;
}

/* Season selector jukebox: selected season centered at full poster size,
   neighbours stack up scaled/rotated on both sides, clamped inside the stage */
.season-stage {
  position: relative;
  height: calc(var(--poster-w, 290px) * 1.5 + 12px);
  margin: 28px 0 12px;
  perspective: 1200px;
  overflow: hidden;
}

.season-poster-card {
  appearance: none;
  position: absolute;
  left: 50%;
  top: 0;
  width: var(--poster-w, 290px);
  height: calc(var(--poster-w, 290px) * 1.5);
  display: flex;
  border: 2px solid transparent;
  border-radius: 14px;
  padding: 0;
  background: rgba(255, 255, 255, 0.05);
  color: inherit;
  cursor: pointer;
  overflow: hidden;
  text-align: left;
  transform-style: preserve-3d;
  transition:
    transform 0.45s ease,
    opacity 0.45s ease,
    border-color 0.25s ease,
    visibility 0.45s ease;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
}

.season-poster-card:focus {
  outline: none;
}

html.mouse-active .season-poster-card:hover,
html:not(.mouse-active) .season-poster-card.nav-focused,
.season-poster-card:focus-visible {
  border-color: rgba(255, 255, 255, 0.55);
}

.season-poster-card-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.season-poster-card-placeholder {
  width: 100%;
  height: 100%;
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
  display: flex;
  align-items: center;
  justify-content: center;
}

.poster-num {
  font-size: 3.4rem;
  font-weight: 800;
  color: rgba(255, 255, 255, 0.15);
}

/* Name tag shown on non-selected posters only */
.season-poster-card-tag {
  position: absolute;
  left: 0;
  bottom: 0;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 22px 10px 8px;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.9) 0%, transparent 100%);
  opacity: 1;
  transition: opacity 0.3s ease;
  pointer-events: none;
}

.season-poster-card--selected .season-poster-card-tag {
  opacity: 0;
}

.season-poster-card-tag-name {
  font-size: 0.78rem;
  font-weight: 600;
  line-height: 1.25;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.season-poster-card-tag-count {
  font-size: 0.66rem;
  color: rgba(255, 255, 255, 0.65);
}

/* Season info floats in the space freed by shifting the selected poster left */
.season-info {
  position: absolute;
  left: calc(50% - var(--info-shift, 0px) + var(--poster-w, 290px) / 2 + 30px);
  top: 50%;
  transform: translateY(-50%);
  width: 320px;
  z-index: 101;
  pointer-events: none;
}

.season-info-inner {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.season-info-swap-enter-active,
.season-info-swap-leave-active {
  transition:
    opacity 0.3s ease,
    transform 0.3s ease;
}

.season-info-swap-enter-from {
  opacity: 0;
  transform: translateY(14px);
}

.season-info-swap-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}

.season-info-name {
  margin: 0;
  font-size: 1.7rem;
  font-weight: 700;
  line-height: 1.2;
}

.season-info-line {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  color: #8ee59b;
  font-size: 0.82rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.season-info-overview {
  margin: 0;
  font-size: 0.9rem;
  line-height: 1.55;
  color: rgba(255, 255, 255, 0.85);
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 8;
  line-clamp: 8;
  max-height: calc(1.55em * 8);
}

/* Episodes grid (selected season only) */
.episodes-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
  padding: 0 30px 30px;
}

.episodes-empty {
  grid-column: 1 / -1;
  padding: 30px 0;
  text-align: center;
  color: rgba(255, 255, 255, 0.5);
  font-size: 0.95rem;
}

/* Episode tile */
.episode-tile {
  position: relative;
  border-radius: 10px;
  overflow: hidden;
  cursor: pointer;
  background: rgba(255, 255, 255, 0.04);
  outline: none;
}

.episode-tile:focus {
  outline: none;
}

.episode-tile:focus-visible {
  outline: none;
}

/* Blinking animation for focus outline */
@keyframes tile-outline-blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.4;
  }
}

/* SVG focus outline styles for tiles */
.tile-focus-outline {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 5;
  opacity: 0;
  transition: opacity 0.2s ease;
}

.tile-focus-outline rect {
  fill: none;
  stroke: rgba(255, 255, 255, 0.9);
  stroke-width: 4;
  vector-effect: non-scaling-stroke;
}

/* Show outline on hover and focus */
html.mouse-active .episode-tile:hover .tile-focus-outline,
html:not(.mouse-active) .episode-tile.nav-focused .tile-focus-outline {
  opacity: 1;
  animation: tile-outline-blink 1s ease-in-out infinite;
}

/* Brighter outline for keyboard focus */
html:not(.mouse-active) .episode-tile.nav-focused .tile-focus-outline rect {
  stroke: #ffffff;
  stroke-width: 5;
  filter: drop-shadow(0 0 6px rgba(255, 255, 255, 0.8));
}

/* Episode preview media area */
.tile-media {
  position: relative;
  aspect-ratio: 16 / 9;
  transition: opacity 0.4s ease;
}

.tile-placeholder {
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, #1f1f2e 0%, #141428 100%);
}

.tile-still {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* Videos stay hidden (and preload="none") until playback starts after the
   season switch has settled; they fade in softly over the still image. */
.tile-media video {
  position: absolute;
  inset: 0;
  z-index: 1;
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  opacity: 0;
  transition: opacity 0.6s ease;
}

.tile-media video.is-playing {
  opacity: 1;
}

/* Spoiler avoidance: episodes ahead of the cursor fade out completely,
   including the synopsis (playback is stopped via the cursor watch). */
.episode-tile--ahead .tile-media {
  opacity: 0;
}

.episode-tile--ahead .tile-info {
  opacity: 0;
}

.episode-tile--ahead .ep-overview {
  opacity: 0;
  visibility: hidden;
}

.ep-number {
  position: absolute;
  top: 8px;
  left: 10px;
  font-size: 1.6rem;
  font-weight: 900;
  color: white;
  text-shadow: 0 2px 10px rgba(0, 0, 0, 0.8);
  opacity: 0.9;
  line-height: 1;
  pointer-events: none;
}

/* Play indicator */
.tile-play {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%) scale(0.8);
  font-size: 2rem;
  color: white;
  opacity: 0;
  transition: all 0.3s ease;
  text-shadow: 0 4px 20px rgba(0, 0, 0, 0.8);
  pointer-events: none;
}

html.mouse-active .episode-tile:hover .tile-play {
  opacity: 1;
  transform: translate(-50%, -50%) scale(1);
}

/* Episode text info */
.tile-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px 12px;
}

.ep-name {
  font-size: 0.95rem;
  font-weight: 600;
  line-height: 1.3;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.ep-meta {
  font-size: 0.75rem;
  color: rgba(255, 255, 255, 0.65);
  font-weight: 500;
}

.ep-overview {
  margin: 2px 0 0;
  font-size: 0.8rem;
  line-height: 1.45;
  color: rgba(255, 255, 255, 0.7);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  transition: opacity 0.4s ease;
}

.tile-info {
  transition: opacity 0.4s ease;
}

/* Responsive */
@media (max-width: 900px) {
  .hero-content {
    padding: 30px;
  }

  .series-overview {
    -webkit-line-clamp: 10;
    line-clamp: 10;
    max-height: calc(1.6em * 10);
  }

  .ep-number {
    font-size: 1.4rem;
  }
}

@media (max-width: 900px) {
  .season-info {
    width: 240px;
    left: calc(50% - var(--info-shift, 0px) + var(--poster-w, 230px) / 2 + 22px);
  }

  .season-info-name {
    font-size: 1.35rem;
  }
}

@media (max-width: 600px) {
  .season-stage {
    height: calc(var(--poster-w, 170px) * 1.5 + 170px);
    margin-top: 18px;
  }

  /* No room beside the center poster: info floats below it instead */
  .season-info {
    left: 0;
    right: 0;
    top: calc(var(--poster-w, 170px) * 1.5 + 16px);
    transform: none;
    width: auto;
    padding: 0 20px;
    text-align: center;
  }

  .season-info-line {
    justify-content: center;
  }

  .season-info-name {
    font-size: 1.15rem;
  }

  .season-info-overview {
    font-size: 0.8rem;
    -webkit-line-clamp: 4;
    line-clamp: 4;
    max-height: calc(1.55em * 4);
  }

  .episodes-grid {
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    padding: 0 16px 24px;
  }

  .series-hero {
    height: 50vh;
    min-height: 300px;
  }

  .hero-content {
    padding: 20px;
  }

  .series-title {
    font-size: 1.8rem;
  }

  .series-overview {
    display: none;
  }
}

/* Episode release menu backdrop */
.episode-release-menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 999;
}
</style>
