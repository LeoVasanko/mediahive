<template>
  <header class="header" :class="[`header-${position}`]">
    <div class="header-left">
      <img :src="logoUrl" alt="MediaHive" class="header-logo" />
      <nav class="header-nav">
        <!-- Browse mode: show both Movies and Series -->
        <template v-if="!isDetailPage">
          <button
            class="header-nav-item"
            :class="{ active: !isSearchActive && currentView === 'movies' }"
            v-bind="navAttrs(navRow, 0)"
            :data-nav-entry-col="!isSearchActive && currentView === 'movies' ? 0 : undefined"
            @focus="switchToMovies"
          >
            Movies
          </button>
          <button
            class="header-nav-item"
            :class="{ active: !isSearchActive && currentView === 'series' }"
            v-bind="navAttrs(navRow, 1)"
            :data-nav-entry-col="!isSearchActive && currentView === 'series' ? 1 : undefined"
            @focus="switchToSeries"
          >
            Series
          </button>
        </template>
        <!-- Detail mode: show current category + Details -->
        <template v-else>
          <button
            class="header-nav-item"
            v-bind="navAttrs(navRow, 0)"
            @focus="goToCategory"
          >
            {{ currentView === 'movies' ? 'Movies' : 'Series' }}
          </button>
          <button
            class="header-nav-item active"
            v-bind="navAttrs(navRow, 1, 1)"
          >
            Details
          </button>
        </template>
      </nav>
    </div>

    <div class="header-search">
      <input
        ref="searchInputRef"
        type="search"
        class="search-input"
        placeholder="Search..."
        v-model="localSearch"
        v-bind="navAttrs(navRow, 2)"
        :data-nav-entry-col="localSearch ? 2 : undefined"
        @focus="handleSearchFocus"
        @keydown.escape="handleEscape"
      />
    </div>

    <div v-if="isDesktopApp" class="header-settings">
      <button
        class="header-settings-btn"
        title="Change media folder"
        @click="changeFolder"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
        </svg>
      </button>
    </div>
  </header>
</template>

<script setup lang="ts">
import { ref, watch, computed, onMounted, onUnmounted } from 'vue';
import { useRouter } from 'vue-router';
import { navAttrs } from '../composables/useKeyboardNavigation';
import logoUrl from '../assets/mediahive.webp';
import { pickFolderAndRestart } from '../api';

const props = defineProps<{
  currentView: 'movies' | 'series';
  searchQuery: string;
  navRow: number;
  position: 'top' | 'after-hero' | 'after-movie-header' | 'after-series-hero';
}>();

const emit = defineEmits<{
  search: [string];
  clearSearch: [];
  goBack: [];
}>();

const router = useRouter();
const searchInputRef = ref<HTMLInputElement | null>(null);
const localSearch = ref(props.searchQuery);

// True only when running inside the packaged pywebview desktop app.
// pywebview injects window.pywebview asynchronously, so we listen for the
// 'pywebviewready' event rather than checking at component creation time.
const isDesktopApp = ref(typeof (window as any).pywebview !== 'undefined');
function _onPywebviewReady() { isDesktopApp.value = true; }
window.addEventListener('pywebviewready', _onPywebviewReady, { once: true });
onUnmounted(() => window.removeEventListener('pywebviewready', _onPywebviewReady));

async function changeFolder() {
  await pickFolderAndRestart();
}

// Check if we're on a detail page
const isDetailPage = computed(() => {
  return props.position === 'after-movie-header' || props.position === 'after-series-hero';
});

// Check if search is active (has query and not on detail page)
const isSearchActive = computed(() => {
  return !isDetailPage.value && !!localSearch.value;
});

// Switch views on focus (no Enter required) - only in browse mode
function switchToMovies() {
  if (!isDetailPage.value && props.currentView !== 'movies') {
    emit('clearSearch');
    router.push('/movies');
  }
}

function switchToSeries() {
  if (!isDetailPage.value && props.currentView !== 'series') {
    emit('clearSearch');
    router.push('/series');
  }
}

// Go back to category list from detail page
function goToCategory() {
  // Emit goBack to let App.vue handle navigation and focus restoration
  emit('goBack');
}

// Handle search input focus - navigate to search if we have a query
function handleSearchFocus() {
  // If on detail page, go back to browse first
  if (isDetailPage.value) {
    goToCategory();
  }
}

// Sync local search to parent
watch(localSearch, (val) => {
  emit('search', val);
});

// Sync parent search to local (for external clears)
watch(() => props.searchQuery, (val) => {
  if (val !== localSearch.value) {
    localSearch.value = val;
  }
});

function handleEscape() {
  // Clear search and blur
  localSearch.value = '';
  searchInputRef.value?.blur();
}

function handleKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
    e.preventDefault();
    searchInputRef.value?.focus();
    searchInputRef.value?.select();
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleKeydown);
});

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown);
});
</script>
