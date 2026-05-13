<template>
  <div class="app">
    <!-- Hidden router-view to make vue-router work -->
    <router-view v-slot="{ Component }">
      <component :is="Component" v-show="false" />
    </router-view>

    <!-- Scanning progress debug overlay -->
    <div v-if="activeTasks.length > 0 || !wsConnected" class="scan-debug-overlay">
      <div v-if="!wsConnected" class="scan-debug-item scan-debug-disconnected">
        ⚡ Reconnecting...
      </div>
      <div
        v-for="task in activeTasks"
        :key="task.id"
        class="scan-debug-item"
        :class="{
          'scan-debug-done': task.status === 'completed',
          'scan-debug-error': task.status === 'error',
        }"
      >
        <span class="scan-debug-label">{{ task.id }}</span>
        <span v-if="task.progress > 0" class="scan-debug-progress">
          {{ Math.round(task.progress * 100) }}%
        </span>
        <span v-if="task.detail" class="scan-debug-detail">{{ task.detail }}</span>
        <span class="scan-debug-status">{{ task.status }}</span>
      </div>
    </div>

    <!-- Persistent Header overlay - single instance -->
    <Header
      :current-view="currentView"
      :search-query="searchQuery"
      :nav-row="1"
      :position="headerPosition"
      @search="searchQuery = $event"
      @clear-search="searchQuery = ''"
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
        <button class="btn btn-primary" @click="reloadPage">
          Try Again
        </button>
      </div>
    </main>

    <!-- Detail page (movie or series) -->
    <MediaDetail
      v-else-if="selectedItem"
      :item="selectedItem"
      :focus-episode="focusEpisode"
      @close="closeDetail"
      @play="handlePlay"
      @open-folder="handleOpenFolder"
    />

    <!-- Browse/Search pages -->
    <main v-else class="main-content">
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
              @play="handlePlay"
              @info="showDetail"
              @select="showDetail"
            />
            <!-- Spacer for header overlay -->
            <div class="header-spacer"></div>
            <!-- Movie categories -->
            <template v-for="(category, categoryIndex) in moviesByGenre" :key="category.name">
              <section class="media-section" v-if="category.items.length > 0">
                <h2 class="section-title">
                  {{ category.name }} ({{ category.items.length }})
                </h2>
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
              @play="handlePlay"
              @info="showDetail"
              @select="showDetail"
            />
            <!-- Spacer for header overlay -->
            <div class="header-spacer"></div>
            <!-- Series categories -->
            <template v-for="(category, categoryIndex) in seriesByGenre" :key="category.name">
              <section class="media-section" v-if="category.items.length > 0">
                <h2 class="section-title">
                  {{ category.name }} ({{ category.items.length }})
                </h2>
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
            @play="handlePlay"
            @info="showDetail"
            @select="showDetail"
          />
          <!-- Spacer for header overlay -->
          <div class="header-spacer"></div>
          <!-- Category sections -->
          <template v-for="(category, categoryIndex) in searchCategories" :key="category.name">
            <section class="media-section">
              <h2 class="section-title">
                {{ category.name }} ({{ category.items.length }})
              </h2>
              <MediaRow
                :items="category.items"
                :row-index="categoryIndex + 2"
                @select="showDetail"
              />
            </section>
          </template>
        </template>
        <template v-else>
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
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import type { Movie, Series, MediaItem, EpisodeWithSeries, MatchedPerson, MatchedEpisode, TaskInfo } from './types';
import { playMedia, openFolder } from './api';
import { useKeyboardNavigation } from './composables/useKeyboardNavigation';
import { useMediaWebSocket } from './composables/useMediaWebSocket';
import Header from './components/Header.vue';
import CollageHero from './components/CollageHero.vue';
import MediaRow from './components/MediaRow.vue';
import MediaDetail from './components/MediaDetail.vue';

// Initialize keyboard navigation
const { getFocusState, restoreFocusState, focusAt } = useKeyboardNavigation();

const router = useRouter();
const route = useRoute();

// WebSocket-driven media index
const { mediaIndex, loading, error, connected: wsConnected, tasks } = useMediaWebSocket();

// Active tasks for the debug overlay
const activeTasks = computed<TaskInfo[]>(() => Array.from(tasks.value.values()));

const searchResults = ref<MediaItem[]>([]);
const isSearching = ref(false);

// Focus episode info for navigating to series detail from search
const focusEpisode = ref<{ seasonNumber: number; episodeNumber: number } | null>(null);

