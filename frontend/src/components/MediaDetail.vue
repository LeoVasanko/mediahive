<template>
  <!-- Full screen view for series -->
  <SeriesFullView
    v-if="item.type === 'series'"
    :series="item.data as Series"
    :all-movies="allMovies"
    :focus-episode="focusEpisode"
    :has-resume-position="hasResumePosition"
    :get-root-name="getRootName"
    @close="$emit('close')"
    @play="handlePlay"
    @openFolder="handleOpenFolder"
    @select-movie="handleSelectMovie"
  />

  <!-- Full page view for movies -->
  <div v-else class="movie-page">
    <div class="movie-page-content">
      <!-- Diagonal collage header -->
      <div class="collage-header">
        <!-- Background collage of showreel videos -->
        <div class="collage-grid">
          <div
            v-for="slot in collageSlots"
            :key="slot.index"
            class="collage-item"
            @mouseenter="handleVideoHover(slot.index, true)"
            @mouseleave="handleVideoHover(slot.index, false)"
          >
            <div class="collage-fallback-tile" :class="`collage-fallback-${slot.index + 1}`"></div>
            <video
              v-if="slot.sourcePaths.length > 0"
              :ref="(el) => setVideoRef(el as HTMLVideoElement, slot.index)"
              :class="{ 'is-ready': isVideoReady(slot.index) }"
              :autoplay="safariAutoplay"
              loop
              muted
              playsinline
              @loadeddata="handleVideoLoaded(slot.index)"
              @error="handleVideoError(slot.index)"
            >
              <source
                v-for="sourcePath in slot.sourcePaths"
                :key="sourcePath"
                :src="getShowreelUrl(sourcePath)"
                :type="getShowreelSourceAttributes(sourcePath).type"
                :codecs="getShowreelSourceAttributes(sourcePath).codecs"
              />
            </video>
          </div>
        </div>

        <!-- Diagonal overlay -->
        <div class="collage-overlay"></div>

        <!-- Title and meta on top -->
        <div class="collage-content">
          <h1 class="modal-title">{{ item.title }}</h1>
          <p v-if="movieTagline" class="header-tagline">{{ movieTagline }}</p>
          <div class="modal-meta">
            <span v-if="rating" class="meta-rating" :class="ratingClass"
              >★ {{ rating.toFixed(1) }}</span
            >
            <span v-if="item.year" class="meta-year">{{ item.year }}</span>
            <span v-if="movieRuntime" class="meta-runtime">{{ formatRuntime(movieRuntime) }}</span>
          </div>
          <!-- Genre tags in header -->
          <div v-if="movieGenres && movieGenres.length > 0" class="header-genres">
            <span v-for="genre in movieGenres" :key="genre" class="genre-tag">{{ genre }}</span>
          </div>
        </div>
      </div>

      <div class="modal-body" :style="backdropStyle">
        <!-- Backdrop overlay for contrast -->
        <div class="backdrop-overlay"></div>

        <div class="modal-body-inner">
          <div class="content-layout">
            <!-- Left sidebar - Synopsis -->
            <div v-if="synopsisPosterUrl" class="content-sidebar sidebar-left">
              <div class="synopsis-box">
                <img
                  :src="synopsisPosterUrl"
                  :alt="`${item.title} poster`"
                  class="synopsis-poster"
                />
              </div>
              <div v-if="movieVersions.length > 0" class="versions-list versions-list-sidebar">
                <ReleaseVersionCard
                  v-for="(version, index) in movieVersions"
                  :key="index"
                  :torrent="version"
                  :best="index === 0"
                  :selectable="!!version.playable_file"
                  :disabled="!version.playable_file"
                  data-nav-release-item="true"
                  v-bind="navAttrs(2 + index, 0, 0)"
                  @activate="handleVersionActivate(version, $event)"
                  @keydown="handleVersionShortcutKeydown($event, version)"
                  @contextmenu="handleVersionContextMenu($event, version)"
                  :title="
                    version.playable_file
                      ? 'Click to play/continue. Alt+Click, Alt+Enter, or Cmd/Ctrl+E to open folder. Right-click for actions.'
                      : 'No playable file'
                  "
                />
              </div>
            </div>

            <div
              v-if="limitedMovieCast.length > 0"
              class="cast-list"
              data-sync-scroll-row="true"
              data-sync-scroll-group="cast"
              data-nav-cast-row="true"
            >
              <a
                v-for="(castMember, castIndex) in limitedMovieCast"
                :key="`${castMember.name}-${castMember.character || ''}`"
                class="cast-card media-card"
                data-nav-cast-item="true"
                v-bind="navAttrs(castNavRow, castIndex)"
                :href="`/search/${encodeURIComponent(castMember.name)}`"
                :title="`Search for ${castMember.name}`"
                @click.prevent="handleCastSelect(castMember.name)"
              >
                <img
                  v-if="castMember.profile_path && !castMember.profile_path.startsWith('/')"
                  :src="getCastProfileUrl(castMember.profile_path)"
                  :alt="castMember.name"
                  class="cast-photo"
                />
                <img
                  v-else
                  :src="getCastPlaceholderUrl(castMember.gender)"
                  :alt="`${castMember.name} placeholder portrait`"
                  class="cast-photo cast-photo-fallback"
                />
                <div class="cast-copy">
                  <span class="cast-name">{{ castMember.name }}</span>
                  <span v-if="castMember.character" class="cast-character">{{
                    castMember.character
                  }}</span>
                </div>
              </a>
            </div>

            <!-- Main content -->
            <div class="content-main"></div>

            <!-- Right sidebar - Metadata -->
            <div v-if="item.type === 'movies'" class="content-sidebar sidebar-right">
              <div class="movie-metadata">
                <div v-if="overview" class="meta-row">
                  <span class="meta-value meta-synopsis">{{ overview }}</span>
                </div>
                <div v-if="movieDirector || movieStatus || movieReleaseDate" class="meta-grid">
                  <template v-if="movieDirector">
                    <span class="meta-label">Directed by</span>
                    <span class="meta-value">{{ movieDirector }}</span>
                  </template>
                  <template v-if="movieStatus || movieReleaseDate">
                    <span v-if="movieStatus" class="meta-label">{{ movieStatus }}</span>
                    <span v-else class="meta-label"></span>
                    <span v-if="movieReleaseDate" class="meta-value">{{ movieReleaseDate }}</span>
                    <span v-else class="meta-value"></span>
                  </template>
                </div>
                <div v-if="movieKeywords && movieKeywords.length > 0" class="meta-keywords-section">
                  <span
                    v-for="(keyword, keywordIndex) in movieKeywords"
                    :key="`${keyword}-${keywordIndex}`"
                    class="meta-keyword"
                    >{{ formatKeywordLabel(keyword) }}</span
                  >
                </div>
              </div>
            </div>
          </div>

          <section v-if="item.type === 'movies' && similarMovies.length > 0" class="similar-movies-section">
            <h2 class="similar-movies-title">Similar In Library</h2>
            <div class="similar-movies-grid" data-sync-scroll-row="true" data-sync-scroll-group="similar">
              <button
                v-for="(movie, similarIndex) in similarMovies"
                :key="movie.tmdbId"
                type="button"
                class="similar-movie-card cast-card media-card"
                v-bind="navAttrs(similarNavRow, similarIndex)"
                @click="handleSelectMovie(movie.localId)"
              >
                <img
                  v-if="movie.coverPath"
                  :src="getCoverUrl(movie.coverPath, movie.rootId)"
                  :alt="movie.title || 'Movie'"
                  class="similar-movie-poster cast-photo"
                />
                <div v-else class="similar-movie-poster cast-photo similar-movie-poster-fallback"></div>
                <div class="similar-movie-meta cast-copy">
                  <span class="similar-movie-name cast-name">{{ movie.title }}</span>
                  <span class="similar-movie-sub cast-character">
                    {{ movie.year || "Unknown Year" }}
                  </span>
                </div>
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>

    <Teleport to="body">
      <div
        v-if="versionActionMenu.visible"
        class="movie-menu-backdrop"
        @click="closeVersionActionMenu"
        @contextmenu.prevent="closeVersionActionMenu"
      ></div>
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
import { computed, ref, watch, onMounted, onUnmounted, nextTick } from "vue"
import type { CastMember, MediaItem, Movie, MovieUi, Series, Torrent } from "../types"
import {
  getCoverUrl,
  getVideoPreviewUrl,
  getVideoSourceAttributes,
  isSafariBrowser,
  type VideoSourceAttributes,
} from "../api"
import castPlaceholderFemaleUrl from "../assets/cast-placeholder-female.svg"
import castPlaceholderMaleUrl from "../assets/cast-placeholder-male.svg"
import SeriesFullView from "./SeriesFullView.vue"
import { sortTorrentsByPreference } from "../composables/useSettings"
import ReleaseVersionCard from "./ReleaseVersionCard.vue"
import ReleaseActionMenu from "./ReleaseActionMenu.vue"
import {
  navAttrs,
  registerOutOfBoundsNavigationHandler,
  FOCUSABLE_ATTR,
} from "../composables/useKeyboardNavigation"

