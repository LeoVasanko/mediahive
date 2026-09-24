<template>
  <div
    v-if="visible"
    ref="menuRef"
    class="version-action-menu"
    :style="menuStyle"
    tabindex="-1"
    @keydown="handleKeydown"
  >
    <div class="version-action-path" :title="resolvedPath">
      {{ resolvedPath }}
    </div>
    <button class="version-action-item" :disabled="disabled" @click="emit('play')">
      <span class="version-action-icon" aria-hidden="true">
        <svg viewBox="0 0 16 16" focusable="false">
          <path
            d="M4 3.2c0-.54.6-.86 1.05-.56l6.2 4.14a.67.67 0 0 1 0 1.12l-6.2 4.14A.67.67 0 0 1 4 11.44V3.2Z"
          />
        </svg>
      </span>
      {{ playLabel }}
    </button>
    <button class="version-action-item" :disabled="disabled" @click="emit('openFolder')">
      <span class="version-action-icon" aria-hidden="true">
        <svg viewBox="0 0 16 16" focusable="false">
          <path
            d="M1.4 4.3c0-.72.58-1.3 1.3-1.3h3.55c.3 0 .58.13.77.35l.72.85h5.56c.72 0 1.3.58 1.3 1.3v.92H1.4V4.3Zm0 3.22h13.2v4.2c0 .72-.58 1.3-1.3 1.3H2.7c-.72 0-1.3-.58-1.3-1.3v-4.2Z"
          />
        </svg>
      </span>
      Open Folder
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue"

const props = withDefaults(
  defineProps<{
    visible: boolean
    x: number
    y: number
    filePath: string | null
    rootName?: string | null
    playLabel?: string
  }>(),
  {
    rootName: null,
    playLabel: "Play",
  },
)

const emit = defineEmits<{
  play: []
  openFolder: []
  close: []
}>()

const menuRef = ref<HTMLElement | null>(null)
const menuLeft = ref(0)
const menuTop = ref(0)
const VIEWPORT_MARGIN = 12

function toPosixPath(value: string | null | undefined): string {
  return (value || "").replace(/\\/g, "/")
}

const resolvedPath = computed(() => {
  if (!props.filePath) return "No playable file"
  const normalizedFilePath = toPosixPath(props.filePath)
  const rootName = toPosixPath((props.rootName || "").trim())
  if (!rootName) return normalizedFilePath
  return `${rootName}/${normalizedFilePath}`
})

const menuStyle = computed(() => ({
  left: `${menuLeft.value}px`,
  top: `${menuTop.value}px`,
}))

const disabled = computed(() => !props.filePath)

function getFocusableElements(): HTMLElement[] {
  if (!menuRef.value) return []
  return Array.from(
    menuRef.value.querySelectorAll<HTMLElement>(".version-action-item:not(:disabled)"),
  )
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
  () => [props.visible, props.x, props.y, resolvedPath.value],
  async ([visible]) => {
    if (!visible) return
    await nextTick()
    clampToViewport()
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
.version-action-menu {
  position: fixed;
  z-index: 1001;
  min-width: 260px;
  max-width: min(680px, calc(100vw - 24px));
  background: rgba(18, 20, 28, 0.98);
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 8px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45);
  overflow: hidden;
}

.version-action-path {
  padding: 8px 12px;
  font-size: 0.74rem;
  line-height: 1.35;
  color: rgba(255, 255, 255, 0.78);
  background: rgba(255, 255, 255, 0.05);
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  word-break: break-all;
  white-space: normal;
}

.version-action-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  border: none;
  background: transparent;
  color: #fff;
  text-align: left;
  padding: 10px 12px;
  font-size: 0.82rem;
  cursor: pointer;
}

html.mouse-active .version-action-item:hover:not(:disabled),
.version-action-item:focus-visible:not(:disabled) {
  background: rgba(255, 255, 255, 0.12);
  outline: none;
}

.version-action-item:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.version-action-icon {
  width: 16px;
  height: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.85);
  flex: 0 0 16px;
}

.version-action-icon svg {
  width: 16px;
  height: 16px;
  fill: currentColor;
}
</style>
