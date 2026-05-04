<template>
  <div class="series-fullscreen">

    <!-- Hero section with backdrop or season collage -->
    <section class="series-hero">
      <div class="hero-bg">
        <!-- Use backdrop if available, otherwise create collage from season posters -->
        <img v-if="backdropUrl" :src="backdropUrl" class="hero-img" :alt="series.title || 'Unknown'" />
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
          <span v-if="series.info?.rating" class="meta-rating" :class="ratingClass">★ {{ series.info.rating.toFixed(1) }}</span>
          <span v-if="series.info?.number_of_seasons" class="meta-item">{{ series.info.number_of_seasons }} Seasons</span>
          <span v-if="series.info?.status" class="meta-badge">{{ series.info.status }}</span>
          <span v-if="series.info?.genres?.length" class="meta-genres">{{ series.info.genres.slice(0, 3).join(' • ') }}</span>
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
              <div v-if="season.overview" class="season-overview-short">{{ truncate(season.overview, 120) }}</div>
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
            @keydown.enter.prevent="handlePlay(episode)"
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
                v-if="getEpisodeImage(episode)"
                :ref="el => setVideoRef(el as HTMLVideoElement, `${sIndex}-${eIndex}`)"
                :src="getEpisodeImage(episode)"
                :alt="`Episode ${episode.episode_number}`"
                loop
                muted
                playsinline
              ></video>
              <div v-else class="tile-placeholder"></div>
            </div>

            <!-- Diagonal cut overlay -->
            <div class="tile-overlay"></div>

            <!-- Episode info overlay -->
            <div class="tile-info">
              <span class="ep-number">{{ episode.episode_number }}</span>
              <div class="ep-details">
                <span class="ep-name">{{ episode.name || `Episode ${episode.episode_number}` }}</span>
                <span v-if="episode.rating" class="ep-rating">★ {{ episode.rating.toFixed(1) }}</span>
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
        <div v-if="Object.values(contextMenu.episode.torrents || {}).length > 0">
          <div
            v-for="(torrent, index) in Object.values(contextMenu.episode.torrents || {})"
            :key="index"
            class="context-menu-version"
          >
            <div class="version-label">{{ getVersionLabel(torrent) }}</div>
            <div class="version-actions">
              <button
                class="ctx-btn ctx-btn-play"
                tabindex="0"
                @click="handlePlayVersion(torrent.playable_file)"
                :disabled="!torrent.playable_file"
              >▶ Play</button>
              <button
                class="ctx-btn ctx-btn-folder"
                tabindex="0"
                @click="handleOpenFolder(torrent.playable_file || '')"
              >📁</button>
            </div>
          </div>
        </div>
        <div v-else class="context-menu-empty">
          No versions available
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, nextTick, watch } from 'vue';
import type { Series, Season, Episode, Torrent } from '../types';
import { getCoverUrl } from '../api';
import { navAttrs } from '../composables/useKeyboardNavigation';

const props = defineProps<{
  series: Series;
  focusEpisode?: { seasonNumber: number; episodeNumber: number } | null;
}>();

const emit = defineEmits<{
  close: [];
  play: [string];
  openFolder: [string];
}>();

// Focus on matched episode when provided
watch(() => props.focusEpisode, (ep) => {
  if (ep) {
    // Delay to ensure DOM is fully rendered after route transition
    setTimeout(() => {
      // Find the season index and episode index
      const seasonIndex = props.series.seasons?.findIndex(s => s.season_number === ep.seasonNumber) ?? -1;
      if (seasonIndex >= 0) {
        const episodeIndex = props.series.seasons?.[seasonIndex]?.episodes?.findIndex(
          e => e.episode_number === ep.episodeNumber
        ) ?? -1;
        if (episodeIndex >= 0) {
          // Find the episode tile element using nav attributes
          const selector = `[data-nav-row="${seasonIndex + 2}"][data-nav-col="${episodeIndex}"]`;
          const element = document.querySelector(selector) as HTMLElement | null;
          if (element) {
            element.scrollIntoView({ behavior: 'smooth', block: 'center' });
            element.focus();
          }
        }
      }
    }, 150);
  }
}, { immediate: true });

// Context menu state
const contextMenu = ref<{
  visible: boolean;
  x: number;
  y: number;
  episode: Episode | null;
}>({
  visible: false,
  x: 0,
  y: 0,
  episode: null,
});

// Show context menu on right-click
function handleContextMenu(event: MouseEvent, episode: Episode) {
  event.preventDefault();
  contextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    episode,
  };
  // Add Escape key listener (capturing phase to intercept before other handlers)
  nextTick(() => {
    document.addEventListener('keydown', handleContextMenuKeydown, true);
    // Focus first play button in the menu
    const firstBtn = document.querySelector('.context-menu .ctx-btn-play:not(:disabled)') as HTMLElement;
    if (firstBtn) {
      firstBtn.focus();
      firstBtn.classList.add('nav-focused');
    }
  });
}