const props = defineProps<{
  item: MediaItem
  allMovies: MovieUi[]
  focusEpisode?: { seasonNumber: number; episodeNumber: number } | null
  hasResumePosition: (filePath: string | null) => boolean
  getRootName: (rootId: string | null | undefined) => string | null
}>()
const emit = defineEmits<{
  close: []
  play: [string]
  openFolder: [string, string | null | undefined]
  searchActor: [string]
  selectMovie: [string]
}>()

// Track expanded episode for showing multiple releases

const videoRefs = ref<(HTMLVideoElement | null)[]>([])
const videoStates = ref<string[]>([])
const COLLAGE_SLOT_COUNT = 5
const safariAutoplay = isSafariBrowser()
const COLLAGE_START_OFFSETS_SECONDS = [0, 8, 6, 4, 2]
const DESKTOP_NAV_SHORTCUT_MIN_WIDTH = 900

let disposeOutOfBoundsHandler: (() => void) | null = null
let lastReleaseShortcutRow: number | null = null

function getDesktopCastFirstItem(): HTMLElement | null {
  const castRow = document.querySelector<HTMLElement>('[data-nav-cast-row="true"]')
  if (!castRow) return null
  return castRow.querySelector<HTMLElement>(`[data-nav-cast-item="true"][${FOCUSABLE_ATTR}="true"]`)
}

function getReleaseAtRow(row: number): HTMLElement | null {
  return document.querySelector<HTMLElement>(
    `[data-nav-release-item="true"][data-nav-row="${row}"][data-nav-col="0"][${FOCUSABLE_ATTR}="true"]`,
  )
}

function getLastReleaseRowBefore(castRow: number): number | null {
  const releases = Array.from(document.querySelectorAll<HTMLElement>('[data-nav-release-item="true"]'))
  let best: number | null = null
  for (const release of releases) {
    const row = parseInt(release.getAttribute("data-nav-row") || "", 10)
    if (!Number.isFinite(row) || row >= castRow) continue
    if (best === null || row > best) best = row
  }
  return best
}

function registerMovieOutOfBoundsShortcut() {
  disposeOutOfBoundsHandler?.()
  disposeOutOfBoundsHandler = registerOutOfBoundsNavigationHandler((context) => {
    if (window.innerWidth <= DESKTOP_NAV_SHORTCUT_MIN_WIDTH) return null
    if (props.item.type !== "movies") return null

    const { current, direction, currentRow, currentCol } = context

    if (direction === "right" && current.hasAttribute("data-nav-release-item")) {
      const firstCast = getDesktopCastFirstItem()
      if (firstCast) {
        lastReleaseShortcutRow = currentRow
        return firstCast
      }
      return null
    }

    if (
      direction === "left" &&
      current.hasAttribute("data-nav-cast-item") &&
      currentCol === 0
    ) {
      const targetRow = lastReleaseShortcutRow ?? getLastReleaseRowBefore(currentRow)
      if (targetRow === null) return null
      return getReleaseAtRow(targetRow)
    }

    return null
  })
}