// Search result categories
interface SearchCategory {
  name: string;
  items: MediaItem[];
}

interface ScoredMediaItem {
  item: MediaItem;
  score: number;
  matchType: 'movies' | 'series' | 'people' | 'other';
}

const searchCategories = ref<SearchCategory[]>([]);

function requestInitialFullscreen() {
  void document.documentElement.requestFullscreen();
}

// Focus state per page for Escape navigation
const focusStateMap = new Map<string, { row: number; col: number }>();
// Track the last viewed item ID to restore focus to the right card
const lastViewedItemId = ref<string | null>(null);

// Save current focus state for a page
function saveFocusForPage(page: string) {
  const state = getFocusState();
  if (state) {
    focusStateMap.set(page, state);
  }
}

// Restore focus state for a page, or find the last viewed item
function restoreFocusForPage(page: string) {
  // First try to find the last viewed item and focus it
  if (lastViewedItemId.value) {
    // Use nextTick + timeout to ensure DOM is updated after navigation
    setTimeout(() => {
      const itemId = lastViewedItemId.value;
      // Find the element with matching item id
      const element = document.querySelector(`[data-item-id="${itemId}"]`) as HTMLElement | null;
      if (element) {
        element.focus();
        element.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
        lastViewedItemId.value = null;
        return;
      }
      // Fallback to saved focus state
      const state = focusStateMap.get(page);
      restoreFocusState(state || null);
      lastViewedItemId.value = null;
    }, 100);
  } else {
    const state = focusStateMap.get(page);
    restoreFocusState(state || null);
  }
}

// Handle Escape key for navigation hierarchy
function handleEscapeKey(event: KeyboardEvent) {
  if (event.key !== 'Escape') return;

  // Ignore if typing in an input
  const target = event.target as HTMLElement;
  if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) {
    return;
  }

  const path = route.path;

  // From movie detail -> movies list
  if (path.startsWith('/movies/') && route.params.id) {
    event.preventDefault();
    router.push('/movies');
    restoreFocusForPage('movies');
    return;
  }

  // From series detail -> series list
  if (path.startsWith('/series/') && route.params.id) {
    event.preventDefault();
    router.push('/series');
    restoreFocusForPage('series');
    return;
  }

  // From series list -> movies list
  if (path === '/series') {
    event.preventDefault();
    saveFocusForPage('series');
    router.push('/movies');
    restoreFocusForPage('movies');
    return;
  }

  // From search -> go back
  if (path.startsWith('/search')) {
    event.preventDefault();
    router.push('/movies');
    restoreFocusForPage('movies');
    return;
  }

  // From movies list -> do nothing (stop here)
}

onMounted(() => {
  document.addEventListener('keydown', handleEscapeKey);
  window.addEventListener('click', requestInitialFullscreen, { once: true });
});

onUnmounted(() => {
  document.removeEventListener('keydown', handleEscapeKey);
  window.removeEventListener('click', requestInitialFullscreen);
});

// Search query stored in ref (not URL-based)
const searchQuery = ref('');

// Handle back navigation (Escape key or Back button)
function goBack() {
  const path = route.path;

  // From movie detail -> movies list with focus restoration
  if (path.startsWith('/movies/') && route.params.id) {
    router.push('/movies');
    restoreFocusForPage('movies');
    return;
  }

  // From series detail -> series list with focus restoration
  if (path.startsWith('/series/') && route.params.id) {
    router.push('/series');
    restoreFocusForPage('series');
    return;
  }

  // Default: just go back
  router.back();
}

// Derive currentView from route
const currentView = computed(() => {
  return (route.meta.view as 'movies' | 'series') || 'movies';
});

// Header position based on current page
const headerPosition = computed(() => {
  if (selectedItem.value) {
    // Detail pages - position after the header section
    return selectedItem.value.type === 'series' ? 'after-series-hero' : 'after-movie-header';
  }
  if (loading.value || error.value) {
    return 'top';
  }
  // Search page always uses after-hero position for consistent layout
  if (searchQuery.value) {
    return 'after-hero';
  }
  return 'after-hero';
});

// Derive selectedItem from route params
const selectedItem = computed(() => {
  const id = route.params.id as string | undefined;
  if (!id || !mediaIndex.value) return null;

  // Check route path to determine type, not currentView (which could be 'search')
  const path = route.path;
  if (path.startsWith('/movies/')) {
    const movie = mediaIndex.value.movies.find(m => m.id === id);
    return movie ? movieToMediaItem(movie) : null;
  } else if (path.startsWith('/series/')) {
    const series = mediaIndex.value.series.find(s => s.id === id);
    return series ? seriesToMediaItem(series) : null;
  }
  return null;
});