// Handle Escape and arrow keys in context menu (capturing phase to intercept before global handler)
function handleContextMenuKeydown(event: KeyboardEvent) {
  if (!contextMenu.value.visible || !contextMenu.value.episode) return;

  if (event.key === 'Escape') {
    event.preventDefault();
    event.stopPropagation();
    closeContextMenu();
    return;
  }

  // Handle arrow key navigation within the menu
  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) {
    event.preventDefault();
    event.stopPropagation();

    const menu = document.querySelector('.context-menu');
    if (!menu) return;

    const buttons = Array.from(menu.querySelectorAll('.ctx-btn:not(:disabled)')) as HTMLElement[];
    if (buttons.length === 0) return;

    const currentIndex = buttons.findIndex(btn => btn === document.activeElement);
    let nextIndex = currentIndex;

    // Each row has 2 buttons (play, folder), navigate accordingly
    const buttonsPerRow = 2;

    switch (event.key) {
      case 'ArrowRight':
        // Move to next button (play -> folder)
        nextIndex = Math.min(currentIndex + 1, buttons.length - 1);
        break;
      case 'ArrowLeft':
        // Move to previous button (folder -> play)
        nextIndex = Math.max(currentIndex - 1, 0);
        break;
      case 'ArrowDown':
        // Move to same column in next row
        nextIndex = Math.min(currentIndex + buttonsPerRow, buttons.length - 1);
        break;
      case 'ArrowUp':
        // Move to same column in previous row
        nextIndex = Math.max(currentIndex - buttonsPerRow, 0);
        break;
    }

    if (nextIndex >= 0 && nextIndex < buttons.length) {
      buttons[nextIndex].focus();
      buttons[nextIndex].classList.add('nav-focused');
      if (currentIndex >= 0 && currentIndex !== nextIndex) {
        buttons[currentIndex].classList.remove('nav-focused');
      }
    }
  }
}

// Close context menu
function closeContextMenu() {
  contextMenu.value.visible = false;
  document.removeEventListener('keydown', handleContextMenuKeydown, true);
}

// Play specific version
function handlePlayVersion(filePath: string | null) {
  if (filePath) {
    emit('play', filePath);
  }
  closeContextMenu();
}

// Open folder for a version
function handleOpenFolder(folderPath: string) {
  emit('openFolder', folderPath);
  closeContextMenu();
}

// Get version display label
function getVersionLabel(torrent: Torrent): string {
  const parts: string[] = [];
  if (torrent.resolution) parts.push(torrent.resolution);
  if (torrent.quality) parts.push(torrent.quality);
  if (torrent.codec) parts.push(torrent.codec);
  if (torrent.audio) parts.push(torrent.audio);
  return parts.length > 0 ? parts.join(' • ') : 'Unknown';
}

// Video refs for hover effects
const videoRefs = ref<Map<string, HTMLVideoElement>>(new Map());
let videoIndex = 0;

// Set video ref with staggered playback
function setVideoRef(el: HTMLVideoElement | null, key: string) {
  if (el) {
    videoRefs.value.set(key, el);
    // Staggered start times with 0.2 second offset
    const index = videoIndex++;
    setTimeout(() => {
      el.play().catch(() => {}); // Ignore autoplay policy errors
    }, index * 200);
  } else {
    videoRefs.value.delete(key);
  }
}

// Volume fade animation tracking
const volumeFadeIntervals = new Map<string, ReturnType<typeof setInterval>>();

// Handle hover-based audio fade in/out for episode videos
function handleEpisodeHover(key: string, isEntering: boolean) {
  const video = videoRefs.value.get(key);
  if (!video) return;

  // Clear any existing fade for this video
  const existingInterval = volumeFadeIntervals.get(key);
  if (existingInterval) {
    clearInterval(existingInterval);
    volumeFadeIntervals.delete(key);
  }

  if (isEntering) {
    // Mute all other videos immediately
    videoRefs.value.forEach((v, k) => {
      if (k !== key) {
        v.volume = 0;
        v.muted = true;
      }
    });

    // Fade in this video's audio
    video.muted = false;
    const fadeIn = setInterval(() => {
      if (video.volume < 0.95) {
        video.volume = Math.min(1, video.volume + 0.1);
      } else {
        video.volume = 1;
        clearInterval(fadeIn);
        volumeFadeIntervals.delete(key);
      }
    }, 30);
    volumeFadeIntervals.set(key, fadeIn);
  } else {
    // Fade out this video's audio
    const fadeOut = setInterval(() => {
      if (video.volume > 0.05) {
        video.volume = Math.max(0, video.volume - 0.1);
      } else {
        video.volume = 0;
        video.muted = true;
        clearInterval(fadeOut);
        volumeFadeIntervals.delete(key);
      }
    }, 30);
    volumeFadeIntervals.set(key, fadeOut);
  }
}