function cleanupVideo(video: HTMLVideoElement | null | undefined) {
  if (!video) return
  video.pause()
  video.src = ""
  video.load()
}

function setVideoRef(el: HTMLVideoElement | null, index: number) {
  const old = videoRefs.value[index]
  if (old && old !== el) {
    cleanupVideo(old)
  }
  videoRefs.value[index] = el
}

function handleVideoLoaded(index: number) {
  videoStates.value[index] = "ready"
}

function handleVideoError(index: number) {
  videoStates.value[index] = "error"
}

function isVideoReady(index: number): boolean {
  return videoStates.value[index] === "ready"
}

// Start staggered video playback
function startStaggeredPlayback() {
  const videos = videoRefs.value.filter((v) => v !== null) as HTMLVideoElement[]
  if (videos.length === 0) return

  if (safariAutoplay) {
    videos.forEach((video, index) => {
      const offset = COLLAGE_START_OFFSETS_SECONDS[index] ?? 0
      const startVideo = () => {
        video.currentTime = offset
        video.play().catch(() => {})
      }

      if (video.readyState >= 1) {
        startVideo()
      } else {
        video.addEventListener("loadedmetadata", startVideo, { once: true })
      }
    })
    return
  }

  // Start first video immediately
  // Non-Safari keeps legacy behavior: start without explicit seek offset.
  videos[0].play().catch(() => {})

  // Set up staggered start for remaining videos
  for (let i = 1; i < videos.length; i++) {
    setTimeout(() => {
      const video = videos[i]
      if (!video) return
      video.play().catch(() => {})
    }, i * 2000)
  }
}

// Volume fade animation tracking
const volumeFadeIntervals = new Map<number, ReturnType<typeof setInterval>>()

// Handle hover-based audio fade in/out
function handleVideoHover(index: number, isEntering: boolean) {
  const video = videoRefs.value[index]
  if (!video) return

  // Clear any existing fade for this video
  const existingInterval = volumeFadeIntervals.get(index)
  if (existingInterval) {
    clearInterval(existingInterval)
    volumeFadeIntervals.delete(index)
  }

  if (isEntering) {
    // Mute all other videos immediately
    document.querySelectorAll("video").forEach((v) => {
      if (v !== video) {
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
        volumeFadeIntervals.delete(index)
      }
    }, 30)
    volumeFadeIntervals.set(index, fadeIn)
  } else {
    // Fade out this video's audio
    const fadeOut = setInterval(() => {
      if (video.volume > 0.05) {
        video.volume = Math.max(0, video.volume - 0.1)
      } else {
        video.volume = 0
        video.muted = true
        clearInterval(fadeOut)
        volumeFadeIntervals.delete(index)
      }
    }, 30)
    volumeFadeIntervals.set(index, fadeOut)
  }
}

onMounted(() => {
  // Wait for videos to be ready, then start staggered playback
  setTimeout(() => {
    startStaggeredPlayback()
  }, 100)

  registerMovieOutOfBoundsShortcut()
})

const showreelSourceSets = computed((): string[][] | null => {
  if (props.item.type === "movies") {
    const movie = props.item.data as Movie
    if (movie.showreel_source_sets && movie.showreel_source_sets.length > 0) {
      return movie.showreel_source_sets
    }
    return movie.showreel_images?.map((path) => [path]) ?? null
  } else {
    const series = props.item.data as Series
    const sourceSets: string[][] = []
    for (const season of series.seasons || []) {
      for (const episode of season.episodes || []) {
        if (episode.reel_sources && episode.reel_sources.length > 0) {
          sourceSets.push(episode.reel_sources)
        } else if (episode.reel_image) {
          sourceSets.push([episode.reel_image])
        }
      }
    }
    return sourceSets.length > 0 ? sourceSets : null
  }
})

const collageSourceSets = computed((): string[][] => {
  if (!showreelSourceSets.value || showreelSourceSets.value.length === 0) return []
  return showreelSourceSets.value.slice(0, 5)
})

const collageSlots = computed(() => {
  return Array.from({ length: COLLAGE_SLOT_COUNT }, (_, index) => ({
    index,
    sourcePaths: collageSourceSets.value[index] ?? [],
  }))
})

watch(
  collageSlots,
  async (slots, oldSlots) => {
    // Pause and unload videos that are no longer referenced before reassigning refs
    if (oldSlots) {
      for (let i = 0; i < oldSlots.length; i++) {
        const oldPaths = oldSlots[i]?.sourcePaths ?? []
        const newPaths = slots[i]?.sourcePaths ?? []
        const changed =
          oldPaths.length !== newPaths.length || oldPaths.some((p, idx) => p !== newPaths[idx])
        if (changed) {
          cleanupVideo(videoRefs.value[i])
        }
      }
    }
    videoRefs.value = Array.from(
      { length: COLLAGE_SLOT_COUNT },
      (_, index) => videoRefs.value[index] ?? null,
    )
    videoStates.value = slots.map((slot) => (slot.sourcePaths.length > 0 ? "loading" : "missing"))
    await nextTick()
    setTimeout(() => {
      startStaggeredPlayback()
    }, 100)
  },
  { immediate: true },
)

function getShowreelUrl(path: string): string {
  return getVideoPreviewUrl(getCoverUrl(path, props.item.root_id))
}

function getShowreelSourceAttributes(path: string): VideoSourceAttributes {
  return getVideoSourceAttributes(path)
}
// Movie versions
const movieVersions = computed((): Torrent[] => {
  if (props.item.type !== "movies") return []
  const movie = props.item.data as Movie
  return sortTorrentsByPreference(Object.values(movie.files || {}))
})