// Show detail by navigating to URL (clears search)
function showDetail(item: MediaItem) {
  // Save the item ID to restore focus when returning
  lastViewedItemId.value = item.id;

  // Save focus state before navigating to detail
  const currentPage = route.path === '/series' ? 'series' : 'movies';
  saveFocusForPage(currentPage);

  // Check if there are matched episodes to focus on
  if (item.type === 'series' && item.searchMatchInfo?.matchedEpisodes?.length) {
    const firstMatch = item.searchMatchInfo.matchedEpisodes[0];
    focusEpisode.value = {
      seasonNumber: firstMatch.seasonNumber,
      episodeNumber: firstMatch.episodeNumber
    };
  } else {
    focusEpisode.value = null;
  }

  if (item.type === 'episode') {
    // For episodes, play directly if possible, otherwise show the series
    const epData = item.data as EpisodeWithSeries;
    const playableFile = Object.values(epData.episode.torrents || {})[0]?.playable_file;
    if (playableFile) {
      handlePlay(playableFile);
    } else {
      router.push(`/series/${epData.series.id}`);
    }
  } else {
    // Push without query to clear search and add to history
    router.push({ path: `/${item.type}/${item.id}` });
  }
}

// Close detail by navigating back to list
function closeDetail() {
  router.push(`/${currentView.value}`);
}

// Convert raw data to MediaItem format
function movieToMediaItem(movie: Movie): MediaItem {
  // Get resolution from first torrent if available
  const torrents = Object.values(movie.torrents || {});
  const resolution = torrents.length > 0 ? torrents[0].resolution : null;

  // Use first showreel image as fallback if no cover
  const coverPath = movie.cover_path ||
    (movie.showreel_images && movie.showreel_images.length > 0 ? movie.showreel_images[0] : null);

  return {
    id: movie.id,
    title: movie.title || 'Unknown',
    year: movie.year,
    cover_path: coverPath,
    showreel_images: movie.showreel_images,
    type: 'movies',
    resolution: resolution,
    data: movie,
  };
}

function seriesToMediaItem(series: Series): MediaItem {
  // For series, collect reel images from all episodes
  const reelImages: string[] = [];
  for (const season of series.seasons || []) {
    for (const episode of season.episodes || []) {
      if (episode.reel_image) {
        reelImages.push(episode.reel_image);
      }
    }
  }

  // Use first reel image as fallback if no cover
  const coverPath = series.cover_path || (reelImages.length > 0 ? reelImages[0] : null);

  return {
    id: series.id,
    title: series.title || 'Unknown',
    year: null,
    cover_path: coverPath,
    showreel_images: reelImages.length > 0 ? reelImages : null,
    type: 'series',
    data: series,
  };
}

// Genre categories for display order (array position determines UI order)
// priority: lower number = higher matching priority (movies assigned to highest priority match)
// exclude: if item has any of these genres, it won't match this category (negative match)
const GENRE_CATEGORIES = [
  { name: 'Action', keywords: ['Action', 'Adventure'], priority: 40, exclude: [] },
  { name: 'Comedy', keywords: ['Comedy'], priority: 30, exclude: ['Drama'] },
  { name: 'Romance', keywords: ['Romance'], priority: 20, exclude: [] },
  { name: 'Drama', keywords: ['Drama'], priority: 50, exclude: [] },
  { name: 'Crime', keywords: ['Crime'], priority: 45, exclude: [] },
  { name: 'Thriller', keywords: ['Thriller', 'Mystery'], priority: 20, exclude: [] },
  { name: 'Horror', keywords: ['Horror'], priority: 10, exclude: [] },
  { name: 'Science Fiction', keywords: ['Science Fiction', 'Sci-Fi'], priority: 15, exclude: [] },
  { name: 'Animation', keywords: ['Animation'], priority: 8, exclude: [] },
  { name: 'Family', keywords: ['Family'], priority: 10, exclude: [] },
  { name: 'Sports', keywords: ['Sports'], priority: 30, exclude: [] },
  { name: 'Documentary', keywords: ['Documentary'], priority: 20, exclude: [] },
  { name: 'War/History', keywords: ['War', 'History'], priority: 30, exclude: ['Fantasy'] },
  { name: 'Music', keywords: ['Music', 'Musical'], priority: 15, exclude: [] },
] as const;

