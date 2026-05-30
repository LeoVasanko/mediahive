<template>
  <header class="header" :class="[`header-${position}`]">
    <div class="header-left">
      <RouterLink to="/" class="header-logo-link" aria-label="Go to front page">
        <img :src="logoUrl" alt="MediaHive" class="header-logo" />
      </RouterLink>
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
          <button class="header-nav-item" v-bind="navAttrs(navRow, 0)" @focus="goToCategory">
            {{ currentView === "search" ? "Search" : currentView === "movies" ? "Movies" : "Series" }}
          </button>
          <button class="header-nav-item active" v-bind="navAttrs(navRow, 1, 1)">Details</button>
        </template>
      </nav>
    </div>

    <div class="header-search">
      <input
        ref="searchInputRef"
        type="search"
        class="search-input"
        placeholder="Search..."
        :spellcheck="false"
        autocorrect="off"
        autocapitalize="off"
        autocomplete="off"
        v-model="localSearch"
        v-bind="navAttrs(navRow, 2)"
        :data-nav-entry-col="localSearch ? 2 : undefined"
        @focus="handleSearchFocus"
        @keydown.escape="handleEscape"
      />
      <HexKeyboard
        v-model="localSearch"
        :visible="hexKeyboardVisible"
        :search-ref="searchInputRef"
        @close="hexKeyboardVisible = false"
        @submit="hexKeyboardVisible = false"
      />
    </div>

    <div v-if="mpcBeConnected" class="player-indicator" title="MPC-BE is connected">
      <span class="player-indicator-dot" aria-hidden="true"></span>
      <span>Player Open</span>
    </div>

    <div class="header-settings">
      <button class="header-settings-btn" title="Settings" @click="openSettings">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <circle cx="12" cy="12" r="3" />
          <path
            d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 5 15.4 1.65 1.65 0 0 0 3.4 15H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"
          />
        </svg>
      </button>

      <!-- Full-screen settings view -->
      <div v-if="showSettings" class="settings-view">
        <div class="settings-header">
          <button class="settings-back" @click="closeSettings">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
            <span>Back</span>
          </button>
          <h1 class="settings-title">Settings</h1>
          <div class="settings-header-spacer"></div>
        </div>

        <div class="settings-content">
          <section class="settings-section">
            <h2 class="settings-section-title">Media Roots</h2>
            <p class="settings-section-desc">Folders scanned and indexed by MediaHive.</p>

            <div class="roots-list">
              <div
                v-for="root in roots"
                :key="root.root_id"
                class="roots-item"
                :class="`roots-item--${root.status}`"
              >
                <div class="roots-item-info">
                  <span class="roots-item-name">{{ root.root_id }}</span>
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
              <button v-if="isDesktopApp" class="roots-add-btn" @click="addRoot">
                + Add Folder…
              </button>
            </div>
          </section>

          <section class="settings-section">
            <h2 class="settings-section-title">Player</h2>
            <p class="settings-section-desc">Choose which media player to launch files with.</p>

            <div class="player-layout">
              <div class="player-list">
                <label
                  v-for="player in detectedPlayers"
                  :key="player.id"
                  class="player-radio-label"
                >
                  <input
                    class="player-radio"
                    type="radio"
                    name="player-selection"
                    :checked="settings.playerId === player.id"
                    @change="setPlayer(player.id)"
                  />
                  {{ player.name }}
                </label>
              </div>

              <div class="player-options">
                <template v-if="settings.playerId === 'custom'">
                  <div class="player-option-group">
                    <label class="player-option-label">Custom Command</label>
                    <input
                      class="player-option-input"
                      type="text"
                      placeholder='C:\Player\player.exe "%s"'
                      v-model="customCmd"
                      @change="setCustomCmd(customCmd)"
                    />
                    <p class="player-option-hint">Use %s as placeholder for the file path.</p>
                  </div>
                </template>

                <template v-if="selectedPlayerFamily === 'mpc'">
                  <div class="player-option-group">
                    <label class="player-option-label" for="mpc-port">MPC Web UI Port</label>
                    <input
                      id="mpc-port"
                      class="player-option-input player-option-input--short"
                      type="number"
                      placeholder="13579"
                      :value="settings.playerMpcPort ?? ''"
                      @input="onMpcPortInput"
                    />
                    <p class="player-option-hint">
                      Port for MPC-BE/HC web interface. Leave empty to disable remote control.
                    </p>
                    <p v-if="settings.playerMpcPort !== null" class="player-family-note">
                      Web remote control enabled
                    </p>
                  </div>
                </template>
              </div>
            </div>
          </section>

          <section class="settings-section">
            <h2 class="settings-section-title">Preferred Format</h2>
            <p class="settings-section-desc">Preferred format when multiple versions are available.</p>

            <div class="format-grid">
              <div class="format-row format-row-stack">
                <label class="format-radio-label" for="resolution-hd">
                  <input
                    id="resolution-hd"
                    class="format-radio"
                    type="radio"
                    name="resolution-preference"
                    :checked="settings.preferredResolution === 'r2'"
                    @change="setPreferredResolution('r2')"
                  />
                  HD or lower
                </label>
                <label class="format-radio-label" for="resolution-fhd">
                  <input
                    id="resolution-fhd"
                    class="format-radio"
                    type="radio"
                    name="resolution-preference"
                    :checked="settings.preferredResolution === 'r3'"
                    @change="setPreferredResolution('r3')"
                  />
                  Full HD
                </label>
                <label class="format-radio-label" for="resolution-4k">
                  <input
                    id="resolution-4k"
                    class="format-radio"
                    type="radio"
                    name="resolution-preference"
                    :checked="settings.preferredResolution === 'r4'"
                    @change="setPreferredResolution('r4')"
                  />
                  4K
                </label>
                <label class="format-radio-label" for="resolution-highest">
                  <input
                    id="resolution-highest"
                    class="format-radio"
                    type="radio"
                    name="resolution-preference"
                    :checked="settings.preferredResolution === 'rmax'"
                    @change="setPreferredResolution('rmax')"
                  />
                  Highest
                </label>
              </div>

              <div class="format-row format-row-stack">
                <label class="format-radio-label" for="hdr-none">
                  <input
                    id="hdr-none"
                    class="format-radio"
                    type="radio"
                    name="hdr-preference"
                    :checked="settings.preferredHdr === 'none'"
                    @change="setPreferredHdr('none')"
                  />
                  No HDR
                </label>
                <label class="format-radio-label" for="hdr-hdr10plus">
                  <input
                    id="hdr-hdr10plus"
                    class="format-radio"
                    type="radio"
                    name="hdr-preference"
                    :checked="settings.preferredHdr === 'hdr10plus'"
                    @change="setPreferredHdr('hdr10plus')"
                  />
                  HDR10+
                </label>
                <label class="format-radio-label" for="hdr-dovi">
                  <input
                    id="hdr-dovi"
                    class="format-radio"
                    type="radio"
                    name="hdr-preference"
                    :checked="settings.preferredHdr === 'dovi'"
                    @change="setPreferredHdr('dovi')"
                  />
                  Dolby Vision
                </label>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { ref, watch, computed, onMounted, onUnmounted } from "vue"
