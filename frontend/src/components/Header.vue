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

    <div v-if="mpcBeConnected" class="player-indicator" title="MPC-BE is connected">
      <span class="player-indicator-dot" aria-hidden="true"></span>
      <span>Player Open</span>
    </div>

    <div class="header-settings">
      <button
        class="header-settings-btn"
        title="Manage media roots"
        @click="showRootsPanel = !showRootsPanel"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
        </svg>
      </button>

      <!-- Roots management dropdown -->
      <div v-if="showRootsPanel" class="roots-panel">
        <div class="roots-panel-header">
          <span class="roots-panel-title">Media Roots</span>
          <button class="roots-panel-close" @click="showRootsPanel = false">×</button>
        </div>
        <div class="roots-list">
          <div
            v-for="root in roots"
            :key="root.root_id"
            class="roots-item"
            :class="`roots-item--${root.status}`"
          >
            <div class="roots-item-info">
              <span class="roots-item-name">{{ root.name }}</span>
              <span class="roots-item-path">{{ root.path }}</span>
            </div>
            <div class="roots-item-meta">
              <span class="roots-item-status">{{ root.status }}</span>
              <button
                v-if="roots.length > 1"
                class="roots-item-remove"
                @click="removeRoot(root.root_id)"
                title="Remove root"
              >
                ×
              </button>
            </div>
          </div>
        </div>
        <div class="roots-actions">
          <button
            v-if="isDesktopApp"
            class="roots-add-btn"
            @click="addRoot"
          >
            + Add Folder…
          </button>
        </div>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { ref, watch, computed, onMounted, onUnmounted } from 'vue';
import { useRouter } from 'vue-router';
import { navAttrs } from '../composables/useKeyboardNavigation';
import logoUrl from '../assets/mediahive.webp';
import { fetchRoots, replaceRoots, pickFolderAndAddRoot } from '../api';

interface RootEntry {
  root_id: string;
  name: string;
  path: string;
  status: string;
}

const props = defineProps<{
  currentView: 'movies' | 'series';
  searchQuery: string;
  mpcBeConnected: boolean;
  navRow: number;
  position: 'top' | 'after-hero' | 'after-movie-header' | 'after-series-hero';
}>();

const emit = defineEmits<{
  search: [string];
  goBack: [];
}>();

const router = useRouter();
const searchInputRef = ref<HTMLInputElement | null>(null);
const localSearch = ref(props.searchQuery);

const isDesktopApp = ref(typeof (window as any).pywebview !== 'undefined');
function _onPywebviewReady() { isDesktopApp.value = true; }
window.addEventListener('pywebviewready', _onPywebviewReady, { once: true });
onUnmounted(() => window.removeEventListener('pywebviewready', _onPywebviewReady));

const showRootsPanel = ref(false);
const roots = ref<RootEntry[]>([]);

async function refreshRoots() {
  try {
    const data = await fetchRoots();
    roots.value = data.map(r => ({
      root_id: r.root_id,
      name: r.path.split('/').pop() || r.path.split('\\').pop() || r.root_id,
      path: r.path,
      status: r.status,
    }));
  } catch (e) {
    console.error('Failed to fetch roots:', e);
  }
}

async function removeRoot(rootId: string) {
  const filtered = roots.value.filter(r => r.root_id !== rootId);
  const newRoots = Object.fromEntries(filtered.map(r => [r.name, r.path]));
  try {
    await replaceRoots(newRoots);
    await refreshRoots();
  } catch (e) {
    console.error('Failed to remove root:', e);
    alert('Failed to remove root');
  }
}

async function addRoot() {
  const folder = await pickFolderAndAddRoot();
  if (!folder) return;
  const name = folder.split('/').pop() || folder.split('\\').pop() || 'media';
  // Resolve name collisions
  let uniqueName = name;
  let suffix = 2;
  const currentNames = new Set(roots.value.map(r => r.name));
  while (currentNames.has(uniqueName)) {
    uniqueName = `${name}${suffix}`;
    suffix++;
  }
  const newRoots = Object.fromEntries(roots.value.map(r => [r.name, r.path]));
  newRoots[uniqueName] = folder;
  try {
    await replaceRoots(newRoots);
    await refreshRoots();
    showRootsPanel.value = false;
  } catch (e) {
    console.error('Failed to add root:', e);
    alert('Failed to add root');
  }
}

watch(showRootsPanel, (visible) => {
  if (visible) void refreshRoots();
});

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
    router.push('/movies');
  }
}

function switchToSeries() {
  if (!isDetailPage.value && props.currentView !== 'series') {
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
  // Intentionally no-op: focusing search should not navigate away from detail.
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

function focusSearchInput() {
  searchInputRef.value?.focus();
  searchInputRef.value?.select();
}

function handleKeydown(e: KeyboardEvent) {
  const target = e.target as HTMLElement | null;
  const isTypingTarget = Boolean(
    target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)
  );
  const isSearchShortcut = (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f';
  const isSlashShortcut = !e.ctrlKey && !e.metaKey && !e.altKey && e.code === 'Slash';

  if (isTypingTarget && !isSearchShortcut) {
    return;
  }

  if (isSearchShortcut || isSlashShortcut) {
    e.preventDefault();
    focusSearchInput();
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleKeydown);
});

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown);
});
</script>

<style scoped>
.player-indicator {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-right: 10px;
  font-size: 0.82rem;
  color: var(--text-secondary);
}

.player-indicator-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #22c55e;
  box-shadow: 0 0 0 4px rgba(34, 197, 94, 0.18);
}

.header-settings {
  position: relative;
}

.roots-panel {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  width: 320px;
  background: rgba(20, 20, 20, 0.95);
  backdrop-filter: blur(16px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  padding: 16px;
  z-index: 1000;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
}

.roots-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.roots-panel-title {
  font-weight: 600;
  font-size: 0.95rem;
}

.roots-panel-close {
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 1.2rem;
  cursor: pointer;
  padding: 0 4px;
}

.roots-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 240px;
  overflow-y: auto;
}

.roots-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 8px;
  gap: 8px;
}

.roots-item--ready {
  border-left: 3px solid #22c55e;
}

.roots-item--scanning {
  border-left: 3px solid #f59e0b;
}

.roots-item--loading {
  border-left: 3px solid #3b82f6;
}

.roots-item--error {
  border-left: 3px solid #ef4444;
}

.roots-item-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.roots-item-name {
  font-size: 0.85rem;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.roots-item-path {
  font-size: 0.75rem;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.roots-item-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.roots-item-status {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
}

.roots-item-remove {
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 1rem;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
}

.roots-item-remove:hover {
  color: #ef4444;
}

.roots-actions {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}

.roots-add-btn {
  width: 100%;
  padding: 8px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px dashed rgba(255, 255, 255, 0.2);
  border-radius: 8px;
  color: var(--text-primary);
  font-size: 0.85rem;
  cursor: pointer;
  transition: background 0.2s;
}

.roots-add-btn:hover {
  background: rgba(255, 255, 255, 0.15);
}
</style>
