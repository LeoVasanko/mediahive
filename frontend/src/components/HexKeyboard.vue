<template>
  <Teleport to="body">
    <Transition name="hex-keyboard-fade">
      <div v-if="visible" ref="keyboardRef" class="hex-keyboard" @click.stop @keydown="handleKeyDown">
        <div ref="gridRef" class="hex-keyboard-grid">
          <template v-for="(key, index) in layoutKeys" :key="key.id">
            <button
              :ref="(el: unknown) => setKeyRef(el as HTMLElement | null, index)"
              class="hex-key"
              :class="[
                `hex-key-${index}`,
                `hex-key-row-${getCoord(index).row}`,
                {
                  'hex-key-blue': key.bg === 'blue',
                  'hex-key-yellow': key.bg === 'yellow',
                  'hex-key-red': key.bg === 'red',
                  'hex-key-pressed': isPressed(index),
                },
              ]"
              tabindex="-1"
              @click="handleKeyClick(key)"
            >
              <span class="hex-key-label" :class="{ 'hex-key-label-large': key.id === 'sp' }">{{ key.label }}</span>
            </button>
          </template>
          <!-- Green focus outline rendered separately on top -->
          <svg
            v-if="focusedIndex !== null"
            class="hex-key-focus"
            viewBox="-5 -5 96.6 110"
            preserveAspectRatio="none"
            :style="focusSvgStyle"
          >
            <polygon points="43.3,0 86.6,25 86.6,75 43.3,100 0,75 0,25" />
          </svg>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, watch, nextTick, onMounted, onUnmounted, computed } from "vue"

interface KeyDef {
  id: string
  label: string
  value?: string
  action?: "backspace" | "space" | "close"
  bg?: "blue" | "yellow" | "red"
}

const props = defineProps<{
  visible: boolean
  modelValue: string
  searchRef?: HTMLInputElement | null
}>()

const emit = defineEmits<{
  "update:modelValue": [string]
  close: []
  submit: []
}>()

const focusedIndex = ref<number | null>(null)
const pressedTimestamps = ref<Map<number, number>>(new Map())
const keyRefs = ref<(HTMLElement | null)[]>([])
const keyboardRef = ref<HTMLElement | null>(null)
const gridRef = ref<HTMLElement | null>(null)
const pressedTimeouts = new Map<number, ReturnType<typeof setTimeout>>()

function isPressed(index: number): boolean {
  const ts = pressedTimestamps.value.get(index)
  if (!ts) return false
  return Date.now() - ts < 1000
}

// Four-row layout with honeycomb staggering
// Each row shifted 0.5 cell left relative to the one below:
// Row 0 (numbers):   shift 0
// Row 1 (qwerty):    shift 0.5
// Row 2 (asdf):      shift 1.0
// Row 3 (zxcv):      shift 1.5
const layoutKeys: KeyDef[] = [
  // Row 0 (number row) — shift 0
  { id: "1", label: "1", value: "1" },
  { id: "2", label: "2", value: "2" },
  { id: "3", label: "3", value: "3" },
  { id: "4", label: "4", value: "4" },
  { id: "5", label: "5", value: "5" },
  { id: "6", label: "6", value: "6" },
  { id: "7", label: "7", value: "7" },
  { id: "8", label: "8", value: "8" },
  { id: "9", label: "9", value: "9" },
  { id: "0", label: "0", value: "0" },
  { id: "bs", label: "\u2190", action: "backspace", bg: "blue" },
  // Row 1 (QWERTY) — shift 0.5
  { id: "q", label: "Q", value: "q" },
  { id: "w", label: "W", value: "w" },
  { id: "e", label: "E", value: "e" },
  { id: "r", label: "R", value: "r" },
  { id: "t", label: "T", value: "t" },
  { id: "y", label: "Y", value: "y" },
  { id: "u", label: "U", value: "u" },
  { id: "i", label: "I", value: "i" },
  { id: "o", label: "O", value: "o" },
  { id: "p", label: "P", value: "p" },
  // Row 2 (ASDF) — shift 1.0
  { id: "a", label: "A", value: "a" },
  { id: "s", label: "S", value: "s" },
  { id: "d", label: "D", value: "d" },
  { id: "f", label: "F", value: "f" },
  { id: "g", label: "G", value: "g" },
  { id: "h", label: "H", value: "h" },
  { id: "j", label: "J", value: "j" },
  { id: "k", label: "K", value: "k" },
  { id: "l", label: "L", value: "l" },
  // Row 3 (ZXCV + Space + Close) — shift 1.5
  { id: "z", label: "Z", value: "z" },
  { id: "x", label: "X", value: "x" },
  { id: "c", label: "C", value: "c" },
  { id: "v", label: "V", value: "v" },
  { id: "b", label: "B", value: "b" },
  { id: "n", label: "N", value: "n" },
  { id: "m", label: "M", value: "m" },
  { id: "sp", label: "\u2423", value: " ", bg: "yellow" },
  { id: "cls", label: "Close", action: "close", bg: "red" },
]