import { useRouter, useRoute } from "vue-router"
import { navAttrs } from "../composables/useKeyboardNavigation"
import logoUrl from "../assets/mediahive.webp"
import { fetchRoots, replaceRoots, pickFolderAndAddRoot, fetchPlayers } from "../api"
import type { PlayerInfo } from "../api"
import HexKeyboard from "./HexKeyboard.vue"
import {
  useSettings,
  type ResolutionPreference,
  type HdrPreference,
} from "../composables/useSettings"

const settings = useSettings()

const detectedPlayers = ref<PlayerInfo[]>([])
const customCmd = ref(settings.playerCustomCmd || "")

const selectedPlayerFamily = computed(() => {
  const p = detectedPlayers.value.find((p) => p.id === settings.playerId)
  return p?.family ?? "default"
})

interface RootEntry {
  root_id: string
  path: string
  status: string
}

const props = defineProps<{
  currentView: "movies" | "series" | "search"
  searchQuery: string
  mpcBeConnected: boolean
  navRow: number
  position: "top" | "after-hero" | "after-movie-header" | "after-series-hero"
}>()

const emit = defineEmits<{
  search: [string]
  goBack: []
}>()

const router = useRouter()
const route = useRoute()
const searchInputRef = ref<HTMLInputElement | null>(null)
const localSearch = ref(props.searchQuery)