// Backdrop URL - only use backdrop_path, fall back to collage (handled in template)
const backdropUrl = computed(() => {
  if (props.series.info?.backdrop_path) {
    return getCoverUrl(props.series.info.backdrop_path);
  }
  return null;
});

// Seasons that have poster images
const seasonsWithPosters = computed(() => {
  return props.series.seasons.filter(s => s.poster_path);
});

// Rating class
const ratingClass = computed(() => {
  if (!props.series.info?.rating) return '';
  if (props.series.info.rating >= 7.5) return 'rating-high';
  if (props.series.info.rating >= 6) return 'rating-medium';
  return 'rating-low';
});

// Get season poster
function getSeasonPoster(season: Season): string | undefined {
  if (season.poster_path) {
    return getCoverUrl(season.poster_path);
  }
  return undefined;
}

// Get episode image
function getEpisodeImage(episode: Episode): string | undefined {
  if (episode.reel_image) {
    return getCoverUrl(episode.reel_image);
  }
  if (episode.still_path && !episode.still_path.startsWith('/')) {
    return getCoverUrl(episode.still_path);
  }
  return undefined;
}

// Collage slice style for season posters
function getCollageSliceStyle(season: Season, index: number) {
  const posterUrl = season.poster_path ? getCoverUrl(season.poster_path) : null;
  const totalSlices = Math.min(seasonsWithPosters.value.length, 5);
  const sliceWidth = 100 / totalSlices;

  return {
    backgroundImage: posterUrl ? `url('${posterUrl}')` : 'linear-gradient(135deg, #1a1a2e, #16213e)',
    left: `${index * sliceWidth}%`,
    width: `${sliceWidth + 5}%`, // overlap slightly
    clipPath: `polygon(${index * 10}% 0, 100% 0, ${100 - (totalSlices - index - 1) * 10}% 100%, 0% 100%)`,
  };
}

// Truncate text
function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength).trim() + '...';
}

// Handle play
function handlePlay(episode: Episode) {
  const playableFile = Object.values(episode.torrents || {})[0]?.playable_file;
  if (playableFile) {
    emit('play', playableFile);
  }
}
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

.rating-high { color: #46d369; }
.rating-medium { color: #f9a825; }
.rating-low { color: #e53935; }

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

.episode-tile:hover,
.episode-tile.nav-focused {
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
  0%, 100% {
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
.episode-tile:hover .tile-focus-outline,
.episode-tile.nav-focused .tile-focus-outline {
  opacity: 1;
  animation: tile-outline-blink 1s ease-in-out infinite;
}

/* Brighter outline for keyboard focus */
.episode-tile.nav-focused .tile-focus-outline rect {
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

.episode-tile:hover .tile-overlay {
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

.episode-tile:hover .tile-play {
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
  overflow: hidden;
}

.context-menu-header {
  padding: 12px 16px;
  font-weight: 600;
  font-size: 0.9rem;
  background: rgba(255, 255, 255, 0.05);
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.context-menu-version {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  gap: 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  transition: background-color 0.15s ease;
}

.context-menu-version:last-child {
  border-bottom: none;
}

.version-label {
  font-size: 0.8rem;
  color: rgba(255, 255, 255, 0.8);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.version-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.ctx-btn {
  padding: 6px 12px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 600;
  transition: all 0.2s ease;
  position: relative;
  outline: none;
}

/* Blinking animation for button focus */
@keyframes btn-outline-blink {
  0%, 100% {
    box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.9);
  }
  50% {
    box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.4);
  }
}

.ctx-btn-play {
  background: #e50914;
  color: white;
}

.ctx-btn-play:hover:not(:disabled),
.ctx-btn-play.nav-focused:not(:disabled) {
  background: #f40612;
  animation: btn-outline-blink 1s ease-in-out infinite;
}

.ctx-btn-play:disabled {
  background: rgba(255, 255, 255, 0.1);
  color: rgba(255, 255, 255, 0.4);
  cursor: not-allowed;
}

.ctx-btn-folder {
  background: rgba(255, 255, 255, 0.1);
  color: white;
}

.ctx-btn-folder:hover,
.ctx-btn-folder.nav-focused {
  background: rgba(255, 255, 255, 0.2);
  animation: btn-outline-blink 1s ease-in-out infinite;
}

.context-menu-empty {
  padding: 16px;
  text-align: center;
  color: rgba(255, 255, 255, 0.5);
  font-size: 0.85rem;
}
</style>