const ROW_0_START = 0
const ROW_0_COUNT = 11
const ROW_1_START = 11
const ROW_1_COUNT = 10
const ROW_2_START = 21
const ROW_2_COUNT = 9
const ROW_3_START = 30
const ROW_3_COUNT = 9

function getCoord(index: number): { row: number; col: number } {
  if (index >= ROW_0_START && index < ROW_0_START + ROW_0_COUNT) {
    return { row: 0, col: index - ROW_0_START }
  }
  if (index >= ROW_1_START && index < ROW_1_START + ROW_1_COUNT) {
    return { row: 1, col: index - ROW_1_START }
  }
  if (index >= ROW_2_START && index < ROW_2_START + ROW_2_COUNT) {
    return { row: 2, col: index - ROW_2_START }
  }
  if (index >= ROW_3_START && index < ROW_3_START + ROW_3_COUNT) {
    return { row: 3, col: index - ROW_3_START }
  }
  return { row: 0, col: 0 }
}

function getRowRange(row: number): { start: number; count: number } {
  switch (row) {
    case 0: return { start: ROW_0_START, count: ROW_0_COUNT }
    case 1: return { start: ROW_1_START, count: ROW_1_COUNT }
    case 2: return { start: ROW_2_START, count: ROW_2_COUNT }
    case 3: return { start: ROW_3_START, count: ROW_3_COUNT }
    default: return { start: 0, count: 0 }
  }
}

function findIndexAt(row: number, col: number): number | null {
  for (let i = 0; i < layoutKeys.length; i++) {
    const c = getCoord(i)
    if (c.row === row && c.col === col) return i
  }
  return null
}

function setKeyRef(el: HTMLElement | null, index: number) {
  keyRefs.value[index] = el
}

function triggerPress(index: number) {
  pressedTimestamps.value.set(index, Date.now())
  const existing = pressedTimeouts.get(index)
  if (existing) clearTimeout(existing)
  const timeout = setTimeout(() => {
    pressedTimestamps.value.delete(index)
    pressedTimeouts.delete(index)
  }, 1000)
  pressedTimeouts.set(index, timeout)

  // Restart CSS animation immediately by touching the DOM directly
  const el = keyRefs.value[index]
  const label = el?.querySelector(".hex-key-label") as HTMLElement | null
  if (label) {
    label.style.animation = "none"
    // Force reflow
    void label.offsetWidth
    label.style.animation = ""
  }
}

function handleKeyClick(key: KeyDef) {
  const index = layoutKeys.findIndex((k) => k.id === key.id)
  if (index !== -1) triggerPress(index)

  if (key.action === "backspace") {
    emit("update:modelValue", props.modelValue.slice(0, -1))
    return
  }
  if (key.action === "space") {
    emit("update:modelValue", props.modelValue + " ")
    return
  }
  if (key.action === "close") {
    close()
    return
  }
  if (key.value) {
    emit("update:modelValue", props.modelValue + key.value)
  }
}

function close() {
  emit("close")
}

function findNext(
  currentIdx: number,
  direction: "up" | "down" | "left" | "right",
): number | null {
  const current = getCoord(currentIdx)

  if (direction === "left") {
    const targetCol = current.col - 1
    if (targetCol < 0) {
      const range = getRowRange(current.row)
      return range.start + range.count - 1
    }
    let next = findIndexAt(current.row, targetCol)
    if (next !== null) return next
    if (current.row > 0) {
      next = findIndexAt(current.row - 1, targetCol)
      if (next !== null) return next
    }
    if (current.row < 3) {
      next = findIndexAt(current.row + 1, targetCol)
      if (next !== null) return next
    }
    return null
  }

  if (direction === "right") {
    const targetCol = current.col + 1
    const range = getRowRange(current.row)
    if (targetCol >= range.count) {
      return range.start
    }
    let next = findIndexAt(current.row, targetCol)
    if (next !== null) return next
    if (current.row > 0) {
      next = findIndexAt(current.row - 1, targetCol)
      if (next !== null) return next
    }
    if (current.row < 3) {
      next = findIndexAt(current.row + 1, targetCol)
      if (next !== null) return next
    }
    return null
  }

  if (direction === "up") {
    const targetRow = current.row - 1
    if (targetRow < 0) return null
    let next = findIndexAt(targetRow, current.col)
    if (next !== null) return next
    next = findIndexAt(targetRow, current.col - 1)
    if (next !== null) return next
    next = findIndexAt(targetRow, current.col + 1)
    if (next !== null) return next
    return null
  }

  if (direction === "down") {
    const targetRow = current.row + 1
    if (targetRow > 3) return null
    let next = findIndexAt(targetRow, current.col)
    if (next !== null) return next
    next = findIndexAt(targetRow, current.col - 1)
    if (next !== null) return next
    next = findIndexAt(targetRow, current.col + 1)
    if (next !== null) return next
    return null
  }

  return null
}

