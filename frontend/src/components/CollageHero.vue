<template>
  <section class="collage-hero" @keydown="handleKeyDown">
    <!-- Diagonal collage grid -->
    <div class="collage-grid">
      <template v-for="(item, index) in collageItems" :key="item.id">
        <div
          :ref="el => setItemRef(el as HTMLElement, index)"
          class="collage-item"
          :class="[`collage-item-${index}`, { 'collage-featured': index === 0, 'collage-item-top-row': isTopRowItem(index), 'nav-focused': focusedIndex === index, 'collage-hidden': !isItemVisible(index) }]"
          v-bind="getItemAttrs(index)"
          @click="handleItemClick(item, index)"
          @focus="focusedIndex = index"
        >
          <!-- Featured item with hexagonal clip -->
          <div v-if="index === 0" class="hex-showcase">
            <img
              v-if="getImageUrl(item)"
              :src="getImageUrl(item)!"
              :alt="item.title || 'Unknown'"
              class="hex-clip"
            />
          </div>
          <!-- SVG focus outline for big hex -->
          <svg v-if="index === 0" class="hex-focus-outline hex-focus-big" viewBox="0 0 130 100" preserveAspectRatio="none">
            <polygon points="21.67,0 21.67,25 0,37.5 0,62.5 21.67,75 21.67,100 108.33,100 108.33,75 130,62.5 130,37.5 108.33,25 108.33,0" />
          </svg>
          <!-- Non-featured items: image, video, or placeholder -->
          <img
            v-if="index !== 0 && getImageUrl(item)"
            :src="getImageUrl(item)!"
            :alt="item.title || 'Unknown'"
            class="collage-media"
          />
          <video
            v-if="index !== 0 && !getImageUrl(item) && getVideoUrl(item)"
            :src="getVideoUrl(item)!"
            class="collage-media"
            muted
            playsinline
          />
          <div v-if="index !== 0 && !getImageUrl(item) && !getVideoUrl(item)" class="collage-placeholder">
            <span class="placeholder-title">{{ item.title }}</span>
          </div>
        <div class="collage-item-overlay"></div>
        <!-- SVG focus outline for small hex items -->
        <svg v-if="index !== 0 && index !== 2 && !isTopRowItem(index)" class="hex-focus-outline hex-focus-small" viewBox="0 0 86.6 100" preserveAspectRatio="none">
          <polygon points="43.3,0 86.6,25 86.6,75 43.3,100 0,75 0,25" />
        </svg>
        <!-- Item 2 uses a custom flat-bottom hex outline to match its clip-path -->
        <svg v-if="index === 2" class="hex-focus-outline hex-focus-small" viewBox="0 0 86.6 100" preserveAspectRatio="none">
          <polygon points="43.3,0 86.6,33.333 86.6,100 0,100 0,33.333" />
        </svg>
        <!-- Top-row items use a custom flat-top hex outline to match their clip-path -->
        <svg v-if="isTopRowItem(index)" class="hex-focus-outline hex-focus-small" viewBox="0 0 86.6 100" preserveAspectRatio="none">
          <polygon points="86.6,0 86.6,66.667 43.3,100 0,66.667 0,0" />
        </svg>
        <div class="collage-item-info" v-if="index === 0">
          <h1 class="collage-title">{{ item.title }}</h1>
          <div class="collage-meta">
            <span v-if="item.year" class="meta-year">{{ item.year }}</span>
            <span v-if="getRating(item)" class="meta-rating" :class="getRatingClass(item)">
              ★ {{ getRating(item)?.toFixed(1) }}
            </span>
            <span v-if="getResolution(item)" class="meta-quality">{{ getResolution(item) }}</span>
          </div>
          <p v-if="getOverview(item)" class="collage-overview">{{ getOverview(item) }}</p>
          <div class="collage-buttons">
            <button class="btn btn-primary" @click.stop="handlePlay(item)">▶ Play</button>
            <button class="btn btn-secondary" @click.stop="$emit('info', item)">ℹ Info</button>
          </div>
        </div>
        <div class="collage-item-hover" v-else>
          <span class="hover-title">{{ item.title }}</span>
          <span v-if="getRating(item)" class="hover-rating">★ {{ getRating(item)?.toFixed(1) }}</span>
        </div>
        </div>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, nextTick, watch } from 'vue';