// Categories sorted by priority for matching (lowest priority number first)
const GENRE_CATEGORIES_BY_PRIORITY = [...GENRE_CATEGORIES].sort((a, b) => a.priority - b.priority);

// Helper to sort by rating (highest first)
function sortByRating(items: MediaItem[]): MediaItem[] {
  return [...items].sort((a, b) => {
    let ratingA = 0;
    let ratingB = 0;

    if (a.type === 'episode') {
      const epData = a.data as EpisodeWithSeries;
      ratingA = epData.episode.rating ?? epData.series.info?.rating ?? 0;
    } else {
      ratingA = (a.data as Movie | Series).info?.rating ?? 0;
    }

    if (b.type === 'episode') {
      const epData = b.data as EpisodeWithSeries;
      ratingB = epData.episode.rating ?? epData.series.info?.rating ?? 0;
    } else {
      ratingB = (b.data as Movie | Series).info?.rating ?? 0;
    }

    return ratingB - ratingA;
  });
}

// Categorize movies by genre
const moviesByGenre = computed(() => {
  if (!mediaIndex.value) return [];

  const allMovies = mediaIndex.value.movies.map(movieToMediaItem);
  const assignedIds = new Set<string>();
  const categories: { name: string; items: MediaItem[] }[] = [];

  // Apply search filter
  const searchFilter = (m: MediaItem) => {
    if (!searchQuery.value) return true;
    return (m.title || '').toLowerCase().includes(searchQuery.value.toLowerCase());
  };

  // Assign movies to categories by matching priority (lowest priority number first)
  const categoryMap = new Map<string, MediaItem[]>();
  for (const category of GENRE_CATEGORIES_BY_PRIORITY) {
    for (const movie of allMovies) {
      if (assignedIds.has(movie.id)) continue;
      if (!searchFilter(movie)) continue;

      const movieData = movie.data as Movie;
      const genres = movieData.info?.genres || [];

      // Check if movie matches this category (has keyword and no excluded genres)
      const hasKeyword = category.keywords.some(keyword =>
        genres.some(g => g.toLowerCase().includes(keyword.toLowerCase()))
      );
      const hasExcluded = category.exclude.length > 0 && category.exclude.some(excl =>
        genres.some(g => g.toLowerCase().includes(excl.toLowerCase()))
      );

      if (hasKeyword && !hasExcluded) {
        if (!categoryMap.has(category.name)) {
          categoryMap.set(category.name, []);
        }
        categoryMap.get(category.name)!.push(movie);
        assignedIds.add(movie.id);
      }
    }
  }

  // Build categories in display order (GENRE_CATEGORIES array order)
  for (const category of GENRE_CATEGORIES) {
    const categoryMovies = categoryMap.get(category.name);
    if (categoryMovies && categoryMovies.length > 0) {
      categories.push({
        name: category.name,
        items: sortByRating(categoryMovies),
      });
    }
  }

  // Collect remaining movies into Miscellaneous
  const miscMovies: MediaItem[] = [];
  for (const movie of allMovies) {
    if (assignedIds.has(movie.id)) continue;
    if (!searchFilter(movie)) continue;
    miscMovies.push(movie);
  }

  if (miscMovies.length > 0) {
    categories.push({
      name: 'Miscellaneous',
      items: sortByRating(miscMovies),
    });
  }

  return categories;
});