function handleKeyDown(e: KeyboardEvent) {
  const direction = {
    ArrowUp: "up",
    ArrowDown: "down",
    ArrowLeft: "left",
    ArrowRight: "right",
  }[e.key] as "up" | "down" | "left" | "right" | undefined

  if (!direction) {
    if (e.key === "Enter" && focusedIndex.value !== null) {
      e.preventDefault()
      e.stopPropagation()
      handleKeyClick(layoutKeys[focusedIndex.value])
    }
    return
  }

  const current = focusedIndex.value ?? 0
  const next = findNext(current, direction)

  if (next !== null && keyRefs.value[next]) {
    e.preventDefault()
    e.stopPropagation()
    focusedIndex.value = next
  }
}

// Compute focus outline position from focused key element
const focusSvgStyle = computed(() => {
  const idx = focusedIndex.value
  if (idx === null || !keyRefs.value[idx] || !gridRef.value) return {}
  const el = keyRefs.value[idx]!
  const grid = gridRef.value
  const gridRect = grid.getBoundingClientRect()
  const elRect = el.getBoundingClientRect()
  return {
    left: `${(elRect.left - gridRect.left) / 0.8}px`,
    top: `${(elRect.top - gridRect.top) / 0.8}px`,
    width: `${elRect.width / 0.8}px`,
    height: `${elRect.height / 0.8}px`,
  }
})

// Position keyboard under search input
function updatePosition() {
  if (!keyboardRef.value || !props.searchRef) return
  const searchRect = props.searchRef.getBoundingClientRect()
  const keyboardEl = keyboardRef.value
  keyboardEl.style.left = `${searchRect.left + searchRect.width / 2}px`
  keyboardEl.style.top = `${searchRect.bottom + 8}px`
  keyboardEl.style.bottom = "auto"
  keyboardEl.style.transform = "translateX(-50%) scale(0.8)"
}

// Track focus loss to close keyboard
function onFocusIn(event: FocusEvent) {
  if (!props.visible) return
  const target = event.target as HTMLElement | null
  if (keyboardRef.value && !keyboardRef.value.contains(target)) {
    close()
  }
}

// Initial focus on 'A' (index 21)
watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      nextTick(() => {
        focusedIndex.value = 21
        updatePosition()
      })
    } else {
      focusedIndex.value = null
    }
  },
)

function onGamepadAction(event: Event) {
  if (!props.visible) return
  const customEvent = event as CustomEvent<{ action?: string }>
  const action = customEvent.detail?.action
  if (!action) return

  if (action === "select") {
    event.preventDefault()
    if (focusedIndex.value !== null) {
      triggerPress(focusedIndex.value)
      handleKeyClick(layoutKeys[focusedIndex.value])
    }
    return
  }

  if (action === "back") {
    event.preventDefault()
    close()
    return
  }

  const direction = {
    up: "up",
    down: "down",
    left: "left",
    right: "right",
  }[action] as "up" | "down" | "left" | "right" | undefined

  if (direction && focusedIndex.value !== null) {
    event.preventDefault()
    const next = findNext(focusedIndex.value, direction)
    if (next !== null && keyRefs.value[next]) {
      focusedIndex.value = next
    }
  }
}