const isDesktopApp = ref(typeof (window as any).pywebview !== "undefined")
function _onPywebviewReady() {
  isDesktopApp.value = true
}
window.addEventListener("pywebviewready", _onPywebviewReady, { once: true })
onUnmounted(() => window.removeEventListener("pywebviewready", _onPywebviewReady))

const showSettings = computed(() => route.path === "/settings")
const roots = ref<RootEntry[]>([])

function openSettings() {
  if (showSettings.value) return
  void router.push("/settings")
}

function closeSettings() {
  if (!showSettings.value) return
  if (window.history.length > 1) {
    router.back()
    return
  }
  void router.replace("/movies")
}

function setPreferredResolution(value: ResolutionPreference) {
  settings.preferredResolution = value
}

function setPreferredHdr(value: HdrPreference) {
  settings.preferredHdr = value
}

function setPlayer(id: string) {
  settings.playerId = id
}

function setCustomCmd(cmd: string) {
  settings.playerCustomCmd = cmd.trim() || null
}

function onMpcPortInput(e: Event) {
  const target = e.target as HTMLInputElement
  const value = target.value.trim()
  if (value === "") {
    settings.playerMpcPort = null
  } else {
    const num = parseInt(value, 10)
    settings.playerMpcPort = isNaN(num) || num <= 0 ? null : num
  }
}

async function refreshPlayers() {
  try {
    detectedPlayers.value = await fetchPlayers()
  } catch (e) {
    console.error("Failed to fetch players:", e)
  }
}

async function refreshRoots() {
  try {
    const data = await fetchRoots()
    roots.value = data.map((r) => ({
      root_id: r.root_id,
      path: r.path,
      status: r.status,
    }))
  } catch (e) {
    console.error("Failed to fetch roots:", e)
  }
}

async function removeRoot(rootId: string) {
  const filtered = roots.value.filter((r) => r.root_id !== rootId)
  const newRoots = Object.fromEntries(filtered.map((r) => [r.root_id, r.path]))
  try {
    await replaceRoots(newRoots)
    await refreshRoots()
  } catch (e) {
    console.error("Failed to remove root:", e)
    alert("Failed to remove root")
  }
}

async function addRoot() {
  const folder = await pickFolderAndAddRoot()
  if (!folder) return
  const suggestedId = folder.split("/").pop() || folder.split("\\").pop() || "media"
  const newRoots = Object.fromEntries(roots.value.map((r) => [r.root_id, r.path]))
  newRoots[suggestedId] = folder
  try {
    await replaceRoots(newRoots)
    await refreshRoots()
    closeSettings()
  } catch (e) {
    console.error("Failed to add root:", e)
    alert("Failed to add root")
  }
}

watch(showSettings, (visible) => {
  if (visible) {
    void refreshRoots()
    void refreshPlayers()
  }
})

// Check if we're on a detail page
const isDetailPage = computed(() => {
  return props.position === "after-movie-header" || props.position === "after-series-hero"
})

// Check if search is active (has query and not on detail page)
const isSearchActive = computed(() => {
  return !isDetailPage.value && !!localSearch.value
})

// Switch views on focus (no Enter required) - only in browse mode
function switchToMovies() {
  if (!isDetailPage.value && props.currentView !== "movies") {
    router.push("/movies")
  }
}

function switchToSeries() {
  if (!isDetailPage.value && props.currentView !== "series") {
    router.push("/series")
  }
}

// Go back to category list from detail page
function goToCategory() {
  // Emit goBack to let App.vue handle navigation and focus restoration
  emit("goBack")
}

// Handle search input focus - navigate to search if we have a query
function handleSearchFocus() {
  // Intentionally no-op: focusing search should not navigate away from detail.
}

// Sync local search to parent
watch(localSearch, (val) => {
  emit("search", val)
})