// Page backdrop background
const backdropStyle = computed(() => {
  if (props.item.type !== "movies") return {}
  const movie = props.item.data as Movie
  const imagePath = movie.backdrop_path
  const imageUrl = getCoverUrl(imagePath, props.item.root_id)
  if (imageUrl) {
    return { backgroundImage: `url("${imageUrl}")` }
  }
  return {}
})

const synopsisPosterUrl = computed(() => {
  if (props.item.type !== "movies") return null
  return getCoverUrl(props.item.cover_path, props.item.root_id)
})

const movieGenres = computed(() => {
  if (props.item.type !== "movies") return null
  return (props.item.data as Movie).info?.genres
})

const movieTagline = computed(() => {
  if (props.item.type !== "movies") return null
  return (props.item.data as Movie).info?.tagline
})

const movieDirector = computed(() => {
  if (props.item.type !== "movies") return null
  return (props.item.data as Movie).info?.director
})

const movieCast = computed(() => {
  if (props.item.type !== "movies") return null
  return (props.item.data as Movie).info?.cast as CastMember[] | null
})

const limitedMovieCast = computed(() => {
  if (!movieCast.value) return []
  return movieCast.value
})

const movieRuntime = computed(() => {
  if (props.item.type !== "movies") return null
  return (props.item.data as Movie).info?.runtime
})

const movieReleaseDate = computed(() => {
  if (props.item.type !== "movies") return null
  const releaseDate = (props.item.data as Movie).info?.release_date
  if (!releaseDate) return null

  const parsedDate = new Date(releaseDate)
  if (Number.isNaN(parsedDate.getTime())) return releaseDate

  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(parsedDate)
})

const movieStatus = computed(() => {
  if (props.item.type !== "movies") return null
  return (props.item.data as Movie).info?.status
})

const movieKeywords = computed(() => {
  if (props.item.type !== "movies") return null
  return (props.item.data as Movie).info?.keywords
})

const viewportWidth = ref(typeof window !== "undefined" ? window.innerWidth : 1920)

const similarNavRow = computed(() => 3 + movieVersions.value.length)

const castNavRow = computed(() => {
  const hasDesktopSimilarShortcut =
    viewportWidth.value > DESKTOP_NAV_SHORTCUT_MIN_WIDTH && similarMovies.value.length > 0
  // Desktop with similar row: keep visual cast placement but move it below similar in nav rows.
  // Narrow layout (or no similar): preserve existing cast row directly after releases.
  return hasDesktopSimilarShortcut ? 4 + movieVersions.value.length : 2 + movieVersions.value.length
})

const similarMovies = computed((): Array<{
  tmdbId: number
  title: string
  localId: string
  coverPath: string | null
  rootId: string | null
  year: string | null
}> => {
  if (props.item.type !== "movies") return []

  const movie = props.item.data as Movie
  const similar = movie.info?.similar || []
  if (similar.length === 0) return []

  const byTmdbId = new Map<number, MovieUi>()
  for (const libraryMovie of props.allMovies || []) {
    const tmdbId = libraryMovie.info?.tmdb_id
    if (libraryMovie.id === props.item.id) continue

    if (typeof tmdbId === "number" && !byTmdbId.has(tmdbId)) {
      byTmdbId.set(tmdbId, libraryMovie)
    }
  }

  const matches: Array<{
    tmdbId: number
    title: string
    localId: string
    coverPath: string | null
    rootId: string | null
    year: string | null
  }> = []

  const seenTmdbIds = new Set<number>()
  for (const similarEntry of similar) {
    if (seenTmdbIds.has(similarEntry.id)) continue
    seenTmdbIds.add(similarEntry.id)

    const matched = byTmdbId.get(similarEntry.id)
    if (!matched) continue

    const title = matched.title || matched.info?.title || similarEntry.title
    if (!title) continue

    matches.push({
      tmdbId: similarEntry.id,
      title,
      localId: matched.id,
      coverPath: matched.cover_path || null,
      rootId: matched.root_id || null,
      year: matched.year ? String(matched.year) : matched.info?.release_date?.slice(0, 4) || null,
    })
  }

  return matches.slice(0, 24)
})

function formatKeywordLabel(keyword: string): string {
  // Keep multi-word keywords together while visually narrowing internal spacing.
  return keyword.trim().replace(/\s+/g, "\u202F")
}

function getCastPlaceholderUrl(gender?: CastMember["gender"]): string {
  return gender === "female" ? castPlaceholderFemaleUrl : castPlaceholderMaleUrl
}

function getCastProfileUrl(profilePath: string | null): string {
  if (!profilePath) return ""
  if (profilePath.includes("/")) {
    return getCoverUrl(profilePath, props.item.root_id)
  }
  const castPath = `.mediahive/people/${profilePath}`
  return getCoverUrl(castPath, props.item.root_id)
}