// Custom handler for X (backspace), Y (space) and shoulder buttons (LB/RB)
function onRawGamepad(event: Event) {
  if (!props.visible) return
  const customEvent = event as CustomEvent<{ action?: string; button?: number }>
  const button = customEvent.detail?.button
  if (button === undefined) return

  // X button = backspace (button 2)
  if (button === 2) {
    event.preventDefault()
    const bsIndex = layoutKeys.findIndex((k) => k.id === "bs")
    if (bsIndex !== -1) triggerPress(bsIndex)
    emit("update:modelValue", props.modelValue.slice(0, -1))
    return
  }

  // Y button = space (button 3)
  if (button === 3) {
    event.preventDefault()
    const spIndex = layoutKeys.findIndex((k) => k.id === "sp")
    if (spIndex !== -1) triggerPress(spIndex)
    emit("update:modelValue", props.modelValue + " ")
    return
  }

  // LB = move cursor left in search input (button 4)
  if (button === 4) {
    event.preventDefault()
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }))
    return
  }

  // RB = move cursor right in search input (button 5)
  if (button === 5) {
    event.preventDefault()
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }))
    return
  }
}

onMounted(() => {
  window.addEventListener("mediahive:gamepad-action", onGamepadAction)
  window.addEventListener("mediahive:gamepad-button", onRawGamepad)
  document.addEventListener("focusin", onFocusIn)
  window.addEventListener("resize", updatePosition)
})

onUnmounted(() => {
  window.removeEventListener("mediahive:gamepad-action", onGamepadAction)
  window.removeEventListener("mediahive:gamepad-button", onRawGamepad)
  document.removeEventListener("focusin", onFocusIn)
  window.removeEventListener("resize", updatePosition)
})
</script>

<style scoped>
.hex-keyboard {
  position: fixed;
  z-index: 3000;
  transform-origin: top center;
}

.hex-keyboard-grid {
  position: relative;
  --key-h: 56px;
  --key-w: calc(0.866 * var(--key-h));
  width: calc(var(--key-w) * 12.5);
  height: calc(var(--key-h) * 3.25);
}

.hex-key {
  position: absolute;
  height: var(--key-h);
  aspect-ratio: 0.866 / 1;
  border: none;
  color: #ffffff;
  font-size: 0.95rem;
  font-weight: 500;
  cursor: pointer;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  clip-path: polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%);
  transition: transform 0.15s ease, color 0.3s ease;
  outline: none;
  transform: scale(0.97);
}

