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

    <!-- Seasons with flowing layout -->
    <section class="seasons-container">
      <div
        v-for="(season, sIndex) in series.seasons"
        :key="sIndex"
        class="season-flow"
        :data-season-index="sIndex"
        :class="{ 'season-even': sIndex % 2 === 1 }"
      >
        <!-- Season poster - tall strip on the side -->
        <div
          class="season-poster-strip"
          :class="{ 'strip-right': sIndex % 2 === 1 }"
          :style="getSeasonPosterStripStyle(sIndex)"
        >
          <div class="poster-container">
            <img
              v-if="getSeasonPoster(season)"
              :src="getSeasonPoster(season)"
              class="season-poster-img"
              :alt="season.name || `Season ${season.season_number}`"
              loading="lazy"
              decoding="async"
            />
            <div v-else class="poster-placeholder">
              <span class="poster-num">{{ season.season_number }}</span>
            </div>
            <div class="poster-overlay">
              <div class="season-label">{{ season.name || `Season ${season.season_number}` }}</div>
              <div v-if="season.overview" class="season-overview-short">
                {{ truncate(season.overview, 120) }}
              </div>
            </div>
          </div>
        </div>

        <!-- Episodes flowing grid -->
        <div class="episodes-flow" :style="getSeasonEpisodesFlowStyle(sIndex)">
          <div
            v-for="(episode, eIndex) in season.episodes"
            :key="`${sIndex}-${episode.episode_number}`"
            class="episode-tile"
            v-bind="getEpisodeNavAttrs(sIndex, eIndex)"
            @click="handlePlay(episode)"
            @focusin="handleEpisodeFocus(sIndex)"
            @keydown.enter.prevent="handleEpisodeEnter($event, episode)"
            @mouseenter="handleEpisodeHover($event, `${sIndex}-${eIndex}`, true)"
            @mouseleave="handleEpisodeHover(`${sIndex}-${eIndex}`, false)"
            @contextmenu="handleContextMenu($event, episode)"
          >
            <!-- SVG focus outline -->
            <svg class="tile-focus-outline" viewBox="0 0 200 113" preserveAspectRatio="none">
              <rect x="0" y="0" width="200" height="113" />
            </svg>
            <!-- Episode background video -->
            <div class="tile-bg">
              <video
                v-if="getEpisodeVideoSources(episode).length > 0"
                :ref="(el) => setVideoRef(el as HTMLVideoElement, `${sIndex}-${eIndex}`)"
                :autoplay="false"
                :preload="sIndex === activeSeasonIndex ? 'auto' : 'none'"
                loop
                muted
                playsinline
              >
                <source
                  v-for="source in getEpisodeVideoSources(episode)"
                  :key="source.src"
                  :src="source.src"
                  :type="source.type"
                  :codecs="source.codecs"
                />
              </video>
              <div v-else class="tile-placeholder"></div>
            </div>

            <!-- Diagonal cut overlay -->
            <div class="tile-overlay"></div>

            <!-- Episode info overlay -->
            <div class="tile-info">
              <span class="ep-number">{{ episode.episode_number }}</span>
              <div class="ep-details">
                <span class="ep-name">{{
                  episode.name || `Episode ${episode.episode_number}`
                }}</span>
                <span v-if="episode.rating" class="ep-rating"
                  >★ {{ episode.rating.toFixed(1) }}</span
                >
              </div>
            </div>

            <!-- Play indicator on hover -->
            <div class="tile-play">▶</div>
          </div>
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
                <template v-if="movie.info?.rating"> • ★ {{ movie.info.rating.toFixed(1) }}</template>
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
        :episode-name="episodeReleaseMenu.episode?.name || `Episode ${episodeReleaseMenu.episode?.episode_number}`"
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
const linkedMoviesNavRow = ref(2)
const seasonLayoutStyles = ref<
  Map<number, { posterStyle: Record<string, string>; episodesStyle: Record<string, string> }>
>(new Map())
let navLayoutFrame: number | null = null
let seasonLayoutFrame: number | null = null