// Sync parent search to local (for external clears)
watch(
  () => props.searchQuery,
  (val) => {
    if (val !== localSearch.value) {
      localSearch.value = val
    }
  },
)

function handleEscape() {
  if (hexKeyboardVisible.value) {
    hexKeyboardVisible.value = false
    return
  }
  // Clear search and blur
  localSearch.value = ""
  searchInputRef.value?.blur()
}

const hexKeyboardVisible = ref(false)

function onGamepadAction(event: Event) {
  const customEvent = event as CustomEvent<{ action?: string }>
  const action = customEvent.detail?.action
  if (!action) return

  // Only handle when search input is focused
  const active = document.activeElement
  if (!active || !searchInputRef.value || active !== searchInputRef.value) return

  if (action === "select") {
    event.preventDefault()
    if (!hexKeyboardVisible.value) {
      hexKeyboardVisible.value = true
    }
  }
}

function focusSearchInput() {
  searchInputRef.value?.focus()
  searchInputRef.value?.select()
}

function handleKeydown(e: KeyboardEvent) {
  const target = e.target as HTMLElement | null
  const isTypingTarget = Boolean(
    target &&
    (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable),
  )
  const isSearchShortcut = (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f"
  const isSlashShortcut = !e.ctrlKey && !e.metaKey && !e.altKey && e.code === "Slash"

  if (isTypingTarget && !isSearchShortcut) {
    return
  }

  if (isSearchShortcut || isSlashShortcut) {
    e.preventDefault()
    focusSearchInput()
  }
}

onMounted(() => {
  window.addEventListener("keydown", handleKeydown)
  window.addEventListener("mediahive:gamepad-action", onGamepadAction)
})

onUnmounted(() => {
  window.removeEventListener("keydown", handleKeydown)
  window.removeEventListener("mediahive:gamepad-action", onGamepadAction)
})
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

.settings-view {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: var(--bg-primary);
  z-index: 2000;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.settings-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 24px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  flex-shrink: 0;
}

.settings-back {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 0.9rem;
  cursor: pointer;
  padding: 8px 0;
  transition: color 0.2s;
}

.settings-back:hover {
  color: var(--text-primary);
}

.settings-title {
  font-size: 1.1rem;
  font-weight: 600;
  margin: 0;
}

.settings-header-spacer {
  width: 80px;
}

.settings-content {
  flex: 1;
  overflow-y: auto;
  padding: 32px 24px;
  max-width: 720px;
  margin: 0 auto;
  width: 100%;
}

.settings-section {
  margin-bottom: 40px;
}

.settings-section-title {
  font-size: 1rem;
  font-weight: 600;
  margin: 0 0 6px;
}

.settings-section-desc {
  font-size: 0.85rem;
  color: var(--text-secondary);
  margin: 0 0 20px;
}

.roots-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
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

.format-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 0;
}

.format-grid {
  display: grid;
  grid-template-columns: 9.5em 9.5em;
  gap: 0.75em 1.25em;
  justify-content: start;
}

.format-row-stack {
  flex-direction: column;
  gap: 8px;
}

.format-radio-label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: var(--text-primary);
  font-size: 0.85rem;
}

.format-radio {
  width: 15px;
  height: 15px;
  cursor: pointer;
  accent-color: var(--accent, #3b82f6);
}

.player-layout {
  display: grid;
  grid-template-columns: 9.5em 1fr;
  gap: 1.25em;
  align-items: start;
}

.player-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.player-radio-label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: var(--text-primary);
  font-size: 0.85rem;
}

.player-radio {
  width: 15px;
  height: 15px;
  cursor: pointer;
  accent-color: var(--accent, #3b82f6);
}

.player-options {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.player-option-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.player-option-label {
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--text-primary);
}

.player-option-input {
  width: 100%;
  max-width: 400px;
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 6px;
  color: var(--text-primary);
  font-size: 0.85rem;
  outline: none;
}

.player-option-input:focus {
  border-color: var(--accent, #3b82f6);
}

.player-option-input--short {
  width: 120px;
  max-width: none;
}

.player-option-hint {
  margin: 2px 0 0;
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.player-family-note {
  margin: 4px 0 0;
  font-size: 0.8rem;
  color: #22c55e;
}
</style>
