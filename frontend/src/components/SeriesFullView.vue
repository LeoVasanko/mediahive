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
        :class="{ 'season-even': sIndex % 2 === 1 }"
      >
        <!-- Season poster - tall strip on the side -->
        <div class="season-poster-strip" :class="{ 'strip-right': sIndex % 2 === 1 }">
          <div class="poster-container">
            <img
              v-if="getSeasonPoster(season)"
              :src="getSeasonPoster(season)"
              class="season-poster-img"
              :alt="season.name || `Season ${season.season_number}`"
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
        <div class="episodes-flow">
          <div
            v-for="(episode, eIndex) in season.episodes"
            :key="`${sIndex}-${episode.episode_number}`"
            class="episode-tile"
            tabindex="0"
            v-bind="navAttrs(sIndex + 2, eIndex)"
            @click="handlePlay(episode)"
            @keydown.enter.prevent="handleEpisodeEnter($event, episode)"
            @mouseenter="handleEpisodeHover(`${sIndex}-${eIndex}`, true)"
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
                :autoplay="safariAutoplay"
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
    </section>

    <!-- Context menu -->
    <Teleport to="body">
      <div
        v-if="contextMenu.visible"
        class="context-menu-backdrop"
        @click="closeContextMenu"
        @contextmenu.prevent="closeContextMenu"
      ></div>
      <div
        v-if="contextMenu.visible && contextMenu.episode"
        class="context-menu"
        :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }"
      >
        <div class="context-menu-header">
          {{ contextMenu.episode.name || `Episode ${contextMenu.episode.episode_number}` }}
        </div>
        <div v-if="Object.values(contextMenu.episode.files || {}).length > 0">
          <ReleaseVersionCard
            v-for="(torrent, index) in sortTorrentsByPreference(Object.values(contextMenu.episode.files || {}))"
            :key="index"
            class="context-menu-version"
            :torrent="torrent"
            variant="menu"
            compact-flags
            :title="
              torrent.playable_file
                ? 'Click to play/continue. Alt+Click, Alt+Enter, or Cmd/Ctrl+E to open folder. Right-click for actions.'
                : 'No playable file'
            "
            @activate="handleVersionActivate(torrent, $event)"
            @keydown="handleVersionShortcutKeydown($event, torrent)"
            @contextmenu="handleVersionContextMenu($event, torrent)"
          />
        </div>
        <div v-else class="context-menu-empty">No versions available</div>
      </div>
      <ReleaseActionMenu
        :visible="versionActionMenu.visible"
        :x="versionActionMenu.x"
        :y="versionActionMenu.y"
        :file-path="versionActionMenu.filePath"
        :root-name="versionActionMenu.rootName"
        :play-label="getPlayLabel(versionActionMenu.filePath)"
        @play="handlePlayVersion(versionActionMenu.filePath)"
        @open-folder="handleOpenFolder(versionActionMenu.filePath || '', versionActionMenu.rootId)"
      />
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, nextTick, watch, onMounted, onUnmounted } from "vue"
import type { Series, Season, Episode, Torrent } from "../types"
import { getCoverUrl, getVideoPreviewUrl, getVideoSourceAttributes, isSafariBrowser } from "../api"
import { navAttrs } from "../composables/useKeyboardNavigation"
import ReleaseVersionCard from "./ReleaseVersionCard.vue"
import ReleaseActionMenu from "./ReleaseActionMenu.vue"
import { sortTorrentsByPreference } from "../composables/useSettings"

const props = defineProps<{
  series: Series
  focusEpisode?: { seasonNumber: number; episodeNumber: number } | null
  hasResumePosition: (filePath: string | null) => boolean
  getRootName: (rootId: string | null | undefined) => string | null
}>()

const emit = defineEmits<{
  close: []
  play: [string]
  openFolder: [string, string | null | undefined]
}>()

const seriesRootRef = ref<HTMLElement | null>(null)

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
            // Find the episode tile element using nav attributes
            const selector = `[data-nav-row="${seasonIndex + 2}"][data-nav-col="${episodeIndex}"]`
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