// Categorize series by genre
const seriesByGenre = computed(() => {
  if (!mediaIndex.value) return [];

  const allSeries = mediaIndex.value.series.map(seriesToMediaItem);
  const assignedIds = new Set<string>();
  const categories: { name: string; items: MediaItem[] }[] = [];

  // Apply search filter
  const searchFilter = (s: MediaItem) => {
    if (!searchQuery.value) return true;
    return (s.title || '').toLowerCase().includes(searchQuery.value.toLowerCase());
  };

  // Assign series to categories by matching priority (lowest priority number first)
  const categoryMap = new Map<string, MediaItem[]>();
  for (const category of GENRE_CATEGORIES_BY_PRIORITY) {
    for (const series of allSeries) {
      if (assignedIds.has(series.id)) continue;
      if (!searchFilter(series)) continue;

      const seriesData = series.data as Series;
      const genres = seriesData.info?.genres || [];

      // Check if series matches this category (has keyword and no excluded genres)
      const hasKeyword = category.keywords.some(keyword =>
        genres.some(g => g.toLowerCase().includes(keyword.toLowerCase()))
      );
      const hasExcluded = category.exclude.length > 0 && category.exclude.some(excl =>
        genres.some(g => g.toLowerCase().includes(excl.toLowerCase()))
      );

      if (hasKeyword && !hasExcluded) {
        if (!categoryMap.has(category.name)) {
          categoryMap.set(category.name, []);
        }
        categoryMap.get(category.name)!.push(series);
        assignedIds.add(series.id);
      }
    }
  }

  // Build categories in display order (GENRE_CATEGORIES array order)
  for (const category of GENRE_CATEGORIES) {
    const categorySeries = categoryMap.get(category.name);
    if (categorySeries && categorySeries.length > 0) {
      categories.push({
        name: category.name,
        items: sortByRating(categorySeries),
      });
    }
  }

  // Collect remaining series into Miscellaneous
  const miscSeries: MediaItem[] = [];
  for (const series of allSeries) {
    if (assignedIds.has(series.id)) continue;
    if (!searchFilter(series)) continue;
    miscSeries.push(series);
  }

  if (miscSeries.length > 0) {
    categories.push({
      name: 'Miscellaneous',
      items: sortByRating(miscSeries),
    });
  }

  return categories;
});

// Calculate relevance score for a match
// Higher score = more relevant (beginning of name > word boundary > mid-word)
function getRelevanceScore(query: string, field: string): number {
  const lowerField = field.toLowerCase();
  const lowerQuery = query.toLowerCase();

  if (!lowerField.includes(lowerQuery)) return 0;

  const index = lowerField.indexOf(lowerQuery);

  // Exact match at start of string - highest score
  if (index === 0) return 100;

  // Match at word boundary (after space, hyphen, colon, etc.)
  const charBefore = lowerField[index - 1];
  if (/[\s\-:_.,;()\[\]]/.test(charBefore)) return 80;

  // Match in the first half of the string
  if (index < lowerField.length / 2) return 50;

  // Match anywhere else
  return 30;
}

// Get best relevance score from multiple fields
function getBestScore(query: string, ...fields: (string | null | undefined)[]): number {
  let bestScore = 0;
  for (const field of fields) {
    if (field) {
      const score = getRelevanceScore(query, field);
      if (score > bestScore) bestScore = score;
    }
  }
  return bestScore;
}

// Check if any person name matches the query - returns matched people with roles
interface PersonMatch {
  name: string;
  roles: string[];
  highlightRoles: boolean;  // true if character name matched (vs actor name)
}

function matchesPeople(query: string, cast: { name: string; character?: string | null }[] | null | undefined, director?: string | null, creators?: string[] | null): { matches: PersonMatch[]; score: number } {
  const matchedPeople: PersonMatch[] = [];
  let bestScore = 0;

  // Check director
  if (director) {
    const score = getRelevanceScore(query, director);
    if (score > 0) {
      matchedPeople.push({ name: director, roles: ['Director'], highlightRoles: false });
      if (score > bestScore) bestScore = score;
    }
  }

  // Check creators
  if (creators) {
    for (const creator of creators) {
      const score = getRelevanceScore(query, creator);
      if (score > 0) {
        const existing = matchedPeople.find(p => p.name.toLowerCase() === creator.toLowerCase());
        if (existing) {
          if (!existing.roles.includes('Creator')) existing.roles.push('Creator');
        } else {
          matchedPeople.push({ name: creator, roles: ['Creator'], highlightRoles: false });
        }
        if (score > bestScore) bestScore = score;
      }
    }
  }

  // Check cast - match on actor name or character name
  if (cast) {
    for (const person of cast) {
      const nameScore = getRelevanceScore(query, person.name);
      const characterScore = person.character ? getRelevanceScore(query, person.character) : 0;
      const bestPersonScore = Math.max(nameScore, characterScore);

      if (bestPersonScore > 0) {
        const role = person.character || 'Cast';
        const highlightRoles = characterScore > nameScore;  // Highlight character if that's what matched
        const existing = matchedPeople.find(p => p.name.toLowerCase() === person.name.toLowerCase());
        if (existing) {
          if (!existing.roles.includes(role)) existing.roles.push(role);
          // Update highlight if character matched better
          if (highlightRoles) existing.highlightRoles = true;
        } else {
          matchedPeople.push({ name: person.name, roles: [role], highlightRoles });
        }
        if (bestPersonScore > bestScore) bestScore = bestPersonScore;
      }
    }
  }

  return { matches: matchedPeople, score: bestScore };
}