const EPISODE_TILE_WIDTH_FALLBACK = 200
const EPISODE_TILE_HEIGHT_FALLBACK = 113
const EPISODE_GAP_FALLBACK = 6
const MIN_POSTER_WIDTH = 120
const MAX_POSTER_WIDTH = 280
const POSTER_ASPECT_RATIO = 3 / 2

function getSeasonPosterStripStyle(seasonIndex: number): Record<string, string> {
  return seasonLayoutStyles.value.get(seasonIndex)?.posterStyle || {}
}

function getSeasonEpisodesFlowStyle(seasonIndex: number): Record<string, string> {
  return seasonLayoutStyles.value.get(seasonIndex)?.episodesStyle || {}
}

function parseCssPx(value: string | null | undefined, fallback = 0): number {
  const parsed = parseFloat(value || "")
  return Number.isFinite(parsed) ? parsed : fallback
}

function computeSeasonLayoutStyles() {
  const root = seriesRootRef.value
  if (!root) return

  const nextStyles = new Map<
    number,
    { posterStyle: Record<string, string>; episodesStyle: Record<string, string> }
  >()

  for (let seasonIndex = 0; seasonIndex < props.series.seasons.length; seasonIndex += 1) {
    const seasonFlow = root.querySelector<HTMLElement>(`.season-flow[data-season-index="${seasonIndex}"]`)
    if (!seasonFlow) continue

    const flowStyle = window.getComputedStyle(seasonFlow)
    if (flowStyle.flexDirection.startsWith("column")) {
      continue
    }

    const episodesFlow = seasonFlow.querySelector<HTMLElement>(".episodes-flow")
    if (!episodesFlow) continue

    const availableWidth = seasonFlow.clientWidth
    if (availableWidth <= 0) continue

    const episodesCount = props.series.seasons[seasonIndex]?.episodes?.length || 0
    const sampleTile = episodesFlow.querySelector<HTMLElement>(".episode-tile")
    const tileWidth = sampleTile?.offsetWidth || EPISODE_TILE_WIDTH_FALLBACK
    const tileHeight = sampleTile?.offsetHeight || EPISODE_TILE_HEIGHT_FALLBACK
    const episodesStyle = window.getComputedStyle(episodesFlow)
    const columnGap = parseCssPx(episodesStyle.columnGap, EPISODE_GAP_FALLBACK)
    const rowGap = parseCssPx(episodesStyle.rowGap, EPISODE_GAP_FALLBACK)
    const paddingLeft = parseCssPx(episodesStyle.paddingLeft)
    const paddingRight = parseCssPx(episodesStyle.paddingRight)
    const paddingTop = parseCssPx(episodesStyle.paddingTop)
    const paddingBottom = parseCssPx(episodesStyle.paddingBottom)
    const horizontalPadding = paddingLeft + paddingRight
    const verticalPadding = paddingTop + paddingBottom

    const widthForCols = (cols: number) =>
      cols * tileWidth + Math.max(0, cols - 1) * columnGap + horizontalPadding

    const maxColsWithoutPoster = Math.max(
      1,
      Math.floor((availableWidth - horizontalPadding + columnGap) / (tileWidth + columnGap)),
    )

    let targetCols = Math.max(1, Math.min(Math.max(1, episodesCount), maxColsWithoutPoster))
    let episodesWidth = widthForCols(targetCols)
    let posterWidth = availableWidth - episodesWidth

    while (targetCols > 1 && posterWidth < MIN_POSTER_WIDTH) {
      targetCols -= 1
      episodesWidth = widthForCols(targetCols)
      posterWidth = availableWidth - episodesWidth
    }

    posterWidth = Math.max(MIN_POSTER_WIDTH, Math.min(MAX_POSTER_WIDTH, posterWidth))
    episodesWidth = Math.max(0, availableWidth - posterWidth)

    const rows = Math.max(1, Math.ceil(Math.max(1, episodesCount) / targetCols))
    const episodesHeight = rows * tileHeight + Math.max(0, rows - 1) * rowGap + verticalPadding
    const posterHeight = Math.max(140, Math.min(episodesHeight, posterWidth * POSTER_ASPECT_RATIO))

    nextStyles.set(seasonIndex, {
      posterStyle: {
        width: `${posterWidth}px`,
        height: `${posterHeight}px`,
        flexBasis: `${posterWidth}px`,
        minWidth: `${posterWidth}px`,
        alignSelf: "flex-start",
      },
      episodesStyle: {
        width: `${episodesWidth}px`,
        flexBasis: `${episodesWidth}px`,
        minWidth: `${episodesWidth}px`,
        maxWidth: `${episodesWidth}px`,
      },
    })
  }

  seasonLayoutStyles.value = nextStyles
}