function formatRuntime(minutes: number): string {
  const hours = Math.floor(minutes / 60)
  const mins = minutes % 60
  if (hours === 0) return `${mins}m`
  return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`
}

const rating = computed(() => {
  if (props.item.type === "movies") {
    return (props.item.data as Movie).info?.rating
  }
  return (props.item.data as Series).info?.rating
})

const overview = computed(() => {
  if (props.item.type === "movies") {
    return (props.item.data as Movie).info?.overview
  }
  return (props.item.data as Series).info?.overview
})

const ratingClass = computed(() => {
  if (!rating.value) return ""
  if (rating.value >= 7.5) return "rating-high"
  if (rating.value >= 6) return "rating-medium"
  return "rating-low"
})

const seasons = computed(() => {
  if (props.item.type !== "series") return []
  const series = props.item.data as Series
  return series.seasons || []
})

const selectedSeasonIndex = ref<number>(0)

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

function closeVersionActionMenu() {
  versionActionMenu.value.visible = false
  versionActionMenu.value.filePath = null
  versionActionMenu.value.rootName = null
  versionActionMenu.value.rootId = null
}

function getPlayLabel(filePath: string | null): string {
  return props.hasResumePosition(filePath) ? "Continue" : "Play"
}

function handlePlayVersion(filePath: string | null) {
  if (filePath) {
    emit("play", filePath)
  }
  closeVersionActionMenu()
}

function handleVersionContextMenu(event: MouseEvent, version: Torrent) {
  event.preventDefault()
  event.stopPropagation()
  const rootId = version.root_id || ((props.item.data as MovieUi).root_id ?? props.item.root_id)
  versionActionMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    filePath: version.playable_file || null,
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

function handleVersionShortcutKeydown(event: KeyboardEvent, version: Torrent) {
  if (!version.playable_file) return
  const rootId = version.root_id || ((props.item.data as MovieUi).root_id ?? props.item.root_id)
  const key = event.key.toLowerCase()
  if (key === "e" && (event.metaKey || event.ctrlKey)) {
    event.preventDefault()
    event.stopPropagation()
    handleOpenFolder(version.playable_file, rootId)
    return
  }
  if (key === "enter" && event.altKey) {
    event.preventDefault()
    event.stopPropagation()
    handleOpenFolder(version.playable_file, rootId)
  }
}

function handleMovieMenuKeydown(event: KeyboardEvent) {
  if (!versionActionMenu.value.visible) return
  if (event.key === "Escape") {
    event.preventDefault()
    event.stopPropagation()
    closeVersionActionMenu()
  }
}

// Select first season by default
watch(
  seasons,
  (s) => {
    if (s.length > 0 && selectedSeasonIndex.value >= s.length) {
      selectedSeasonIndex.value = 0
    }
  },
  { immediate: true },
)

function handlePlay(filePath: string | null) {
  if (filePath) {
    emit("play", filePath)
  }
}

function handleVersionActivate(version: Torrent, event: MouseEvent | KeyboardEvent) {
  if (!version.playable_file) return
  const rootId = version.root_id || ((props.item.data as MovieUi).root_id ?? props.item.root_id)
  if (event.altKey) {
    handleOpenFolder(version.playable_file, rootId)
    return
  }
  handlePlay(version.playable_file)
}

function handleOpenFolder(folderPath: string, rootId?: string | null) {
  closeVersionActionMenu()
  emit("openFolder", folderPath, rootId)
}

function handleCastSelect(castName: string) {
  const name = castName.trim()
  if (!name) return
  emit("searchActor", name)
}

function handleSelectMovie(movieId: string) {
  emit("selectMovie", movieId)
}

function handleResize() {
  viewportWidth.value = window.innerWidth
}

onMounted(() => {
  document.addEventListener("keydown", handleMovieMenuKeydown, true)
  window.addEventListener("resize", handleResize)
})

onUnmounted(() => {
  document.removeEventListener("keydown", handleMovieMenuKeydown, true)
  window.removeEventListener("resize", handleResize)
  disposeOutOfBoundsHandler?.()
  disposeOutOfBoundsHandler = null
  lastReleaseShortcutRow = null
  // Clear all volume fade intervals
  for (const interval of volumeFadeIntervals.values()) {
    clearInterval(interval)
  }
  volumeFadeIntervals.clear()
  // Pause and unload all video elements
  for (const video of videoRefs.value) {
    cleanupVideo(video)
  }
  videoRefs.value = []
})
</script>

<style scoped>
/* Movie page layout (inline within main content) */
.movie-page {
  background-color: var(--bg-primary);
  --movie-nav-bar-width: calc(40vw + 0.8rem);
}

.similar-movies-section {
  margin-top: 20px;
}

.similar-movies-title {
  margin: 0 0 12px;
  font-size: 1.15rem;
  font-weight: 700;
}

.similar-movies-grid {
  --sync-row-tail: 0px;
  --sync-row-right-deadzone: 32px;
  display: flex;
  flex-wrap: nowrap;
  gap: 6px;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.similar-movies-grid::-webkit-scrollbar {
  height: 0;
  display: none;
}

.similar-movie-card {
  appearance: none;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.similar-movie-poster {
  width: 100%;
  height: 100%;
}

.similar-movie-poster-fallback {
  background: linear-gradient(135deg, #282d3a, #171b24);
}

.similar-movie-meta {
  inset: auto 0 0 0;
}

.similar-movie-name {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.similar-movie-sub {
  white-space: nowrap;
}

.movie-menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 999;
}

.movie-page-content {
  position: relative;
}

.movie-page-content::before {
  content: "";
  position: absolute;
  top: 300px;
  /* Align clipped edge after reel 2/5; breakpoints move to 3/5 and 4/5 with proportional slant compensation. */
  left: calc(-50vw + 50% - 1.6rem);
  width: var(--movie-nav-bar-width);
  max-width: calc(100vw - 24px);
  height: var(--header-height);
  background: linear-gradient(to bottom, rgba(5, 7, 10, 0.72) 0%, rgba(5, 7, 10, 0.5) 100%);
  -webkit-backdrop-filter: blur(10px) saturate(115%);
  backdrop-filter: blur(10px) saturate(115%);
  /* Match reel slant angle: 2rem horizontal shift over 300px reel height. */
  -webkit-clip-path: polygon(
    0 0,
    100% 0,
    calc(100% - (var(--header-height) * 0.1067)) 100%,
    0 100%
  );
  clip-path: polygon(0 0, 100% 0, calc(100% - (var(--header-height) * 0.1067)) 100%, 0 100%);
  pointer-events: none;
  z-index: 30;
}

@media (max-width: 1280px) {
  .movie-page {
    --movie-nav-bar-width: calc(60vw + 0.4rem);
  }
}

@media (max-width: 900px) {
  .movie-page {
    --movie-nav-bar-width: 80vw;
  }
}

/* Modal body with backdrop - full viewport width, fits backdrop height */
.modal-body {
  position: relative;
  padding: 24px 0 40px;
  /* Add top padding to make room for header bar overlay */
  padding-top: calc(var(--header-height) + 40px);
  background-size: cover;
  background-position: center top;
  min-height: max(500px, 56.25vw); /* 16:9 aspect ratio as minimum */
  /* Expand to full viewport width */
  width: 100vw;
  margin-left: calc(-50vw + 50%);
  box-sizing: border-box;
}

.backdrop-overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

/* Ensure content is above backdrop overlay */
.modal-body-inner {
  position: relative;
  z-index: 1;
  width: 100%;
  padding: 0 32px;
  box-sizing: border-box;
}

/* Three-column layout */
.content-layout {
  display: grid;
  grid-template-columns: minmax(260px, 360px) minmax(0, 1fr) minmax(240px, 320px);
  grid-template-areas:
    "left cast cast"
    "left main right";
  gap: 32px;
  align-items: start;
  position: relative;
}

.content-main {
  grid-area: main;
  min-width: 0;
  position: relative;
  z-index: 2;
}

.content-sidebar {
  min-width: 0;
  position: relative;
  z-index: 2;
}

.sidebar-left {
  grid-area: left;
  align-self: start;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sidebar-right {
  grid-area: right;
}

/* Synopsis box */
.synopsis-box {
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-radius: 12px;
  overflow: hidden;
}

.synopsis-poster {
  display: block;
  width: 100%;
  height: auto;
  object-fit: contain;
}

.synopsis-text {
  color: var(--text-secondary);
  font-size: 0.9rem;
  line-height: 1.6;
}

.section-label {
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
  margin-bottom: 12px;
}

/* Header tagline */
.header-tagline {
  color: rgba(255, 255, 255, 0.7);
  font-style: italic;
  font-size: 1rem;
  margin: 4px 0 12px;
}

/* Header genres */
.header-genres {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.header-genres .genre-tag {
  background: rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  color: var(--text-primary);
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 0.8rem;
  font-weight: 500;
}

.meta-runtime {
  color: rgba(255, 255, 255, 0.8);
  font-size: 0.85rem;
}

.modal-rating {
  font-weight: 600;
  font-size: 1rem;
  display: flex;
  align-items: center;
  gap: 6px;
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

.vote-count {
  font-weight: 400;
  font-size: 0.85rem;
  color: var(--text-muted);
}

/* Genre tags */
.genre-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.genre-tag {
  background: rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  color: var(--text-primary);
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 0.8rem;
  font-weight: 500;
}

/* Movie tagline */
.movie-tagline {
  color: var(--text-secondary);
  font-style: italic;
  font-size: 1rem;
  margin-bottom: 16px;
  background: rgba(0, 0, 0, 0.3);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  padding: 12px 16px;
  border-radius: 8px;
  display: inline-block;
}

/* Movie metadata - sidebar */
.movie-metadata {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px 24px;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-radius: 12px;
}

.meta-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.meta-grid {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  column-gap: 12px;
  row-gap: 8px;
  align-items: baseline;
}

.meta-label {
  color: #77d38a;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.meta-value {
  color: var(--text-primary);
  font-size: 0.9rem;
  line-height: 1.4;
}

.meta-synopsis {
  font-size: 0.84rem;
  line-height: 1.55;
}

.meta-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  color: #8ee59b;
  font-size: 0.82rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.meta-summary-item {
  color: #8ee59b;
  font-size: 0.82rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  white-space: nowrap;
}

.meta-keywords-section {
  padding-top: 2px;
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  row-gap: 1px;
}

.meta-keyword {
  color: var(--text-primary);
  font-size: 0.72rem;
  line-height: 1.1;
  white-space: nowrap;
}

.cast-list {
  --sync-row-tail: 0px;
  --cast-safe-start: var(--movie-nav-bar-width);
  --cast-safe-end: 32px;
  --sync-row-right-deadzone: 32px;
  position: absolute;
  top: calc(-1 * (var(--header-height) + 2.5rem));
  left: calc(-50vw + 50%);
  width: 100vw;
  margin: 0;
  padding: 4px calc(var(--cast-safe-end) + var(--sync-row-tail)) 8px var(--cast-safe-start);
  z-index: 0;
  display: flex;
  flex-wrap: nowrap;
  gap: 6px;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.cast-list::-webkit-scrollbar {
  height: 0;
  display: none;
}

.cast-card {
  flex: 0 0 94px;
  width: 94px;
  position: relative;
  overflow: hidden;
  border-radius: 12px;
  aspect-ratio: 2 / 3;
  cursor: pointer;
}

.cast-card::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  border: 0 solid rgba(255, 255, 255, 0.95);
  pointer-events: none;
  transition: border-width 120ms ease;
}

.cast-card:focus-visible,
html:not(.mouse-active) .cast-card.nav-focused {
  outline: none;
}

.cast-card:focus-visible::after,
html:not(.mouse-active) .cast-card.nav-focused::after {
  border-width: 2px;
}

.cast-photo {
  display: block;
  width: 100%;
  height: 100%;
  border-radius: inherit;
  object-fit: cover;
  background: rgba(255, 255, 255, 0.08);
}

.cast-photo-fallback {
  filter: saturate(0.9) contrast(1.05);
}

.cast-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  position: absolute;
  inset: auto 0 0 0;
  padding: 28px 8px 8px;
  background: linear-gradient(
    180deg,
    rgba(0, 0, 0, 0) 0%,
    rgba(0, 0, 0, 0.78) 45%,
    rgba(0, 0, 0, 0.95) 100%
  );
}

.cast-name {
  color: var(--text-primary);
  font-size: 0.72rem;
  font-weight: 600;
  line-height: 1.2;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.7);
}

.cast-character {
  color: #8ee59b;
  font-size: 0.64rem;
  line-height: 1.25;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.65);
}

@media (max-width: 1200px) {
  .content-layout {
    grid-template-columns: minmax(240px, 320px) minmax(0, 1fr);
    grid-template-areas:
      "left cast"
      "left main"
      "left right";
    gap: 24px;
  }
}

@media (max-width: 900px) {
  .content-layout {
    grid-template-columns: 1fr;
    grid-template-areas:
      "left"
      "cast"
      "main"
      "right";
    gap: 20px;
  }

  .sidebar-left {
    align-self: auto;
    display: grid;
    grid-template-columns: clamp(120px, 36vw, 180px) minmax(0, 1fr);
    align-items: start;
    gap: 12px;
  }

  .synopsis-box {
    width: 100%;
    margin: 0;
  }

  .versions-list-sidebar {
    min-width: 0;
  }

  .cast-list {
    --cast-safe-start: 32px;
    --cast-safe-end: 32px;
    --sync-row-right-deadzone: 32px;
    position: relative;
    top: auto;
    left: -32px;
    width: calc(100% + 64px);
    margin-left: 0;
    margin-top: 0;
  }
}

/* Showreel gallery */
.showreel-gallery {
  margin-bottom: 24px;
}

.showreel-title {
  font-size: 1rem;
  font-weight: 600;
  margin-bottom: 12px;
  color: var(--text-primary, #fff);
}

.showreel-images {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 8px;
  scroll-behavior: smooth;
}

.showreel-images::-webkit-scrollbar {
  height: 6px;
}

.showreel-images::-webkit-scrollbar-track {
  background: rgba(255, 255, 255, 0.05);
  border-radius: 3px;
}

.showreel-images::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.2);
  border-radius: 3px;
}

.showreel-image {
  height: 120px;
  width: auto;
  border-radius: 6px;
  flex-shrink: 0;
  object-fit: cover;
  transition: box-shadow 0.2s;
  cursor: pointer;
}

html.mouse-active .showreel-image:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
}

/* Movie versions styling */
.versions-section {
  margin-top: 24px;
  border-top: 1px solid var(--border-color, rgba(255, 255, 255, 0.1));
  padding-top: 20px;
}

.versions-title {
  font-size: 1.1rem;
  font-weight: 600;
  margin-bottom: 16px;
  color: var(--text-primary, #fff);
}

.version-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.version-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 8px;
  border: 1px solid transparent;
  transition:
    background 0.2s,
    border-color 0.2s;
}

html.mouse-active .version-item:hover {
  background: rgba(255, 255, 255, 0.08);
}

.version-item.version-best {
  border-color: rgba(70, 211, 105, 0.3);
  background: rgba(70, 211, 105, 0.05);
}

.version-info {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-width: 0;
}

.version-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.version-language-flags {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  width: 100%;
  white-space: nowrap;
  overflow: hidden;
  min-width: 0;
}

.version-language-flags > .language-flags-audio {
  flex: 0 0 auto;
}

.version-language-flags > .language-flags-subs {
  flex: 1 1 auto;
  min-width: 0;
  -webkit-mask-image: linear-gradient(to right, black calc(100% - 18px), transparent);
  mask-image: linear-gradient(to right, black calc(100% - 18px), transparent);
}

.version-language-flags > .language-flags-subs :deep(.language-flags) {
  display: inline-flex;
  max-width: 100%;
  overflow: hidden;
}

.version-language-flags > .language-flags-subs :deep(.language-flag-list) {
  width: max-content;
  max-width: none;
  overflow: hidden;
}

.language-separator {
  color: rgba(255, 255, 255, 0.8);
  font-size: 0.98rem;
  font-weight: 700;
  line-height: 1;
  margin: 0;
}

.version-meta-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) max-content;
  grid-template-rows: auto auto;
  column-gap: 8px;
  row-gap: 6px;
  align-items: stretch;
}

.version-meta-grid .version-badges {
  grid-column: 1;
  grid-row: 1;
}

.version-meta-grid .version-language-flags {
  grid-column: 1;
  grid-row: 2;
}

.version-dolby {
  grid-column: 2;
  grid-row: 1 / span 2;
  align-self: stretch;
  justify-self: end;
  display: flex;
  min-width: 0;
}

.version-badge {
  font-size: 0.75rem;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 500;
  text-transform: uppercase;
}

.version-badge.resolution {
  background: rgba(66, 133, 244, 0.2);
  color: #4285f4;
}

.version-badge.quality {
  background: rgba(156, 39, 176, 0.2);
  color: #ce93d8;
}

.version-badge.codec {
  background: rgba(255, 152, 0, 0.2);
  color: #ffb74d;
}

.version-badge.audio {
  background: rgba(0, 188, 212, 0.2);
  color: #4dd0e1;
}

.version-badge.best {
  background: rgba(70, 211, 105, 0.2);
  color: #46d369;
}

.version-path {
  font-size: 0.8rem;
  color: var(--text-muted, rgba(255, 255, 255, 0.5));
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.version-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
  margin-left: 0;
}

/* Season header styling */
.season-header {
  display: flex;
  gap: 20px;
  margin-bottom: 20px;
  align-items: flex-start;
}

.season-selector {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.season-selector label {
  font-size: 0.85rem;
  color: var(--text-muted, rgba(255, 255, 255, 0.5));
}

.season-selector select {
  background: var(--bg-secondary, #1f1f1f);
  border: 1px solid var(--border-color, #333);
  border-radius: 6px;
  padding: 8px 12px;
  color: var(--text-primary, #fff);
  font-size: 0.9rem;
  min-width: 180px;
  cursor: pointer;
}

.season-poster {
  flex-shrink: 0;
}

.season-poster img {
  height: 150px;
  width: auto;
  border-radius: 6px;
  object-fit: cover;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

/* Episode list styling */
.episode-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 16px;
}

.episode-item {
  background: rgba(255, 255, 255, 0.05);
  border-radius: 8px;
  border: 1px solid transparent;
  transition:
    background 0.2s,
    border-color 0.2s;
  overflow: hidden;
}

html.mouse-active .episode-item:hover {
  background: rgba(255, 255, 255, 0.08);
}

.episode-item.episode-expanded {
  border-color: rgba(66, 133, 244, 0.3);
}

.episode-header {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  cursor: pointer;
  gap: 16px;
}

.episode-thumbnail {
  flex-shrink: 0;
  width: 120px;
  height: 68px;
  border-radius: 4px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.1);
}

.episode-thumbnail img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.episode-thumbnail-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--text-muted, rgba(255, 255, 255, 0.3));
}

.episode-info {
  display: flex;
  align-items: center;
  gap: 16px;
  flex: 1;
  min-width: 0;
}

.episode-details {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  min-width: 0;
}

.episode-name {
  font-weight: 500;
  color: var(--text-primary, #fff);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.episode-meta {
  display: flex;
  gap: 12px;
  font-size: 0.8rem;
  color: var(--text-muted, rgba(255, 255, 255, 0.5));
}

.episode-runtime {
  color: var(--text-secondary, rgba(255, 255, 255, 0.7));
}

.episode-rating {
  color: #f9a825;
}

.episode-versions {
  color: #4285f4;
}

.episode-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
  margin-left: 16px;
}

.episode-overview {
  padding: 0 16px 12px 64px;
  font-size: 0.85rem;
  color: var(--text-secondary, rgba(255, 255, 255, 0.7));
  line-height: 1.5;
}

/* Release list within episodes */
.release-list {
  padding: 8px 16px 16px 64px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.release-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.03);
  border-radius: 6px;
  border: 1px solid transparent;
}

html.mouse-active .release-item:hover {
  background: rgba(255, 255, 255, 0.06);
}

.release-item.release-best {
  border-color: rgba(70, 211, 105, 0.3);
  background: rgba(70, 211, 105, 0.05);
}

.release-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  min-width: 0;
}

.release-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.release-badge {
  font-size: 0.7rem;
  padding: 2px 6px;
  border-radius: 4px;
  font-weight: 500;
  text-transform: uppercase;
}

.release-badge.resolution {
  background: rgba(66, 133, 244, 0.2);
  color: #4285f4;
}

.release-badge.quality {
  background: rgba(156, 39, 176, 0.2);
  color: #ce93d8;
}

.release-badge.codec {
  background: rgba(255, 152, 0, 0.2);
  color: #ffb74d;
}

.release-badge.audio {
  background: rgba(0, 188, 212, 0.2);
  color: #4dd0e1;
}

.release-badge.best {
  background: rgba(70, 211, 105, 0.2);
  color: #46d369;
}

.release-path {
  font-size: 0.75rem;
  color: var(--text-muted, rgba(255, 255, 255, 0.5));
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.release-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
  margin-left: 12px;
}

/* Collage header styles - full viewport width */
.collage-header {
  position: relative;
  height: 300px;
  overflow: hidden;
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
  /* Expand to full viewport width */
  width: 100vw;
  margin-left: calc(-50vw + 50%);
}

.collage-header .collage-grid {
  display: flex;
  height: 100%;
  width: 100%;
}

.collage-header .collage-item {
  flex: 1;
  min-width: 0;
  position: relative;
  margin-left: -2rem;
  /* Slanted clip - parallelogram shape with 2rem slant */
  clip-path: polygon(2rem 0, 100% 0, 100% 100%, 0 100%);
  cursor: pointer;
  overflow: hidden;
}

.collage-header .collage-item video {
  position: relative;
  z-index: 1;
  width: 100%;
  height: 100%;
  object-fit: cover;
  opacity: 0;
  transition: opacity 0.25s ease;
}

.collage-header .collage-item video.is-ready {
  opacity: 1;
}

/* First item - no slant, straight left edge */
.collage-header .collage-item:first-child {
  margin-left: 0;
  clip-path: polygon(0 0, 100% 0, 100% 100%, 0 100%);
}

/* Last item - slant on right edge for visual interest */
.collage-header .collage-item:last-child {
  clip-path: polygon(2rem 0, 100% 0, 100% 100%, 0 100%);
}

/* Single item - no slant */
.collage-header .collage-item:only-child {
  clip-path: none;
  margin-left: 0;
}

/* Subtle vignette on each collage image */
.collage-header .collage-item::before {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(to bottom, transparent 0%, rgba(0, 0, 0, 0.3) 100%);
  pointer-events: none;
  z-index: 2;
}

.collage-fallback-tile {
  position: absolute;
  inset: 0;
  z-index: 0;
}

.collage-fallback-1 {
  background: linear-gradient(135deg, #1b2738 0%, #0f1724 100%);
}

.collage-fallback-2 {
  background: linear-gradient(135deg, #1b2738 0%, #0f1724 100%);
}

.collage-fallback-3 {
  background: linear-gradient(135deg, #1b2738 0%, #0f1724 100%);
}

.collage-fallback-4 {
  background: linear-gradient(135deg, #1b2738 0%, #0f1724 100%);
}

.collage-fallback-5 {
  background: linear-gradient(135deg, #1b2738 0%, #0f1724 100%);
}

.collage-overlay {
  display: none;
}

.collage-content {
  position: absolute;
  bottom: 16px;
  left: 24px;
  right: 24px;
  z-index: 2;
  pointer-events: none;
}

.collage-content .modal-title {
  font-size: 1.8rem;
  margin-bottom: 8px;
}

.collage-content .modal-meta {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.meta-rating {
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 3px;
  background: rgba(0, 0, 0, 0.5);
  font-size: 0.8rem;
}

.meta-year {
  color: rgba(255, 255, 255, 0.8);
  font-size: 0.85rem;
}

.meta-disc {
  font-size: 1rem;
  line-height: 1;
}

.meta-badge {
  background: rgba(255, 255, 255, 0.15);
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 0.7rem;
  font-weight: 600;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.header-actions .btn {
  padding: 8px 16px;
  font-size: 0.85rem;
}

/* Versions list - vertical layout */
.versions-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
</style>
