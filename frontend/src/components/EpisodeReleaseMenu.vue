<template>
  <div
    v-if="visible"
    ref="menuRef"
    class="episode-release-menu"
    :style="menuStyle"
    tabindex="-1"
    @keydown="handleKeydown"
  >
    <div class="episode-release-header">{{ episodeName }}</div>
    <div v-if="releases.length > 0" class="episode-release-list">
      <ReleaseVersionCard
        v-for="(release, index) in releases"
        :key="index"
        :torrent="release"
        :best="index === 0"
        :selectable="!!release.playable_file"
        :disabled="!release.playable_file"
        compact-flags
        variant="menu"
        inert-card
        show-actions
        :play-label="getPlayLabel(release.playable_file)"
        @play="emit('play', release.playable_file || '')"
        @open-folder="emit('openFolder', release.playable_file || '')"
      />
    </div>
    <div v-else class="episode-release-empty">No versions available</div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue"
import type { Torrent } from "../types"
import ReleaseVersionCard from "./ReleaseVersionCard.vue"

const props = defineProps<{
  visible: boolean
  x: number
  y: number
  episodeName: string
  releases: Torrent[]
  hasResumePosition: (filePath: string | null) => boolean
}>()

const emit = defineEmits<{
  play: [string]
  openFolder: [string]
  close: []
}>()

const menuRef = ref<HTMLElement | null>(null)
const menuLeft = ref(0)
const menuTop = ref(0)
const VIEWPORT_MARGIN = 12

const menuStyle = computed(() => ({
  left: `${menuLeft.value}px`,
  top: `${menuTop.value}px`,
}))

function getPlayLabel(filePath: string | null | undefined): string {
  return props.hasResumePosition(filePath || null) ? "Continue" : "Play"
}

function getFocusableElements(): HTMLElement[] {
  if (!menuRef.value) return []
  return Array.from(menuRef.value.querySelectorAll<HTMLElement>(".ctx-btn:not(:disabled)"))
}

function focusNext(delta: number) {
  const elements = getFocusableElements()
  if (elements.length === 0) return
  const currentIndex = elements.findIndex((el) => el === document.activeElement)
  const nextIndex =
    currentIndex < 0 ? 0 : (currentIndex + delta + elements.length) % elements.length
  elements[nextIndex].focus()
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === "Tab") {
    event.preventDefault()
    focusNext(event.shiftKey ? -1 : 1)
    return
  }
  if (event.key === "ArrowDown" || event.key === "ArrowRight") {
    event.preventDefault()
    focusNext(1)
    return
  }
  if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
    event.preventDefault()
    focusNext(-1)
    return
  }
  if (event.key === "Escape") {
    event.preventDefault()
    emit("close")
    return
  }
}

function clampToViewport() {
  const menu = menuRef.value
  if (!menu) return

  const width = menu.offsetWidth
  const height = menu.offsetHeight

  const maxLeft = Math.max(VIEWPORT_MARGIN, window.innerWidth - width - VIEWPORT_MARGIN)
  const maxTop = Math.max(VIEWPORT_MARGIN, window.innerHeight - height - VIEWPORT_MARGIN)

  menuLeft.value = Math.min(Math.max(props.x, VIEWPORT_MARGIN), maxLeft)
  menuTop.value = Math.min(Math.max(props.y, VIEWPORT_MARGIN), maxTop)
}

function handleViewportChange() {
  if (!props.visible) return
  clampToViewport()
}

watch(
  () => [props.visible, props.x, props.y, props.episodeName, props.releases.length],
  async ([visible]) => {
    if (!visible) return
    await nextTick()
    clampToViewport()
    // Focus first action button for keyboard navigation
    const firstBtn = menuRef.value?.querySelector(".ctx-btn:not(:disabled)") as HTMLElement | null
    firstBtn?.focus()
  },
  { immediate: true },
)

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      window.addEventListener("resize", handleViewportChange)
      return
    }
    window.removeEventListener("resize", handleViewportChange)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  window.removeEventListener("resize", handleViewportChange)
})
</script>

<style scoped>
.episode-release-menu {
  position: fixed;
  z-index: 1000;
  background: rgba(20, 20, 30, 0.98);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 8px;
  min-width: 420px;
  max-width: min(820px, calc(100vw - 24px));
  max-height: calc(100vh - 24px);
  overflow-y: auto;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6);
  padding: 8px;
}

.episode-release-header {
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

.episode-release-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.episode-release-empty {
  padding: 16px;
  text-align: center;
  color: rgba(255, 255, 255, 0.5);
  font-size: 0.85rem;
}
</style>