import type { MediaItem, Movie, Series } from '../types';
import { getCoverUrl } from '../api';
import { navAttrs } from '../composables/useKeyboardNavigation';

const focusedIndex = ref<number | null>(null);
const itemRefs = ref<(HTMLElement | null)[]>([]);
// Track last row when on col=0 (big image) for sideways navigation
const lastRow = ref(1);

// Reset lastRow when entering the hero from outside (via global nav)
watch(focusedIndex, (newVal, oldVal) => {
  if (newVal === 0 && oldVal === null) {
    // Entering big image from outside hero - reset to row 1
    lastRow.value = 1;
  }
});
// Track which items are visible (at least 75% in viewport)
const visibleItems = ref<Set<number>>(new Set());

function setItemRef(el: HTMLElement | null, index: number) {
  itemRefs.value[index] = el;
}

// Check if element is at least 75% visible horizontally
function isElementVisible(el: HTMLElement | null): boolean {
  if (!el) return false;
  const rect = el.getBoundingClientRect();
  const viewportWidth = window.innerWidth;

  // Calculate how much of the element is visible
  const visibleLeft = Math.max(0, rect.left);
  const visibleRight = Math.min(viewportWidth, rect.right);
  const visibleWidth = Math.max(0, visibleRight - visibleLeft);
  const visibleRatio = visibleWidth / rect.width;

  return visibleRatio >= 0.75;
}

function updateVisibility() {
  const newVisible = new Set<number>();

  for (let i = 0; i < itemRefs.value.length; i++) {
    // Always include items 0, 1, 2 (big image and left side)
    if (i <= 2 || isElementVisible(itemRefs.value[i])) {
      newVisible.add(i);
    }
  }

  visibleItems.value = newVisible;
}

onMounted(() => {
  window.addEventListener('resize', updateVisibility);
  document.addEventListener('focusin', handleDocumentFocusIn);
  // Initial visibility check after render
  nextTick(() => {
    updateVisibility();
  });
});

onUnmounted(() => {
  window.removeEventListener('resize', updateVisibility);
  document.removeEventListener('focusin', handleDocumentFocusIn);
});

function clearHeroFocus() {
  focusedIndex.value = null;
}

function handleDocumentFocusIn(event: FocusEvent) {
  const target = event.target as HTMLElement | null;
  if (!target?.closest('.collage-hero')) {
    clearHeroFocus();
  }
}

// Coordinate system for navigation based on actual CSS positioning:
// Visual layout (honeycomb stagger, but sequential cols for navigation):
//   top=-12.5%:    [1]      [3]   [6]   [9]  [12] [15]    <- row 0
//   top=25%:            [0]    [5]   [8]  [11] [14]       <- row 1
//   top=62.5%:    [2]      [4]   [7]  [10] [13] [16]      <- row 2
//
// Navigation columns (no gaps - sequential numbering):
// col -1: left side items (1, 2)
// col 0: big image (0)
// col 1: first right column (3, 5, 4)
// col 2: second right column (6, 8, 7)
// col 3: third right column (9, 11, 10)
// col 4: fourth right column (12, 14, 13)
// col 5: fifth right column (15, 16)