// Context menu state
const contextMenu = ref<{
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

const versionActionMenu = ref<{
  visible: boolean
  x: number
  y: number
  filePath: string | null
  rootName: string | null
  rootId: string | null
}>({
  visible: false,
  x: 0,
  y: 0,
  filePath: null,
  rootName: null,
  rootId: null,
})

const releaseMenuOriginElement = ref<HTMLElement | null>(null)

// Show context menu on right-click
function handleContextMenu(event: MouseEvent, episode: Episode) {
  event.preventDefault()
  releaseMenuOriginElement.value = event.currentTarget as HTMLElement | null
  openEpisodeReleaseMenu(episode, event.clientX, event.clientY)
}

function openEpisodeReleaseMenu(episode: Episode, x: number, y: number) {
  closeVersionActionMenu()
  contextMenu.value = {
    visible: true,
    x,
    y,
    episode,
  }
  // Add Escape key listener (capturing phase to intercept before other handlers)
  nextTick(() => {
    document.addEventListener("keydown", handleContextMenuKeydown, true)
    // Focus first selectable version card.
    const firstCard = document.querySelector(
      ".context-menu .version-row.version-selectable",
    ) as HTMLElement
    if (firstCard) {
      firstCard.focus()
    }
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

// Handle Escape and arrow keys in context menu (capturing phase to intercept before global handler)
function handleContextMenuKeydown(event: KeyboardEvent) {
  if (!contextMenu.value.visible) return

  const popupFocusable = getPopupFocusableElements()

  if (event.key === "Tab") {
    if (popupFocusable.length === 0) return
    event.preventDefault()
    event.stopPropagation()

    const currentIndex = popupFocusable.findIndex((el) => el === document.activeElement)
    const delta = event.shiftKey ? -1 : 1
    const nextIndex =
      currentIndex < 0 ? 0 : (currentIndex + delta + popupFocusable.length) % popupFocusable.length
    popupFocusable[nextIndex].focus()
    return
  }

  if (
    event.key === "ArrowDown" ||
    event.key === "ArrowRight" ||
    event.key === "ArrowUp" ||
    event.key === "ArrowLeft"
  ) {
    if (popupFocusable.length === 0) return
    event.preventDefault()
    event.stopPropagation()

    const currentIndex = popupFocusable.findIndex((el) => el === document.activeElement)
    const delta = event.key === "ArrowDown" || event.key === "ArrowRight" ? 1 : -1
    const nextIndex =
      currentIndex < 0 ? 0 : (currentIndex + delta + popupFocusable.length) % popupFocusable.length
    popupFocusable[nextIndex].focus()
    return
  }

  if (event.key === "Escape") {
    event.preventDefault()
    event.stopPropagation()
    if (versionActionMenu.value.visible) {
      closeVersionActionMenu()
      return
    }
    closeContextMenu()
  }
}

function getPopupFocusableElements(): HTMLElement[] {
  const releaseItems = Array.from(
    document.querySelectorAll<HTMLElement>(".context-menu .version-row.version-selectable"),
  )
  const actionItems = versionActionMenu.value.visible
    ? Array.from(
        document.querySelectorAll<HTMLElement>(
          ".version-action-menu .version-action-item:not(:disabled)",
        ),
      )
    : []
  return [...releaseItems, ...actionItems]
}

// Close context menu
function closeContextMenu() {
  closeVersionActionMenu()
  contextMenu.value.visible = false
  document.removeEventListener("keydown", handleContextMenuKeydown, true)
  nextTick(() => {
    releaseMenuOriginElement.value?.focus()
  })
}

function closeVersionActionMenu() {
  versionActionMenu.value.visible = false
  versionActionMenu.value.filePath = null
  versionActionMenu.value.rootName = null
  versionActionMenu.value.rootId = null
}

function handleGamepadAction(event: Event) {
  const actionEvent = event as CustomEvent<{ action?: string }>
  if (actionEvent.detail?.action !== "menu") return

  const active = document.activeElement as HTMLElement | null
  if (!active || !active.classList.contains("episode-tile")) return

  const row = parseInt(active.getAttribute("data-nav-row") || "-1", 10)
  const col = parseInt(active.getAttribute("data-nav-col") || "-1", 10)
  if (row < 2 || col < 0) return

  const season = props.series.seasons?.[row - 2]
  const episode = season?.episodes?.[col]
  if (!episode) return

  actionEvent.preventDefault()
  openEpisodeReleaseMenuFromElement(episode, active)
}

// Play specific version
function handlePlayVersion(filePath: string | null) {
  if (filePath) {
    emit("play", filePath)
  }
  closeVersionActionMenu()
  closeContextMenu()
}

function getPlayLabel(filePath: string | null): string {
  return props.hasResumePosition(filePath) ? "Continue" : "Play"
}

// Open folder for a version
function handleOpenFolder(folderPath: string, rootId?: string | null) {
  if (!folderPath) return
  emit("openFolder", folderPath, rootId)
  closeVersionActionMenu()
  closeContextMenu()
}

function handleVersionActivate(torrent: Torrent, event: MouseEvent | KeyboardEvent) {
  if (!torrent.playable_file) return
  const rootId = torrent.root_id || props.series.root_id
  if (event.altKey) {
    handleOpenFolder(torrent.playable_file, rootId)
    return
  }
  handlePlayVersion(torrent.playable_file)
}

function handleVersionShortcutKeydown(event: KeyboardEvent, torrent: Torrent) {
  if (!torrent.playable_file) return
  const rootId = torrent.root_id || props.series.root_id
  const key = event.key.toLowerCase()
  if (key === "e" && (event.metaKey || event.ctrlKey)) {
    event.preventDefault()
    event.stopPropagation()
    handleOpenFolder(torrent.playable_file, rootId)
  }
}

function handleVersionContextMenu(event: MouseEvent, torrent: Torrent) {
  event.preventDefault()
  event.stopPropagation()
  const rootId = torrent.root_id || props.series.root_id
  versionActionMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    filePath: torrent.playable_file || null,
    rootName: props.getRootName(rootId) || null,
    rootId,
  }
  nextTick(() => {
    const firstAction = document.querySelector(
      ".version-action-menu .version-action-item:not(:disabled)",
    ) as HTMLElement | null
    firstAction?.focus()
  })
}

// Video refs for hover effects
const videoRefs = ref<Map<string, HTMLVideoElement>>(new Map())
let videoIndex = 0
const safariAutoplay = isSafariBrowser()

function cleanupVideo(video: HTMLVideoElement | null | undefined) {
  if (!video) return
  video.pause()
  video.src = ""
  video.load()
}

// Set video ref with staggered playback
function setVideoRef(el: HTMLVideoElement | null, key: string) {
  if (el) {
    videoRefs.value.set(key, el)
    // Staggered start times with 0.2 second offset
    const index = videoIndex++
    setTimeout(
      () => {
        if (safariAutoplay && el.readyState >= 1) {
          el.currentTime = 0.001 + (index % 6) * 0.03
        }
        el.play().catch(() => {}) // Ignore autoplay policy errors
      },
      safariAutoplay ? 0 : index * 200,
    )
  } else {
    const old = videoRefs.value.get(key)
    if (old) {
      cleanupVideo(old)
    }
    videoRefs.value.delete(key)
  }
}

// Volume fade animation tracking
const volumeFadeIntervals = new Map<string, ReturnType<typeof setInterval>>()

// Handle hover-based audio fade in/out for episode videos
function handleEpisodeHover(key: string, isEntering: boolean) {
  const video = videoRefs.value.get(key)
  if (!video) return

  // Clear any existing fade for this video
  const existingInterval = volumeFadeIntervals.get(key)
  if (existingInterval) {
    clearInterval(existingInterval)
    volumeFadeIntervals.delete(key)
  }

  if (isEntering) {
    // Mute all other videos immediately
    videoRefs.value.forEach((v, k) => {
      if (k !== key) {
        v.volume = 0
        v.muted = true
      }
    })

    // Fade in this video's audio
    video.muted = false
    const fadeIn = setInterval(() => {
      if (video.volume < 0.95) {
        video.volume = Math.min(1, video.volume + 0.1)
      } else {
        video.volume = 1
        clearInterval(fadeIn)
        volumeFadeIntervals.delete(key)
      }
    }, 30)
    volumeFadeIntervals.set(key, fadeIn)
  } else {
    // Fade out this video's audio
    const fadeOut = setInterval(() => {
      if (video.volume > 0.05) {
        video.volume = Math.max(0, video.volume - 0.1)
      } else {
        video.volume = 0
        video.muted = true
        clearInterval(fadeOut)
        volumeFadeIntervals.delete(key)
      }
    }, 30)
    volumeFadeIntervals.set(key, fadeOut)
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
})

onUnmounted(() => {
  window.removeEventListener("mediahive:gamepad-action", handleGamepadAction as EventListener)
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
</script>

<style scoped>
.series-fullscreen {
  min-height: calc(100vh - 60px); /* Account for header height */
  background: #0a0a0a;
  overflow-x: hidden;
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
  align-self: stretch;
}

.poster-container {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 200px;
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

/* Context menu styles */
.context-menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 999;
}

.context-menu {
  position: fixed;
  z-index: 1000;
  background: rgba(20, 20, 30, 0.98);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 8px;
  min-width: 280px;
  max-width: 400px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6);
  overflow: visible;
  padding: 8px;
}

.context-menu-header {
  padding: 10px 12px;
  font-weight: 600;
  font-size: 0.9rem;
  background: rgba(255, 255, 255, 0.05);
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  margin-bottom: 8px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.context-menu-version {
  margin-bottom: 6px;
}

.context-menu-version:last-child {
  margin-bottom: 0;
}

.context-menu-empty {
  padding: 12px;
  text-align: center;
  color: rgba(255, 255, 255, 0.5);
  font-size: 0.85rem;
}
</style>