/* Row-based greyscale gradients — lighter top, darker home row */
.hex-key-row-0 {
  background: linear-gradient(180deg, #626b7bd0 0%, #3a3f4ad0 100%);
}
.hex-key-row-1, .hex-key-row-3 {
  background: linear-gradient(180deg, #2a3343d0 0%, #2c3242d0 100%);
}
.hex-key-row-2 {
  background: linear-gradient(180deg, #1f2937d0 0%, #111827d0 100%);
}

/* No hover/active scale — keyboard is gamepad-operated */

/* Keypress feedback: instant green, then exponential fade to white */
.hex-key-pressed .hex-key-label {
  animation: hex-label-fade 1s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

@keyframes hex-label-fade {
  0% { color: #22c55e; }
  100% { color: #ffffff; }
}

/* Special key backgrounds override row gradients */
.hex-key-blue {
  background: linear-gradient(180deg, #60a5fad0 0%, #3b82f6d0 100%);
}

.hex-key-yellow {
  background: linear-gradient(180deg, #facc15d0 0%, #eab308d0 100%);
}

.hex-key-red {
  background: linear-gradient(180deg, #f87171d0 0%, #ef4444d0 100%);
}

.hex-key-label {
  position: relative;
  z-index: 2;
  pointer-events: none;
  user-select: none;
}

.hex-key-label-large {
  font-size: 2em;
  line-height: 1;
  margin-top: -0.15em;
}

/* Green focus outline rendered separately on top of the grid */
.hex-key-focus {
  position: absolute;
  pointer-events: none;
  z-index: 20;
  overflow: visible;
}

.hex-key-focus polygon {
  fill: none;
  stroke: #22c55e;
  stroke-width: 3;
  vector-effect: non-scaling-stroke;
  animation: hex-outline-blink 1s ease-in-out infinite;
}

@keyframes hex-outline-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

/* Row positioning */
/* Row 0 (numbers + BS) — shift 0 */
.hex-key-0  { top: calc(var(--key-h) * 0.00); left: calc(var(--key-w) * 0.0); }
.hex-key-1  { top: calc(var(--key-h) * 0.00); left: calc(var(--key-w) * 1.0); }
.hex-key-2  { top: calc(var(--key-h) * 0.00); left: calc(var(--key-w) * 2.0); }
.hex-key-3  { top: calc(var(--key-h) * 0.00); left: calc(var(--key-w) * 3.0); }
.hex-key-4  { top: calc(var(--key-h) * 0.00); left: calc(var(--key-w) * 4.0); }
.hex-key-5  { top: calc(var(--key-h) * 0.00); left: calc(var(--key-w) * 5.0); }
.hex-key-6  { top: calc(var(--key-h) * 0.00); left: calc(var(--key-w) * 6.0); }
.hex-key-7  { top: calc(var(--key-h) * 0.00); left: calc(var(--key-w) * 7.0); }
.hex-key-8  { top: calc(var(--key-h) * 0.00); left: calc(var(--key-w) * 8.0); }
.hex-key-9  { top: calc(var(--key-h) * 0.00); left: calc(var(--key-w) * 9.0); }
.hex-key-10 { top: calc(var(--key-h) * 0.00); left: calc(var(--key-w) * 10.0); }

/* Row 1 (QWERTY) — shift 0.5 */
.hex-key-11 { top: calc(var(--key-h) * 0.75); left: calc(var(--key-w) * 0.5); }
.hex-key-12 { top: calc(var(--key-h) * 0.75); left: calc(var(--key-w) * 1.5); }
.hex-key-13 { top: calc(var(--key-h) * 0.75); left: calc(var(--key-w) * 2.5); }
.hex-key-14 { top: calc(var(--key-h) * 0.75); left: calc(var(--key-w) * 3.5); }
.hex-key-15 { top: calc(var(--key-h) * 0.75); left: calc(var(--key-w) * 4.5); }
.hex-key-16 { top: calc(var(--key-h) * 0.75); left: calc(var(--key-w) * 5.5); }
.hex-key-17 { top: calc(var(--key-h) * 0.75); left: calc(var(--key-w) * 6.5); }
.hex-key-18 { top: calc(var(--key-h) * 0.75); left: calc(var(--key-w) * 7.5); }
.hex-key-19 { top: calc(var(--key-h) * 0.75); left: calc(var(--key-w) * 8.5); }
.hex-key-20 { top: calc(var(--key-h) * 0.75); left: calc(var(--key-w) * 9.5); }

/* Row 2 (ASDF) — shift 1.0 */
.hex-key-21 { top: calc(var(--key-h) * 1.50); left: calc(var(--key-w) * 1.0); }
.hex-key-22 { top: calc(var(--key-h) * 1.50); left: calc(var(--key-w) * 2.0); }
.hex-key-23 { top: calc(var(--key-h) * 1.50); left: calc(var(--key-w) * 3.0); }
.hex-key-24 { top: calc(var(--key-h) * 1.50); left: calc(var(--key-w) * 4.0); }
.hex-key-25 { top: calc(var(--key-h) * 1.50); left: calc(var(--key-w) * 5.0); }
.hex-key-26 { top: calc(var(--key-h) * 1.50); left: calc(var(--key-w) * 6.0); }
.hex-key-27 { top: calc(var(--key-h) * 1.50); left: calc(var(--key-w) * 7.0); }
.hex-key-28 { top: calc(var(--key-h) * 1.50); left: calc(var(--key-w) * 8.0); }
.hex-key-29 { top: calc(var(--key-h) * 1.50); left: calc(var(--key-w) * 9.0); }

/* Row 3 (ZXCV + Space + Close) — shift 1.5 */
.hex-key-30 { top: calc(var(--key-h) * 2.25); left: calc(var(--key-w) * 1.5); }
.hex-key-31 { top: calc(var(--key-h) * 2.25); left: calc(var(--key-w) * 2.5); }
.hex-key-32 { top: calc(var(--key-h) * 2.25); left: calc(var(--key-w) * 3.5); }
.hex-key-33 { top: calc(var(--key-h) * 2.25); left: calc(var(--key-w) * 4.5); }
.hex-key-34 { top: calc(var(--key-h) * 2.25); left: calc(var(--key-w) * 5.5); }
.hex-key-35 { top: calc(var(--key-h) * 2.25); left: calc(var(--key-w) * 6.5); }
.hex-key-36 { top: calc(var(--key-h) * 2.25); left: calc(var(--key-w) * 7.5); }
.hex-key-37 { top: calc(var(--key-h) * 2.25); left: calc(var(--key-w) * 8.5); }
.hex-key-38 { top: calc(var(--key-h) * 2.25); left: calc(var(--key-w) * 9.5); }

/* Transition */
.hex-keyboard-fade-enter-active,
.hex-keyboard-fade-leave-active {
  transition: opacity 0.25s ease;
}

.hex-keyboard-fade-enter-from,
.hex-keyboard-fade-leave-to {
  opacity: 0;
}
</style>