interface NavCoord { row: number; col: number }
const coordMap: Record<number, NavCoord> = {
  0:  { row: 1, col: 0 },   // Big image
  // Left side (col -1)
  1:  { row: 0, col: -1 },  // top left
  2:  { row: 2, col: -1 },  // bottom left
  // Right side - sequential columns
  3:  { row: 0, col: 1 },
  4:  { row: 2, col: 1 },
  5:  { row: 1, col: 1 },
  6:  { row: 0, col: 2 },
  7:  { row: 2, col: 2 },
  8:  { row: 1, col: 2 },
  9:  { row: 0, col: 3 },
  10: { row: 2, col: 3 },
  11: { row: 1, col: 3 },
  12: { row: 0, col: 4 },
  13: { row: 2, col: 4 },
  14: { row: 1, col: 4 },
  15: { row: 0, col: 5 },
  16: { row: 2, col: 5 },
  17: { row: 1, col: 5 },
  18: { row: 0, col: 6 },
  19: { row: 2, col: 6 },
  20: { row: 1, col: 6 },
  21: { row: 0, col: 7 },
  22: { row: 2, col: 7 },
  23: { row: 1, col: 7 },
  24: { row: 0, col: 8 },
  25: { row: 2, col: 8 },
  26: { row: 1, col: 8 },
};

function isTopRowItem(index: number): boolean {
  const coord = coordMap[index];
  return index !== 0 && coord?.row === 0;
}

// Reverse lookup: find item index at given coordinates
function findItemAt(row: number, col: number): number | null {
  for (const [idx, coord] of Object.entries(coordMap)) {
    if (coord.row === row && coord.col === col) return parseInt(idx);
  }
  return null;
}

// Find nearest item in a direction from current position
function findNext(currentIdx: number, direction: 'up' | 'down' | 'left' | 'right'): number | null {
  const current = coordMap[currentIdx];
  if (!current) return null;

  // Use lastRow for big image vertical navigation
  const effectiveRow = current.col === 0 ? lastRow.value : current.row;

  if (direction === 'left') {
    // Moving left: decrease col
    const targetCol = current.col - 1;

    if (current.col === 0) {
      // From big image, go to col -1 if it exists for the effective row
      // Row 1 has no col -1, so stay on big image (or could wrap to last item)
      const leftItem = findItemAt(effectiveRow, -1);
      if (leftItem !== null) {
        return leftItem;
      }
      // Row 1 has no left item - fall back to top-left tile so left side stays reachable
      const topLeftItem = findItemAt(0, -1);
      if (topLeftItem !== null) {
        lastRow.value = 0;
        return topLeftItem;
      }
      // No fallback available - stay put
      return 0;
    }

    if (targetCol < -1) {
      // Already at col -1, can't go further left - stay put
      return currentIdx;
    }

    if (targetCol === 0) {
      // Moving to big image - remember current row
      lastRow.value = current.row;
      return 0;
    }
    // Find item at same row, col-1
    return findItemAt(current.row, targetCol);
  }

  if (direction === 'right') {
    // Moving right: increase col
    const targetCol = current.col + 1;

    if (current.col === 0) {
      // From big image, go to col 1 at remembered row
      return findItemAt(lastRow.value, 1);
    }
    if (current.col === -1) {
      // From left column, go to big image
      lastRow.value = current.row;
      return 0;
    }
    // Find item at same row, col+1, but only if it's visible
    const nextItem = findItemAt(current.row, targetCol);
    if (nextItem !== null && visibleItems.value.has(nextItem)) {
      return nextItem;
    }
    // No more visible items to the right - stay put
    return currentIdx;
  }

  // Helper to check if item at row/col is visible
  const isItemVisibleAt = (row: number, col: number) => {
    const item = findItemAt(row, col);
    return item !== null && visibleItems.value.has(item);
  };

  if (direction === 'up') {
    const targetRow = effectiveRow - 1;
    if (targetRow < 0) return null; // Exit up

    if (current.col === 0) {
      // Big image: up/down always exits the hero section
      return null;
    }

    // Check if target row item at same col is visible
    if (!isItemVisibleAt(targetRow, current.col)) {
      // Skip to row above if middle row is not visible at this column
      if (targetRow === 1 && isItemVisibleAt(0, current.col)) {
        const item = findItemAt(0, current.col);
        if (item !== null) return item;
      }
      return currentIdx; // Stay put
    }

    // Find item at row-1, same col (or nearest)
    let item = findItemAt(targetRow, current.col);
    if (item !== null) return item;
    // Try to find nearest col in target row
    for (let c = current.col; c >= -1; c--) {
      item = findItemAt(targetRow, c);
      if (item !== null && isItemVisibleAt(targetRow, c)) return item;
    }
    for (let c = current.col + 1; c <= 10; c++) {
      item = findItemAt(targetRow, c);
      if (item !== null && isItemVisibleAt(targetRow, c)) return item;
    }
    return null;
  }

  if (direction === 'down') {
    const targetRow = effectiveRow + 1;
    if (targetRow > 2) return null; // Exit down

    if (current.col === 0) {
      // Big image: up/down always exits the hero section
      return null;
    }

    // Check if target row item at same col is visible
    if (!isItemVisibleAt(targetRow, current.col)) {
      // Skip to row below if middle row is not visible at this column
      if (targetRow === 1 && isItemVisibleAt(2, current.col)) {
        const item = findItemAt(2, current.col);
        if (item !== null) return item;
      }
      return currentIdx; // Stay put
    }

    // Find item at row+1, same col (or nearest)
    let item = findItemAt(targetRow, current.col);
    if (item !== null) return item;
    // Try to find nearest col in target row
    for (let c = current.col; c >= -1; c--) {
      item = findItemAt(targetRow, c);
      if (item !== null && isItemVisibleAt(targetRow, c)) return item;
    }
    for (let c = current.col + 1; c <= 10; c++) {
      item = findItemAt(targetRow, c);
      if (item !== null && isItemVisibleAt(targetRow, c)) return item;
    }
    return null;
  }

  return null;
}