// Format matched people - returns array of MatchedPerson for display
function formatMatchedPeople(people: PersonMatch[]): MatchedPerson[] {
  return people.map(p => ({
    name: p.name,
    roles: p.roles.join(', '),
    highlightRoles: p.highlightRoles
  }));
}

// Debounced search with limit
let searchTimeout: ReturnType<typeof setTimeout> | null = null;
const MAX_RESULTS = 100;

// Watch for detail page entry/exit to manage focus
watch(selectedItem, (item, oldItem) => {
  if (item && !oldItem) {
    // Skip auto-focus if we have a specific episode to focus on (from search)
    if (item.type === 'series' && focusEpisode.value) {
      return;
    }
    // Entering detail page - focus Play button (row 2, col 0) after transition
    focusAt(2, 0, 150);
  } else if (!item && oldItem) {
    // Leaving detail page (browser back, Escape, etc.) - restore focus to the item card
    const page = currentView.value === 'series' ? 'series' : 'movies';
    restoreFocusForPage(page);
  }
});

// Focus search input by default on initial movies page load
let initialFocusDone = false;
watch([mediaIndex, currentView, searchQuery, selectedItem], ([index, view, query, item]) => {
  if (!initialFocusDone && index && view === 'movies' && !query && !item) {
    initialFocusDone = true;
    // Focus search input on first movies page load
    setTimeout(() => {
      const searchInput = document.querySelector('.search-input') as HTMLInputElement;
      if (searchInput) {
        searchInput.focus();
      }
    }, 100);
  }
});

watch(searchQuery, (query) => {
  if (searchTimeout) {
    clearTimeout(searchTimeout);
  }

  if (!query || !mediaIndex.value) {
    searchResults.value = [];
    searchCategories.value = [];
    isSearching.value = false;
    return;
  }

  isSearching.value = true;

  // Debounce search by 50ms
  searchTimeout = setTimeout(() => {
    performSearch(query.toLowerCase());
  }, 50);
});