function scheduleSeasonLayoutRecompute() {
  if (seasonLayoutFrame !== null) return
  seasonLayoutFrame = window.requestAnimationFrame(() => {
    seasonLayoutFrame = null
    computeSeasonLayoutStyles()
    scheduleEpisodeNavLayoutRecompute()
  })
}

function getEpisodeKey(seasonIndex: number, episodeIndex: number): string {
  return `${seasonIndex}-${episodeIndex}`
}

function getEpisodeNavAttrs(seasonIndex: number, episodeIndex: number) {
  const key = getEpisodeKey(seasonIndex, episodeIndex)
  const coords = episodeNavCoords.value.get(key) || { row: seasonIndex + 2, col: episodeIndex }
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
  let currentRow = 2

  for (let seasonIndex = 0; seasonIndex < props.series.seasons.length; seasonIndex += 1) {
    const tiles = Array.from(
      root.querySelectorAll<HTMLElement>(`.episode-tile[data-season-index="${seasonIndex}"]`),
    ).sort((a, b) => {
      const aIndex = parseInt(a.getAttribute("data-episode-index") || "0", 10)
      const bIndex = parseInt(b.getAttribute("data-episode-index") || "0", 10)
      return aIndex - bIndex
    })

    if (tiles.length === 0) {
      const fallbackCount = props.series.seasons[seasonIndex]?.episodes?.length || 0
      for (let episodeIndex = 0; episodeIndex < fallbackCount; episodeIndex += 1) {
        nextCoords.set(getEpisodeKey(seasonIndex, episodeIndex), {
          row: currentRow,
          col: episodeIndex,
        })
      }
      if (fallbackCount > 0) {
        currentRow += 1
      }
      continue
    }

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

      nextCoords.set(getEpisodeKey(seasonIndex, episodeIndex), {
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
      scheduleSeasonLayoutRecompute()
      scheduleEpisodeNavLayoutRecompute()
    })
  },
  { immediate: true },
)