function handleKeyDown(e: KeyboardEvent) {
  // Only handle if focus is within this component
  const target = e.target as HTMLElement;
  if (!target.closest('.collage-hero')) return;

  const direction = {
    ArrowUp: 'up',
    ArrowDown: 'down',
    ArrowLeft: 'left',
    ArrowRight: 'right',
  }[e.key] as 'up' | 'down' | 'left' | 'right' | undefined;

  if (!direction) {
    if (e.key === 'Enter' && focusedIndex.value !== null) {
      const item = collageItems.value[focusedIndex.value];
      if (item) handleItemClick(item, focusedIndex.value);
      e.preventDefault();
    }
    return;
  }

  const current = focusedIndex.value ?? 0;
  const next = findNext(current, direction);

  if (next !== null && itemRefs.value[next]) {
    e.preventDefault();
    e.stopPropagation();
    focusedIndex.value = next;
    itemRefs.value[next]?.focus({ preventScroll: true });
    return;
  }

  // Navigation is leaving this section; clear local highlight and let global handler continue.
  clearHeroFocus();
  // If next is null, let event bubble to global navigation
}

// Get attributes for an item - only item 0 participates in global nav as entry point
function getItemAttrs(index: number) {
  if (index === 0) {
    // Big image is the entry point at row 0, col 0 in global nav
    return { ...navAttrs(0, 0) };
  }
  return { tabindex: 0 };
}

// Check if an item index should be visible based on its column and row
function isItemVisible(index: number): boolean {
  // Always show the first few items (big image and left side)
  if (index <= 2) return true;
  return visibleItems.value.has(index);
}

const props = defineProps<{
  items: MediaItem[];
  featuredItem?: MediaItem | null;
}>();

const emit = defineEmits<{
  play: [string];
  info: [MediaItem];
  select: [MediaItem];
}>();

// Max items we might ever need - use a generous constant
const maxItems = 27;