function performSearch(query: string) {
  if (!mediaIndex.value) return;

  const allScored: ScoredMediaItem[] = [];
  const processedIds = new Set<string>();

  // Check if query is a year (4 digits, reasonable range)
  const yearMatch = query.match(/^(\d{4})$/);
  const searchYear = yearMatch ? parseInt(yearMatch[1], 10) : null;
  const isYearSearch = searchYear !== null && searchYear >= 1900 && searchYear <= 2100;

  // Search movies
  for (const movie of mediaIndex.value.movies) {
    // Year search - match movies from that year
    if (isYearSearch) {
      if (movie.year === searchYear) {
        allScored.push({
          item: movieToMediaItem(movie),
          score: 100 + (movie.info?.rating ?? 0) / 10,  // High base score, ranked by rating
          matchType: 'movies'
        });
        processedIds.add(movie.id);
      }
      continue;
    }

    // Direct title match -> Movies category
    const titleScore = getBestScore(query, movie.title, movie.info?.original_title);
    if (titleScore > 0) {
      allScored.push({
        item: movieToMediaItem(movie),
        score: titleScore + (movie.info?.rating ?? 0) / 10,
        matchType: 'movies'
      });
      processedIds.add(movie.id);
      continue;
    }

    // Cast/director match -> People category
    const peopleMatch = matchesPeople(query, movie.info?.cast, movie.info?.director);
    if (peopleMatch.matches.length > 0) {
      const item = movieToMediaItem(movie);
      item.searchMatchInfo = { matchedPeople: formatMatchedPeople(peopleMatch.matches) };
      allScored.push({
        item,
        score: peopleMatch.score + (movie.info?.rating ?? 0) / 10,
        matchType: 'people'
      });
      processedIds.add(movie.id);
      continue;
    }

    // Other metadata matches -> Other category
    const otherScore = getBestScore(query,
      movie.info?.genres?.join(' '),
      movie.info?.keywords?.join(' '),
      movie.info?.overview,
      movie.info?.tagline,
      movie.info?.similar?.map(s => s.title).join(' ')
    );
    if (otherScore > 0) {
      allScored.push({
        item: movieToMediaItem(movie),
        score: otherScore + (movie.info?.rating ?? 0) / 10,
        matchType: 'other'
      });
      processedIds.add(movie.id);
    }
  }

  // Search series
  for (const series of mediaIndex.value.series) {
    // Year search - match series that started that year
    if (isYearSearch) {
      // Extract year from release_date (format: "YYYY-MM-DD" or just "YYYY")
      const seriesYear = series.info?.release_date ? parseInt(series.info.release_date.substring(0, 4), 10) : null;
      if (seriesYear === searchYear) {
        allScored.push({
          item: seriesToMediaItem(series),
          score: 100 + (series.info?.rating ?? 0) / 10,
          matchType: 'series'
        });
        processedIds.add(series.id);
      }
      continue;
    }

    // Direct title match -> Series category
    const titleScore = getBestScore(query, series.title, series.info?.original_title);
    if (titleScore > 0) {
      allScored.push({
        item: seriesToMediaItem(series),
        score: titleScore + (series.info?.rating ?? 0) / 10,
        matchType: 'series'
      });
      processedIds.add(series.id);
      continue;
    }

    // Check episode name matches -> Series category (show the series with matched episodes)
    const matchedEpisodes: MatchedEpisode[] = [];
    let episodeScore = 0;
    // Check if series has only one season and has ended (hide "SN" in that case)
    const isEndedSingleSeason = (series.info?.number_of_seasons === 1 || (series.seasons?.length === 1)) &&
      ['Ended', 'Canceled', 'Cancelled'].includes(series.info?.status || '');

    for (const season of series.seasons || []) {
      for (const episode of season.episodes || []) {
        if (episode.name) {
          const epScore = getRelevanceScore(query, episode.name);
          if (epScore > 0) {
            // Hide season for: single-season ended series OR Season 0 (specials)
            const hideSeason = isEndedSingleSeason || season.season_number === 0;
            const location = hideSeason
              ? `Episode ${episode.episode_number}`
              : `S${season.season_number} Episode ${episode.episode_number}`;
            matchedEpisodes.push({
              name: episode.name,
              location,
              seasonNumber: season.season_number,
              episodeNumber: episode.episode_number
            });
            if (epScore > episodeScore) episodeScore = epScore;
          }
        }
      }
    }
    if (matchedEpisodes.length > 0 && !processedIds.has(series.id)) {
      const item = seriesToMediaItem(series);
      item.searchMatchInfo = { matchedEpisodes };
      allScored.push({
        item,
        score: episodeScore + (series.info?.rating ?? 0) / 10,
        matchType: 'series'
      });
      processedIds.add(series.id);
      continue;
    }

    // Cast/creators match -> People category
    const peopleMatch = matchesPeople(query, series.info?.cast, null, series.info?.creators);
    if (peopleMatch.matches.length > 0 && !processedIds.has(series.id)) {
      const item = seriesToMediaItem(series);
      item.searchMatchInfo = { matchedPeople: formatMatchedPeople(peopleMatch.matches) };
      allScored.push({
        item,
        score: peopleMatch.score + (series.info?.rating ?? 0) / 10,
        matchType: 'people'
      });
      processedIds.add(series.id);
      continue;
    }

    // Other metadata matches -> Other category
    if (!processedIds.has(series.id)) {
      const otherScore = getBestScore(query,
        series.info?.genres?.join(' '),
        series.info?.keywords?.join(' '),
        series.info?.overview,
        series.info?.tagline,
        series.info?.similar?.map(s => s.title).join(' '),
        series.info?.networks?.join(' ')
      );
      if (otherScore > 0) {
        allScored.push({
          item: seriesToMediaItem(series),
          score: otherScore + (series.info?.rating ?? 0) / 10,
          matchType: 'other'
        });
        processedIds.add(series.id);
      }
    }
  }

  // Sort all results by score (descending)
  allScored.sort((a, b) => b.score - a.score);

  // Take top results and deduplicate
  const topResults = allScored.slice(0, MAX_RESULTS);

  // Build categories from the scored results
  const moviesCat: MediaItem[] = [];
  const seriesCat: MediaItem[] = [];
  const peopleCat: MediaItem[] = [];
  const otherCat: MediaItem[] = [];

  for (const scored of topResults) {
    switch (scored.matchType) {
      case 'movies':
        moviesCat.push(scored.item);
        break;
      case 'series':
        seriesCat.push(scored.item);
        break;
      case 'people':
        peopleCat.push(scored.item);
        break;
      case 'other':
        otherCat.push(scored.item);
        break;
    }
  }

  // Build categories array (only include non-empty)
  const categories: SearchCategory[] = [];
  if (moviesCat.length > 0) categories.push({ name: 'Movies', items: moviesCat });
  if (seriesCat.length > 0) categories.push({ name: 'Series', items: seriesCat });
  if (peopleCat.length > 0) categories.push({ name: 'People', items: peopleCat });
  if (otherCat.length > 0) categories.push({ name: 'Other', items: otherCat });

  searchCategories.value = categories;

  // All results ranked by relevance for the hero
  searchResults.value = topResults.map(s => s.item);

  isSearching.value = false;
}