// Focus on matched episode when provided
watch(
  () => props.focusEpisode,
  (ep) => {
    if (ep) {
      // Delay to ensure DOM is fully rendered after route transition
      setTimeout(() => {
        // Find the season index and episode index
        const seasonIndex =
          props.series.seasons?.findIndex((s) => s.season_number === ep.seasonNumber) ?? -1
        if (seasonIndex >= 0) {
          const episodeIndex =
            props.series.seasons?.[seasonIndex]?.episodes?.findIndex(
              (e) => e.episode_number === ep.episodeNumber,
            ) ?? -1
          if (episodeIndex >= 0) {
            const selector = `.episode-tile[data-season-index="${seasonIndex}"][data-episode-index="${episodeIndex}"]`
            const element = seriesRootRef.value?.querySelector(selector) as HTMLElement | null
            if (element) {
              element.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" })
              element.focus()
            }
          }
        }
      }, 150)
    }
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
    ? Object.values(episodeReleaseMenu.value.episode.files).find((t) => t.playable_file === filePath)
    : undefined
  const rootId = torrent?.root_id ?? props.series.root_id ?? null
  emit("openFolder", filePath, rootId)
  closeEpisodeReleaseMenu()
}

// Video refs for hover effects
const videoRefs = ref<Map<string, HTMLVideoElement>>(new Map())
const safariAutoplay = isSafariBrowser()
// No season plays until the user browses it (keyboard/gamepad focus or mouse hover)
const activeSeasonIndex = ref(-1)
const SEASON_VIDEO_STARTUP_STEP_MS = 500
const seasonStartupTimers = new Map<string, ReturnType<typeof setTimeout>>()
let seasonStartupToken = 0

// Episode tiles currently near the viewport; only these are allowed to play
const visibleEpisodeKeys = new Set<string>()
const episodeTileByKey = new Map<string, Element>()
let episodeVisibilityObserver: IntersectionObserver | null = null

const { stopped: previewPlaybackStopped } = useIdlePreviewPlayback({
  onStop: stopEpisodePreviews,
  onRestart: () => syncSeasonVideoPlayback(),
})

function parseEpisodeKey(key: string): { seasonIndex: number; episodeIndex: number } | null {
  const [seasonPart, episodePart] = key.split("-")
  const seasonIndex = parseInt(seasonPart || "", 10)
  const episodeIndex = parseInt(episodePart || "", 10)
  if (!Number.isFinite(seasonIndex) || !Number.isFinite(episodeIndex)) {
    return null
  }
  return { seasonIndex, episodeIndex }
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
          const target = entry.target as HTMLElement
          const key = `${target.dataset.seasonIndex}-${target.dataset.episodeIndex}`
          if (entry.isIntersecting) {
            if (!visibleEpisodeKeys.has(key)) {
              visibleEpisodeKeys.add(key)
              changed = true
            }
          } else if (visibleEpisodeKeys.delete(key)) {
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
  clearSeasonStartupTimers()
  clearEpisodeHoverAudioIdleTimer()
  hoveredEpisodeAudioKey = null
  for (const interval of volumeFadeIntervals.values()) {
    clearInterval(interval)
  }
  volumeFadeIntervals.clear()
  for (const video of videoRefs.value.values()) {
    pauseEpisodeVideo(video)
  }
}

function pauseEpisodeVideo(video: HTMLVideoElement) {
  video.pause()
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

  const activeSeasonVideos: Array<{ key: string; episodeIndex: number; video: HTMLVideoElement }> = []

  for (const [key, video] of videoRefs.value.entries()) {
    const parsed = parseEpisodeKey(key)
    if (!parsed) {
      pauseEpisodeVideo(video)
      continue
    }

    // Only the browsed season's near-viewport tiles may play, and only while
    // the user is active.
    const eligible =
      parsed.seasonIndex === activeSeasonIndex.value &&
      visibleEpisodeKeys.has(key) &&
      !previewPlaybackStopped.value

    if (!eligible) {
      pauseEpisodeVideo(video)
      continue
    }

    // Already playing and still eligible: leave it running so visibility
    // updates (scrolling, idle resume) don't restart it from the beginning.
    if (!video.paused && !video.ended) {
      continue
    }

    pauseEpisodeVideo(video)
    activeSeasonVideos.push({
      key,
      episodeIndex: parsed.episodeIndex,
      video,
    })
  }

  activeSeasonVideos.sort((a, b) => {
    if (priorityKey) {
      if (a.key === priorityKey) return -1
      if (b.key === priorityKey) return 1
    }
    return a.episodeIndex - b.episodeIndex
  })

  for (let i = 0; i < activeSeasonVideos.length; i += 1) {
    const { key, video } = activeSeasonVideos[i]
    const delayMs = priorityKey ? (i === 0 ? 0 : i * SEASON_VIDEO_STARTUP_STEP_MS) : i * SEASON_VIDEO_STARTUP_STEP_MS
    const timeoutId = setTimeout(() => {
      if (token !== seasonStartupToken || activeSeasonIndex.value < 0 || previewPlaybackStopped.value)
        return
      if (safariAutoplay && video.readyState >= 1) {
        video.currentTime = 0.001
      }
      video.play().catch(() => {})
      seasonStartupTimers.delete(key)
    }, delayMs)
    seasonStartupTimers.set(key, timeoutId)
  }
}

function setActiveSeason(seasonIndex: number) {
  if (seasonIndex < 0) return
  if (activeSeasonIndex.value === seasonIndex) return
  activeSeasonIndex.value = seasonIndex
  syncSeasonVideoPlayback()
}

function handleEpisodeFocus(seasonIndex: number) {
  setActiveSeason(seasonIndex)
}

function cleanupVideo(video: HTMLVideoElement | null | undefined) {
  if (!video) return
  video.pause()
  video.src = ""
  video.load()
}

// Track mounted videos and sync playback with active season.
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
    const tile = el.closest(".episode-tile")
    if (tile) {
      episodeTileByKey.set(key, tile)
      getEpisodeVisibilityObserver().observe(tile)
    }
    el.addEventListener(
      "loadeddata",
      () => {
        const parsed = parseEpisodeKey(key)
        if (!parsed || parsed.seasonIndex !== activeSeasonIndex.value) {
          pauseEpisodeVideo(el)
        }
      },
      { once: true },
    )
    syncSeasonVideoPlayback()
  } else {
    const timeoutId = seasonStartupTimers.get(key)
    if (timeoutId) {
      clearTimeout(timeoutId)
      seasonStartupTimers.delete(key)
    }
    const tile = episodeTileByKey.get(key)
    if (tile) {
      episodeVisibilityObserver?.unobserve(tile)
      episodeTileByKey.delete(key)
    }
    visibleEpisodeKeys.delete(key)
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

function scheduleEpisodeHoverAudioIdleFade(key: string, delayMs: number = AUDIO_IDLE_FADE_DELAY_MS) {
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
function handleEpisodeHover(eventOrKey: MouseEvent | string, keyOrIsEntering: string | boolean, maybeIsEntering?: boolean) {
  const event = typeof eventOrKey === "string" ? null : eventOrKey
  const key = typeof eventOrKey === "string" ? eventOrKey : (keyOrIsEntering as string)
  const isEntering = typeof eventOrKey === "string" ? Boolean(keyOrIsEntering) : Boolean(maybeIsEntering)

  if (!document.documentElement.classList.contains("mouse-active")) return

  const video = videoRefs.value.get(key)
  if (!video) return
  const parsed = parseEpisodeKey(key)
  if (!parsed) return

  if (isEntering) {
    if (event?.currentTarget instanceof HTMLElement) {
      event.currentTarget.focus({ preventScroll: true })
    }
    setActiveSeason(parsed.seasonIndex)
    syncSeasonVideoPlayback(key)
  }

  if (parsed.seasonIndex !== activeSeasonIndex.value) return

  if (isEntering) {
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

// Truncate text
function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text
  return text.slice(0, maxLength).trim() + "..."
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
  window.addEventListener("resize", scheduleEpisodeNavLayoutRecompute, { passive: true })
  window.addEventListener("resize", scheduleSeasonLayoutRecompute, { passive: true })
  window.addEventListener("mousemove", handleEpisodeHoverAudioMouseMove, { passive: true })
  nextTick(() => {
    const focusSeasonNumber = props.focusEpisode?.seasonNumber
    if (typeof focusSeasonNumber === "number") {
      const focusSeasonIndex =
        props.series.seasons.findIndex((season) => season.season_number === focusSeasonNumber) ?? -1
      if (focusSeasonIndex >= 0) {
        activeSeasonIndex.value = focusSeasonIndex
      }
    }
    syncSeasonVideoPlayback()
    scheduleSeasonLayoutRecompute()
    scheduleEpisodeNavLayoutRecompute()
  })
})

onUnmounted(() => {
  window.removeEventListener("mediahive:gamepad-action", handleGamepadAction as EventListener)
  window.removeEventListener("resize", scheduleEpisodeNavLayoutRecompute)
  window.removeEventListener("resize", scheduleSeasonLayoutRecompute)
  window.removeEventListener("mousemove", handleEpisodeHoverAudioMouseMove)
  clearEpisodeHoverAudioIdleTimer()
  clearSeasonStartupTimers()
  episodeVisibilityObserver?.disconnect()
  episodeVisibilityObserver = null
  episodeTileByKey.clear()
  visibleEpisodeKeys.clear()
  if (seasonLayoutFrame !== null) {
    window.cancelAnimationFrame(seasonLayoutFrame)
    seasonLayoutFrame = null
  }
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
  () => props.focusEpisode,
  (episode) => {
    if (!episode) return
    const seasonIndex = props.series.seasons.findIndex(
      (season) => season.season_number === episode.seasonNumber,
    )
    if (seasonIndex >= 0) {
      setActiveSeason(seasonIndex)
    }
  },
)

watch(
  () => props.series.seasons.map((season) => season.episodes.length),
  () => {
    if (activeSeasonIndex.value >= props.series.seasons.length) {
      activeSeasonIndex.value = Math.max(0, props.series.seasons.length - 1)
    }
    syncSeasonVideoPlayback()
    nextTick(() => {
      scheduleSeasonLayoutRecompute()
    })
  },
)
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

/* Season flow - alternating layout */
.season-flow {
  display: flex;
  position: relative;
  margin-bottom: 30px;
}

.season-flow.season-even {
  flex-direction: row-reverse;
}

/* Season poster strip */
.season-poster-strip {
  width: 280px;
  flex-shrink: 0;
  position: relative;
  align-self: flex-start;
}

.poster-container {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
}

.season-poster-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: top center;
}

.poster-placeholder {
  width: 100%;
  height: 100%;
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
  display: flex;
  align-items: center;
  justify-content: center;
}

.poster-num {
  font-size: 4rem;
  font-weight: 800;
  color: rgba(255, 255, 255, 0.15);
}

.poster-overlay {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 20px 15px;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.95) 0%, transparent 100%);
}

.season-label {
  font-size: 1rem;
  font-weight: 700;
  margin-bottom: 6px;
}

.season-overview-short {
  font-size: 0.75rem;
  color: rgba(255, 255, 255, 0.7);
  line-height: 1.4;
}

/* Episodes flowing grid */
.episodes-flow {
  flex: 1;
  display: grid;
  grid-template-columns: repeat(auto-fit, 200px);
  grid-auto-rows: 113px;
  gap: 6px;
  padding: 6px;
  align-content: start;
  justify-content: start;
}

/* Even seasons: episodes align to the right (same side as poster) */
.season-even .episodes-flow {
  justify-content: end;
}

/* Episode tile */
.episode-tile {
  position: relative;
  width: 200px;
  height: 113px; /* 16:9 aspect ratio */
  border-radius: 6px;
  overflow: hidden;
  cursor: pointer;
  transition: z-index 0s 0.3s;
  outline: none;
}

html.mouse-active .episode-tile:hover,
html:not(.mouse-active) .episode-tile.nav-focused {
  z-index: 20;
  transition: z-index 0s;
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

.tile-bg {
  position: absolute;
  inset: 0;
}

.tile-bg video {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.tile-placeholder {
  width: 100%;
  height: 100%;
  background: linear-gradient(135deg, #1f1f2e 0%, #141428 100%);
}

.tile-overlay {
  display: none;
}

html.mouse-active .episode-tile:hover .tile-overlay {
  opacity: 0.4;
}

/* Episode info */
.tile-info {
  position: absolute;
  inset: 0;
  padding: 8px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.ep-number {
  font-size: 1.8rem;
  font-weight: 900;
  color: white;
  text-shadow: 0 2px 10px rgba(0, 0, 0, 0.8);
  opacity: 0.9;
  line-height: 1;
}

.ep-details {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ep-name {
  font-size: 0.7rem;
  font-weight: 600;
  color: white;
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.9);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.3;
}

.ep-rating {
  font-size: 0.65rem;
  color: #f9a825;
  font-weight: 600;
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.9);
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
}

html.mouse-active .episode-tile:hover .tile-play {
  opacity: 1;
  transform: translate(-50%, -50%) scale(1);
}

/* Responsive */
@media (max-width: 900px) {
  .season-poster-strip {
    width: 120px;
  }

  .episodes-flow {
    grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
    grid-auto-rows: 70px;
  }

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

@media (max-width: 600px) {
  .season-flow {
    flex-direction: column !important;
  }

  .season-poster-strip {
    width: 100%;
    height: 200px;
  }

  .poster-container {
    position: relative;
    top: 0;
    height: 100%;
    max-height: none;
  }

  .back-btn {
    top: 10px;
    left: 10px;
    padding: 8px 14px;
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