// Get items for the collage based on visible columns
const collageItems = computed(() => {
  const result: MediaItem[] = [];

  // Add featured item first
  if (props.featuredItem) {
    result.push(props.featuredItem);
  }

  // Add more items, avoiding duplicates, up to max needed
  for (const item of props.items) {
    if (result.length >= maxItems) break;
    if (!result.find(r => r.id === item.id)) {
      result.push(item);
    }
  }

  return result;
});

function getImageUrl(item: MediaItem): string | undefined {
  const coverPath = item.cover_path;
  const backdropPath = item.type === 'movies'
    ? (item.data as Movie).backdrop_path
    : (item.data as Series).backdrop_path;
  const imagePath = coverPath || backdropPath;

  // Check if the path is an image (not a video)
  if (imagePath && /\.(webm|mp4|mkv|avi|mov)$/i.test(imagePath)) {
    return undefined;
  }

  return getCoverUrl(imagePath);
}

function getVideoUrl(item: MediaItem): string | undefined {
  // Check showreel_images for video files
  if (item.type === 'movies') {
    const movie = item.data as Movie;
    const showreel = movie.showreel_images;
    if (showreel && showreel.length > 0) {
      // Find first video file in showreel
      const video = showreel.find(path => /\.(webm|mp4)$/i.test(path));
      if (video) {
        return getCoverUrl(video);
      }
    }
  }
  return undefined;
}

function getRating(item: MediaItem): number | null {
  if (item.type === 'movies') {
    return (item.data as Movie).info?.rating ?? null;
  }
  return (item.data as Series).info?.rating ?? null;
}

function getRatingClass(item: MediaItem): string {
  const rating = getRating(item);
  if (!rating) return '';
  if (rating >= 7.5) return 'rating-high';
  if (rating >= 6) return 'rating-medium';
  return 'rating-low';
}

function getResolution(item: MediaItem): string | null {
  if (item.type === 'movies') {
    const movie = item.data as Movie;
    return Object.values(movie.torrents || {})[0]?.resolution ?? null;
  }
  return null;
}

function getOverview(item: MediaItem): string | null {
  const overview = item.type === 'movies'
    ? (item.data as Movie).info?.overview
    : (item.data as Series).info?.overview;
  if (!overview) return null;
  return overview.length > 150 ? overview.slice(0, 150) + '...' : overview;
}

function getPlayableFile(item: MediaItem): string | null {
  if (item.type === 'movies') {
    const movie = item.data as Movie;
    return Object.values(movie.torrents || {})[0]?.playable_file ?? null;
  }
  const series = item.data as Series;
  for (const season of series.seasons || []) {
    for (const episode of season.episodes || []) {
      for (const torrent of Object.values(episode.torrents || {})) {
        if (torrent.playable_file) return torrent.playable_file;
      }
    }
  }
  return null;
}

function handlePlay(item: MediaItem) {
  const file = getPlayableFile(item);
  if (file) emit('play', file);
}

function handleItemClick(item: MediaItem, index: number) {
  if (index === 0) {
    emit('info', item);
  } else {
    emit('select', item);
  }
}
</script>

<style scoped>
.collage-hero {
  position: relative;
  height: 70vh;
  min-height: 450px;
  max-height: 600px;
  overflow: visible;
  background: var(--bg-primary);
  --h: clamp(450px, 70vh, 600px);
  --small-w: calc(0.433 * var(--h));  /* Small hex width = 0.866 * 50% of height */
  --big-edge: calc(1.0833 * var(--h)); /* Big hex right edge = 1.3 * 0.8333 * height */
}

.collage-grid {
  position: relative;
  height: 100%;
}

.collage-item {
  position: absolute;
  cursor: pointer;
  overflow: hidden;
  transition: transform 0.3s ease, filter 0.3s ease, opacity 0.3s ease, visibility 0.3s ease;
}

/* Media (images and videos) fill the collage item */
.collage-media {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center 30%;
  transition: transform 0.3s ease, filter 0.3s ease, opacity 0.3s ease, visibility 0.3s ease;
}