// Sort by newest timestamp (descending)
function sortByNewest(items: MediaItem[]): MediaItem[] {
  return [...items].sort((a, b) => {
    const aNewest = (a.data as Movie | Series).newest ?? 0;
    const bNewest = (b.data as Movie | Series).newest ?? 0;
    return bNewest - aNewest;
  });
}

// Newest items from all genre categories for showcase
const newestMovies = computed(() => {
  const allItems: MediaItem[] = [];
  for (const category of moviesByGenre.value) {
    allItems.push(...category.items);
  }
  return sortByNewest(allItems).slice(0, 20);
});

const newestSeries = computed(() => {
  const allItems: MediaItem[] = [];
  for (const category of seriesByGenre.value) {
    allItems.push(...category.items);
  }
  return sortByNewest(allItems).slice(0, 20);
});

// Items for the collage hero - separate for movies and series to enable smooth transitions
const movieCollageItems = computed(() => {
  const items = newestMovies.value;
  const withCovers = items.filter(m => m.cover_path);
  const withoutCovers = items.filter(m => !m.cover_path);
  return [...withCovers, ...withoutCovers].slice(0, 100);
});

const seriesCollageItems = computed(() => {
  const items = newestSeries.value;
  const withCovers = items.filter(m => m.cover_path);
  const withoutCovers = items.filter(m => !m.cover_path);
  return [...withCovers, ...withoutCovers].slice(0, 100);
});

// Featured items for hero - separate for movies and series
const movieFeaturedItem = computed(() => {
  const withCovers = newestMovies.value.filter(m => m.cover_path);
  return withCovers[0] || newestMovies.value[0] || null;
});

const seriesFeaturedItem = computed(() => {
  const withCovers = newestSeries.value.filter(s => s.cover_path);
  return withCovers[0] || newestSeries.value[0] || null;
});

// Search results collage items (already ranked by relevance)
const searchCollageItems = computed(() => {
  const items = searchResults.value;
  const withCovers = items.filter(m => m.cover_path);
  const withoutCovers = items.filter(m => !m.cover_path);
  return [...withCovers, ...withoutCovers].slice(0, 100);
});

// Search featured item (highest relevance with cover)
const searchFeaturedItem = computed(() => {
  const withCovers = searchResults.value.filter(m => m.cover_path);
  return withCovers[0] || searchResults.value[0] || null;
});

function reloadPage() {
  window.location.reload();
}

async function handlePlay(filePath: string) {
  try {
    await playMedia(filePath);
  } catch (e) {
    console.error('Failed to play media:', e);
  }
}

async function handleOpenFolder(folderPath: string) {
  try {
    await openFolder(folderPath);
  } catch (e) {
    console.error('Failed to open folder:', e);
  }
}


</script>

<style scoped>
.app {
  min-height: 100vh;
}

/* Scanning progress debug overlay */
.scan-debug-overlay {
  position: fixed;
  bottom: 12px;
  right: 12px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 11px;
  max-width: 380px;
  pointer-events: none;
}

.scan-debug-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  background: rgba(0, 0, 0, 0.82);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 6px;
  color: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(8px);
  white-space: nowrap;
  overflow: hidden;
}

.scan-debug-disconnected {
  color: #f59e0b;
  border-color: rgba(245, 158, 11, 0.3);
}

.scan-debug-done {
  color: #34d399;
  border-color: rgba(52, 211, 153, 0.3);
}

.scan-debug-error {
  color: #f87171;
  border-color: rgba(248, 113, 113, 0.3);
}

.scan-debug-label {
  color: rgba(255, 255, 255, 0.5);
  flex-shrink: 0;
}

.scan-debug-progress {
  color: #60a5fa;
  font-weight: 600;
  flex-shrink: 0;
}

.scan-debug-detail {
  color: rgba(255, 255, 255, 0.55);
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.scan-debug-status {
  color: rgba(255, 255, 255, 0.35);
  flex-shrink: 0;
  margin-left: auto;
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
  padding: 60px 20px;
  color: var(--text-secondary);
  font-size: 1.1rem;
}
</style>