/* Hidden items - keep in DOM for measurement but invisible */
.collage-item.collage-hidden {
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
}

.collage-item:hover,
.collage-item.nav-focused {
  z-index: 10;
}

/* SVG focus outline styles */
.hex-focus-outline {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 5;
  opacity: 0;
  transition: opacity 0.2s ease;
}

.hex-focus-outline polygon {
  fill: none;
  stroke: rgba(255, 255, 255, 0.9);
  stroke-width: 4;
  vector-effect: non-scaling-stroke;
}

/* Show outline on hover and focus */
.collage-item:hover .hex-focus-outline,
.collage-item.nav-focused .hex-focus-outline {
  opacity: 1;
}

/* Blinking animation for focus outline */
@keyframes hex-outline-blink {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.4;
  }
}

/* Brighter outline for keyboard focus */
.collage-item.nav-focused .hex-focus-outline polygon {
  stroke: #ffffff;
  stroke-width: 5;
  filter: drop-shadow(0 0 6px rgba(255, 255, 255, 0.8));
}

/* Blinking effect on hover/focus */
.collage-item:hover .hex-focus-outline,
.collage-item.nav-focused .hex-focus-outline {
  animation: hex-outline-blink 1s ease-in-out infinite;
}

/* Small items: slightly smaller to create gaps */
.collage-item:not(.collage-featured) {
  transform: scale(0.97);
}

/* Featured item: slightly smaller to create gaps */
.collage-featured {
  transform: scale(0.99);
}

/* Featured item - left side */
.collage-item-0 {
  left: calc(var(--small-w) * 0.5);
  top: 0;
  height: 100%;
  aspect-ratio: 1.3 / 1;
  clip-path: polygon(
    16.67% 0%,
    16.67% 25%,
    0%     37.5%,
    0%     62.5%,
    16.67% 75%,
    16.67% 100%,

    83.33% 100%,

    83.33% 75%,
    100%   62.5%,
    100%   37.5%,
    83.33% 25%,
    83.33% 0%
  );
}

/* Hexagonal showcase for featured item */
.hex-showcase {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.hex-showcase img.hex-clip {
  height: 100%;
  aspect-ratio: 1.3 / 1;
  object-fit: cover;
  object-position: center 30%;
  clip-path: polygon(
    16.67% 0%,
    16.67% 25%,
    0%     37.5%,
    0%     62.5%,
    16.67% 75%,
    16.67% 100%,

    83.33% 100%,

    83.33% 75%,
    100%   62.5%,
    100%   37.5%,
    83.33% 25%,
    83.33% 0%
  );
}

/* Small items: 50% height, aspect 0.866:1 (pointy-top regular hex) */
/* 3 rows with 25% overlap, shifted up 12.5% (25% of small height) */
/* Fill order: left side first (1-2), then right side nearest to big image (3+) */

.collage-item:not(.collage-featured) {
  height: 50%;
  aspect-ratio: 0.866 / 1;
  clip-path: polygon(
    50%  0%,
    100% 25%,
    100% 75%,
    50%  100%,
    0%   75%,
    0%   25%
  );
}

/* LEFT SIDE - positioned at left edge */
/* Row 1 (top) */
.collage-item-1 {
  top: -12.5%;
  left: 0;
}

/* Row 3 (bottom) */
.collage-item-2 {
  top: 62.5%;
  left: 0;
}

/* Keep top hex shape for left-bottom item, but flatten bottom edge to avoid overflow */
.collage-item.collage-item-2:not(.collage-featured) {
  height: 37.5%;
  width: var(--small-w);
  clip-path: polygon(
    50%  0%,
    100% 33.333%,
    100% 100%,
    0%   100%,
    0%   33.333%
  );
}

/* Top-row items: keep side angles but flatten top edge to avoid top overflow */
.collage-item.collage-item-top-row:not(.collage-featured) {
  top: 0;
  height: 37.5%;
  width: var(--small-w);
  clip-path: polygon(
    100% 0%,
    100% 66.667%,
    50%  100%,
    0%   66.667%,
    0%   0%
  );
}

/* RIGHT SIDE - Column 0 (closest to big image) */
/* Row 1 (top): -12.5% */
.collage-item-3 {
  top: -12.5%;
  left: calc(var(--big-edge) + var(--small-w) * 0.5);
}

/* Row 3 (bottom): 62.5% */
.collage-item-4 {
  top: 62.5%;
  left: calc(var(--big-edge) + var(--small-w) * 0.5);
}

/* Row 2 (middle): 25%, offset 0.5 */
.collage-item-5 {
  top: 25%;
  left: calc(var(--big-edge) + var(--small-w) * 1);
}

/* RIGHT SIDE - Column 1 */
.collage-item-6 {
  top: -12.5%;
  left: calc(var(--big-edge) + var(--small-w) * 1.5);
}

.collage-item-7 {
  top: 62.5%;
  left: calc(var(--big-edge) + var(--small-w) * 1.5);
}

.collage-item-8 {
  top: 25%;
  left: calc(var(--big-edge) + var(--small-w) * 2);
}

/* RIGHT SIDE - Column 2 */
.collage-item-9 {
  top: -12.5%;
  left: calc(var(--big-edge) + var(--small-w) * 2.5);
}

.collage-item-10 {
  top: 62.5%;
  left: calc(var(--big-edge) + var(--small-w) * 2.5);
}

.collage-item-11 {
  top: 25%;
  left: calc(var(--big-edge) + var(--small-w) * 3);
}

/* RIGHT SIDE - Column 3 */
.collage-item-12 {
  top: -12.5%;
  left: calc(var(--big-edge) + var(--small-w) * 3.5);
}

.collage-item-13 {
  top: 62.5%;
  left: calc(var(--big-edge) + var(--small-w) * 3.5);
}

.collage-item-14 {
  top: 25%;
  left: calc(var(--big-edge) + var(--small-w) * 4);
}

/* RIGHT SIDE - Column 4 */
.collage-item-15 {
  top: -12.5%;
  left: calc(var(--big-edge) + var(--small-w) * 4.5);
}

.collage-item-16 {
  top: 62.5%;
  left: calc(var(--big-edge) + var(--small-w) * 4.5);
}

.collage-item-17 {
  top: 25%;
  left: calc(var(--big-edge) + var(--small-w) * 5);
}

/* RIGHT SIDE - Column 5 */
.collage-item-18 {
  top: -12.5%;
  left: calc(var(--big-edge) + var(--small-w) * 5.5);
}

.collage-item-19 {
  top: 62.5%;
  left: calc(var(--big-edge) + var(--small-w) * 5.5);
}

.collage-item-20 {
  top: 25%;
  left: calc(var(--big-edge) + var(--small-w) * 6);
}

/* RIGHT SIDE - Column 6 */
.collage-item-21 {
  top: -12.5%;
  left: calc(var(--big-edge) + var(--small-w) * 6.5);
}

.collage-item-22 {
  top: 62.5%;
  left: calc(var(--big-edge) + var(--small-w) * 6.5);
}

.collage-item-23 {
  top: 25%;
  left: calc(var(--big-edge) + var(--small-w) * 7);
}

/* RIGHT SIDE - Column 7 */
.collage-item-24 {
  top: -12.5%;
  left: calc(var(--big-edge) + var(--small-w) * 7.5);
}

.collage-item-25 {
  top: 62.5%;
  left: calc(var(--big-edge) + var(--small-w) * 7.5);
}

.collage-item-26 {
  top: 25%;
  left: calc(var(--big-edge) + var(--small-w) * 8);
}

.collage-item-overlay {
  position: absolute;
  inset: 0;
  background: transparent;
  pointer-events: none;
}

/* Placeholder for missing images */
.collage-placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.collage-placeholder::before {
  content: '';
  position: absolute;
  inset: 0;
  background: #e50914;
}

.placeholder-title {
  position: relative;
  color: white;
  font-size: 1rem;
  font-weight: bold;
  text-align: center;
  padding: 10px;
  text-shadow: 0 2px 4px rgba(0,0,0,0.8);
  z-index: 1;
}

/* Featured item content */
.collage-item-info {
  position: absolute;
  bottom: 15%;
  left: 50%;
  transform: translateX(-50%);
  width: 60%;
  text-align: center;
  z-index: 2;
  color: white;
}

.collage-title {
  font-size: clamp(1.8rem, 4vw, 3rem);
  font-weight: 700;
  margin-bottom: 12px;
  text-shadow: 2px 2px 8px rgba(0, 0, 0, 0.8);
  line-height: 1.1;
}

.collage-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.meta-year {
  color: rgba(255, 255, 255, 0.8);
  font-size: 0.95rem;
}

.meta-rating {
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.6);
  font-size: 0.85rem;
}

.rating-high { color: #46d369; }
.rating-medium { color: #f9a825; }
.rating-low { color: #e53935; }

.meta-quality {
  background: rgba(255, 255, 255, 0.15);
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 600;
}

.collage-overview {
  color: rgba(255, 255, 255, 0.85);
  font-size: 0.9rem;
  line-height: 1.4;
  margin-bottom: 16px;
  max-width: 450px;
}

.collage-buttons {
  display: flex;
  gap: 10px;
}

.collage-buttons .btn {
  padding: 10px 24px;
  font-size: 0.95rem;
}

/* Hover info for non-featured items */
.collage-item-hover {
  position: absolute;
  bottom: 40%;
  left: 50%;
  transform: translateX(-50%) translateY(10px);
  padding: 8px 16px;
  background: rgba(0, 0, 0, 0.85);
  border-radius: 4px;
  opacity: 0;
  transition: opacity 0.3s ease, transform 0.3s ease;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.collage-item:not(.collage-featured):hover .collage-item-hover,
.collage-item:not(.collage-featured).nav-focused .collage-item-hover {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
}

.hover-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: white;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.hover-rating {
  font-size: 0.75rem;
  color: #46d369;
}

/* Responsive adjustments */
@media (max-width: 900px) {
  .collage-hero {
    height: 50vh;
    min-height: 350px;
    --h: clamp(350px, 50vh, 600px);
  }

  .collage-item-info {
    right: 20%;
    bottom: 10%;
  }

  .collage-overview {
    display: none;
  }
}

@media (max-width: 600px) {
  .collage-hero {
    height: 45vh;
    min-height: 280px;
    --h: clamp(280px, 45vh, 600px);
  }

  .collage-item-0 {
    left: 50%;
    transform: translateX(-50%);
  }

  .hex-showcase img.hex-clip {
    clip-path: none;
    aspect-ratio: auto;
    height: 100%;
  }

  .collage-item-1,
  .collage-item-2,
  .collage-item-3,
  .collage-item-4,
  .collage-item-5,
  .collage-item-6,
  .collage-item-7,
  .collage-item-8,
  .collage-item-9,
  .collage-item-10,
  .collage-item-11,
  .collage-item-12,
  .collage-item-13,
  .collage-item-14,
  .collage-item-15,
  .collage-item-16,
  .collage-item-17,
  .collage-item-18,
  .collage-item-19,
  .collage-item-20,
  .collage-item-21,
  .collage-item-22,
  .collage-item-23,
  .collage-item-24,
  .collage-item-25,
  .collage-item-26 {
    display: none;
  }

  .collage-item-info {
    left: 4%;
    right: 4%;
    bottom: 15%;
  }

  .collage-buttons .btn {
    padding: 8px 16px;
    font-size: 0.85rem;
  }
}
</style>
