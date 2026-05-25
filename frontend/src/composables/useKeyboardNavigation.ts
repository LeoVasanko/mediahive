import { ref } from "vue"

export interface FocusableElement {
  element: HTMLElement
  row: number
  col: number
}

// Global focus state
const focusedElement = ref<HTMLElement | null>(null)
const isNavigating = ref(false)
// Track the "desired" column when moving vertically (to maintain column position across rows of different lengths)
const desiredCol = ref<number | null>(null)
// Track if global handlers are installed
let handlersInstalled = false

// Data attribute names
const FOCUSABLE_ATTR = "data-nav-focusable"
const ROW_ATTR = "data-nav-row"
const COL_ATTR = "data-nav-col"
const ENTRY_COL_ATTR = "data-nav-entry-col"
const SYNC_SCROLL_ROW_ATTR = "data-sync-scroll-row"

const SYNC_SCROLL_FIRST_CONTENT_ROW = 2
const SYNC_SCROLL_DEADZONE_RATIO = 0.18
const SYNC_SCROLL_EASING_MS = 220
const SYNC_SCROLL_TAIL_VAR = "--sync-row-tail"

let syncedRowsFrame: number | null = null
let syncedRowsCurrentOffset = 0
let syncedRowsTargetOffset = 0
let syncedRowsTailPx = 0
let lastSyncedAnchorCol: number | null = null
let lastSyncedRowsAnimationAt: number | null = null

function getSyncedRows(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>(`[${SYNC_SCROLL_ROW_ATTR}="true"]`))
}

function getSyncedRowMetrics(rows: HTMLElement[]) {
  for (const row of rows) {
    const cards = Array.from(row.querySelectorAll<HTMLElement>(`.media-card[${FOCUSABLE_ATTR}]`))
    if (cards.length === 0) continue

    const firstRect = cards[0].getBoundingClientRect()
    const cardWidth = firstRect.width
    if (cardWidth <= 0) continue

    const rowStyle = window.getComputedStyle(row)
    const paddingLeft = parseFloat(rowStyle.paddingLeft || "0")
    let gap = parseFloat(rowStyle.columnGap || rowStyle.gap || "0")

    if (cards.length > 1) {
      const secondRect = cards[1].getBoundingClientRect()
      gap = Math.max(0, secondRect.left - firstRect.left - cardWidth)
    }

    return {
      cardWidth,
      stride: cardWidth + gap,
      paddingLeft,
      viewportWidth: row.clientWidth,
    }
  }

  return null
}

function clampRowScrollOffset(row: HTMLElement, offset: number): number {
  const maxOffset = Math.max(0, row.scrollWidth - row.clientWidth)
  return Math.min(Math.max(offset, 0), maxOffset)
}

function getRowMaxOffset(row: HTMLElement): number {
  return Math.max(0, row.scrollWidth - row.clientWidth)
}

function getRowNaturalMaxOffset(row: HTMLElement): number {
  return Math.max(0, getRowMaxOffset(row) - syncedRowsTailPx)
}

function getTailNeededForOffset(offset: number, rows: HTMLElement[]): number {
  if (rows.length === 0) return 0

  let minNaturalMax = Number.POSITIVE_INFINITY
  for (const row of rows) {
    minNaturalMax = Math.min(minNaturalMax, getRowNaturalMaxOffset(row))
  }

  if (!Number.isFinite(minNaturalMax)) return 0
  return Math.max(0, offset - minNaturalMax)
}

function setSyncedRowsTail(tailPx: number, rows: HTMLElement[] = getSyncedRows()) {
  const nextTail = Math.max(0, tailPx)
  syncedRowsTailPx = nextTail
  for (const row of rows) {
    row.style.setProperty(SYNC_SCROLL_TAIL_VAR, `${nextTail}px`)
  }
}

function applySyncedRowScroll(offset: number, rows: HTMLElement[] = getSyncedRows()) {
  for (const row of rows) {
    row.scrollLeft = clampRowScrollOffset(row, offset)
  }
}

function resetSyncedRows(immediate: boolean = false) {
  lastSyncedAnchorCol = null
  syncedRowsTargetOffset = 0
  setSyncedRowsTail(0)

  if (immediate) {
    syncedRowsCurrentOffset = 0
    applySyncedRowScroll(0)
    lastSyncedRowsAnimationAt = null
    stopSyncedRowAnimation()
    return
  }

  if (Math.abs(syncedRowsCurrentOffset) < 0.5) {
    syncedRowsCurrentOffset = 0
    applySyncedRowScroll(0)
    lastSyncedRowsAnimationAt = null
    stopSyncedRowAnimation()
    return
  }

  if (syncedRowsFrame === null) {
    syncedRowsFrame = window.requestAnimationFrame(animateSyncedRows)
  }
}

function stopSyncedRowAnimation() {
  if (syncedRowsFrame !== null) {
    window.cancelAnimationFrame(syncedRowsFrame)
    syncedRowsFrame = null
  }
  lastSyncedRowsAnimationAt = null
}

function animateSyncedRows(now: number) {
  const rows = getSyncedRows()
  if (rows.length === 0) {
    stopSyncedRowAnimation()
    return
  }

  const delta = syncedRowsTargetOffset - syncedRowsCurrentOffset
  const elapsedMs =
    lastSyncedRowsAnimationAt === null ? 16 : Math.max(1, now - lastSyncedRowsAnimationAt)
  lastSyncedRowsAnimationAt = now

  const alpha = 1 - Math.exp(-elapsedMs / SYNC_SCROLL_EASING_MS)
  syncedRowsCurrentOffset += delta * alpha
  applySyncedRowScroll(syncedRowsCurrentOffset, rows)

  if (Math.abs(delta) < 0.5) {
    syncedRowsCurrentOffset = syncedRowsTargetOffset
    applySyncedRowScroll(syncedRowsCurrentOffset, rows)
    stopSyncedRowAnimation()
    return
  }

  syncedRowsFrame = window.requestAnimationFrame(animateSyncedRows)
}

function updateSyncedRowTarget(anchorCol: number, anchorRow: HTMLElement | null = null) {
  const rows = getSyncedRows()
  if (rows.length === 0) return

  const metrics = getSyncedRowMetrics(rows)
  if (!metrics) return

  const currentOffset = syncedRowsCurrentOffset
  const effectiveAnchorOffset = anchorRow
    ? clampRowScrollOffset(anchorRow, currentOffset)
    : currentOffset
  const deadzoneInset = Math.max(
    metrics.paddingLeft,
    (metrics.viewportWidth - metrics.cardWidth) * SYNC_SCROLL_DEADZONE_RATIO,
  )
  const minVisibleLeft = deadzoneInset
  const maxVisibleLeft = Math.max(
    minVisibleLeft,
    metrics.viewportWidth - metrics.cardWidth - deadzoneInset,
  )
  const itemLeft = metrics.paddingLeft + anchorCol * metrics.stride
  const viewportLeft = itemLeft - effectiveAnchorOffset

  const desiredOffset =
    viewportLeft < minVisibleLeft
      ? Math.max(0, itemLeft - minVisibleLeft)
      : viewportLeft > maxVisibleLeft
        ? Math.max(0, itemLeft - maxVisibleLeft)
        : currentOffset

  const neededTail = getTailNeededForOffset(desiredOffset, rows)
  if (Math.abs(neededTail - syncedRowsTailPx) >= 0.5) {
    setSyncedRowsTail(neededTail, rows)
  }

  lastSyncedAnchorCol = anchorCol
  syncedRowsTargetOffset = desiredOffset

  if (anchorRow) {
    // Preserve a global virtual offset, bounded by the focused row after tail-space is applied.
    syncedRowsTargetOffset = Math.min(syncedRowsTargetOffset, getRowMaxOffset(anchorRow))
  } else if (syncedRowsTailPx > 0) {
    setSyncedRowsTail(0, rows)
  }

  if (syncedRowsFrame === null) {
    syncedRowsCurrentOffset = currentOffset
  }

  if (Math.abs(syncedRowsTargetOffset - syncedRowsCurrentOffset) < 0.5) {
    syncedRowsCurrentOffset = syncedRowsTargetOffset
    applySyncedRowScroll(syncedRowsCurrentOffset, rows)
    stopSyncedRowAnimation()
    return
  }

  if (syncedRowsFrame === null) {
    syncedRowsFrame = window.requestAnimationFrame(animateSyncedRows)
  }
}

function syncRowsToElement(element: HTMLElement) {
  const row = parseInt(element.getAttribute(ROW_ATTR) || "0", 10)
  if (row < SYNC_SCROLL_FIRST_CONTENT_ROW) {
    resetSyncedRows()
    return
  }

  const currentCol = parseInt(element.getAttribute(COL_ATTR) || "0", 10)
  const anchorCol = desiredCol.value ?? currentCol
  const anchorRow = element.closest<HTMLElement>(`[${SYNC_SCROLL_ROW_ATTR}="true"]`)
  updateSyncedRowTarget(anchorCol, anchorRow)
}

function handleSyncedRowResize() {
  if (lastSyncedAnchorCol === null) {
    resetSyncedRows(true)
    return
  }
  updateSyncedRowTarget(lastSyncedAnchorCol)
}

function ensureElementVisibleVertically(element: HTMLElement) {
  const rootStyle = window.getComputedStyle(document.documentElement)
  const headerHeight = parseFloat(rootStyle.getPropertyValue("--header-height") || "0")
  const topMargin = headerHeight + 24
  const bottomMargin = 24
  const rect = element.getBoundingClientRect()

  if (rect.top < topMargin) {
    window.scrollBy({
      top: rect.top - topMargin,
      behavior: "smooth",
    })
    return
  }

  if (rect.bottom > window.innerHeight - bottomMargin) {
    window.scrollBy({
      top: rect.bottom - (window.innerHeight - bottomMargin),
      behavior: "smooth",
    })
  }
}

/**
 * Get all focusable elements in the DOM, grouped by row
 */
function getFocusableElements(): FocusableElement[] {
  const elements = document.querySelectorAll(`[${FOCUSABLE_ATTR}]`)
  const result: FocusableElement[] = []

  elements.forEach((el) => {
    const htmlEl = el as HTMLElement
    // Skip hidden elements
    if (htmlEl.offsetParent === null) return

    const rect = htmlEl.getBoundingClientRect()
    // Skip elements not in viewport or zero-sized
    if (rect.width === 0 || rect.height === 0) return

    const row = parseInt(htmlEl.getAttribute(ROW_ATTR) || "0", 10)
    const col = parseInt(htmlEl.getAttribute(COL_ATTR) || "0", 10)

    result.push({
      element: htmlEl,
      row,
      col,
    })
  })

  return result
}

/**
 * Get elements grouped by row
 */
function getElementsByRow(): Map<number, FocusableElement[]> {
  const elements = getFocusableElements()
  const byRow = new Map<number, FocusableElement[]>()

  for (const el of elements) {
    if (!byRow.has(el.row)) {
      byRow.set(el.row, [])
    }
    byRow.get(el.row)!.push(el)
  }

  // Sort each row by column
  for (const [, rowElements] of byRow) {
    rowElements.sort((a, b) => a.col - b.col)
  }

  return byRow
}

/**
 * Find element by row and col indices
 * @param useEntryCol - if true, check for entry-col override on elements
 */
function findElementAt(
  row: number,
  col: number,
  useEntryCol: boolean = false,
): FocusableElement | null {
  const byRow = getElementsByRow()
  const rowElements = byRow.get(row)
  if (!rowElements || rowElements.length === 0) return null

  // Check if any element in this row has an entry-col override
  if (useEntryCol) {
    for (const el of rowElements) {
      const entryCol = el.element.getAttribute(ENTRY_COL_ATTR)
      if (entryCol !== null) {
        const overrideCol = parseInt(entryCol, 10)
        const entryTarget = rowElements.find((e) => e.col === overrideCol)
        if (entryTarget) return entryTarget
      }
    }
  }

  // Find exact match or nearest col
  const exact = rowElements.find((e) => e.col === col)
  if (exact) return exact

  // Find nearest col in this row
  let nearest = rowElements[0]
  let nearestDist = Math.abs(nearest.col - col)

  for (const el of rowElements) {
    const dist = Math.abs(el.col - col)
    if (dist < nearestDist) {
      nearest = el
      nearestDist = dist
    }
  }

  return nearest
}

function findElementClosestToLogicalViewportX(
  row: number,
  logicalViewportCenterX: number,
  preferredCol: number,
  metrics: { cardWidth: number; stride: number; paddingLeft: number } | null,
): FocusableElement | null {
  const byRow = getElementsByRow()
  const rowElements = byRow.get(row)
  if (!rowElements || rowElements.length === 0) return null

  let nearest: FocusableElement | null = null
  let nearestViewportDist = Number.POSITIVE_INFINITY
  let nearestColDist = Number.POSITIVE_INFINITY

  for (const candidate of rowElements) {
    let candidateCenterX: number
    if (metrics) {
      // Compare using global synced offset so capped rows do not skew vertical matching.
      candidateCenterX =
        metrics.paddingLeft +
        candidate.col * metrics.stride -
        syncedRowsCurrentOffset +
        metrics.cardWidth / 2
    } else {
      const rect = candidate.element.getBoundingClientRect()
      candidateCenterX = rect.left + rect.width / 2
    }

    const viewportDist = Math.abs(candidateCenterX - logicalViewportCenterX)
    const colDist = Math.abs(candidate.col - preferredCol)

    if (
      viewportDist < nearestViewportDist ||
      (Math.abs(viewportDist - nearestViewportDist) < 0.5 && colDist < nearestColDist)
    ) {
      nearest = candidate
      nearestViewportDist = viewportDist
      nearestColDist = colDist
    }
  }

  return nearest
}

/**
 * Find next element in direction using row/col indices
 */
function findNextElement(
  current: HTMLElement,
  direction: "up" | "down" | "left" | "right",
): HTMLElement | null {
  const currentRow = parseInt(current.getAttribute(ROW_ATTR) || "0", 10)
  const currentCol = parseInt(current.getAttribute(COL_ATTR) || "0", 10)
  const byRow = getElementsByRow()

  if (direction === "left" || direction === "right") {
    // Horizontal: move within same row by col index
    desiredCol.value = null // Reset desired col on horizontal movement

    const rowElements = byRow.get(currentRow)
    if (!rowElements) return null

    const delta = direction === "right" ? 1 : -1
    const targetCol = currentCol + delta

    // Find element with target col in this row
    const target = rowElements.find((e) => e.col === targetCol)
    return target?.element || null
  } else {
    // Vertical: move to adjacent row, try to maintain column
    const sortedRows = Array.from(byRow.keys()).sort((a, b) => a - b)
    const currentRowIdx = sortedRows.indexOf(currentRow)

    if (currentRowIdx === -1) return null

    const delta = direction === "down" ? 1 : -1
    const targetRowIdx = currentRowIdx + delta

    if (targetRowIdx < 0 || targetRowIdx >= sortedRows.length) return null

    const targetRow = sortedRows[targetRowIdx]

    // Use desired col if set, otherwise use current col
    const targetCol = desiredCol.value ?? currentCol

    // Set desired col if not already set (first vertical move in a sequence)
    if (desiredCol.value === null) {
      desiredCol.value = currentCol
    }

    // Use entry column hook for vertical navigation when present.
    const entryTarget = findElementAt(targetRow, targetCol, true)
    const targetRowElements = byRow.get(targetRow) ?? []
    const hasEntryOverride = targetRowElements.some((el) => el.element.hasAttribute(ENTRY_COL_ATTR))
    if (hasEntryOverride) {
      return entryTarget?.element || null
    }

    const syncedRows = getSyncedRows()
    const metrics = getSyncedRowMetrics(syncedRows)
    const logicalCurrentCenterX = metrics
      ? metrics.paddingLeft +
        currentCol * metrics.stride -
        syncedRowsCurrentOffset +
        metrics.cardWidth / 2
      : (() => {
          const currentRect = current.getBoundingClientRect()
          return currentRect.left + currentRect.width / 2
        })()
    const closestByViewport = findElementClosestToLogicalViewportX(
      targetRow,
      logicalCurrentCenterX,
      targetCol,
      metrics,
    )
    return closestByViewport?.element || entryTarget?.element || null
  }
}

/**
 * Focus an element and scroll it into view
 */
function focusElement(element: HTMLElement | null) {
  if (!element) return

  // Remove focus from previous element
  if (focusedElement.value && focusedElement.value !== element) {
    focusedElement.value.classList.remove("nav-focused")
    focusedElement.value.blur()
  }

  // Add focus to new element
  element.classList.add("nav-focused")
  element.focus({ preventScroll: true })

  ensureElementVisibleVertically(element)
  syncRowsToElement(element)

  focusedElement.value = element
}

/**
 * Get current focus state (row, col) for saving
 */
function getFocusState(): { row: number; col: number } | null {
  if (!focusedElement.value) return null
  const row = parseInt(focusedElement.value.getAttribute(ROW_ATTR) || "0", 10)
  const col = parseInt(focusedElement.value.getAttribute(COL_ATTR) || "0", 10)
  return { row, col }
}

/**
 * Restore focus to element with given row/col
 */
function restoreFocusState(state: { row: number; col: number } | null) {
  if (!state) return

  const target = findElementAt(state.row, state.col)
  if (target) {
    setTimeout(() => {
      focusElement(target.element)
    }, 50)
  }
}

/**
 * Focus element at specific row/col after a delay (for page transitions)
 */
function focusAt(row: number, col: number, delay: number = 100) {
  setTimeout(() => {
    const target = findElementAt(row, col)
    if (target) {
      focusElement(target.element)
    }
  }, delay)
}

/**
 * Check if we should allow navigation from an input element
 */
function shouldAllowNavigationFromInput(target: HTMLElement, direction: string): boolean {
  if (target.tagName !== "INPUT" && target.tagName !== "TEXTAREA" && !target.isContentEditable) {
    return true // Not an input, allow navigation
  }

  // Always allow up/down navigation from inputs
  if (direction === "up" || direction === "down") {
    return true
  }

  // For left/right, only capture if input is empty
  const inputEl = target as HTMLInputElement | HTMLTextAreaElement
  const value = inputEl.value || ""
  return value.length === 0
}

/**
 * Handle keyboard navigation
 */
function handleKeyDown(event: KeyboardEvent) {
  const target = event.target as HTMLElement

  const direction = {
    ArrowUp: "up",
    ArrowDown: "down",
    ArrowLeft: "left",
    ArrowRight: "right",
  }[event.key] as "up" | "down" | "left" | "right" | undefined

  if (!direction) return

  // Check if we should allow navigation from this element
  if (!shouldAllowNavigationFromInput(target, direction)) {
    return
  }

  event.preventDefault()
  isNavigating.value = true

  // Get current focused element or find the first one
  let current = focusedElement.value

  // If no element is focused, try to get the currently focused element from DOM
  if (!current) {
    const activeElement = document.activeElement as HTMLElement
    if (activeElement && activeElement.hasAttribute(FOCUSABLE_ATTR)) {
      current = activeElement
    }
  }

  // If still no current, focus the first available element
  if (!current) {
    const elements = getFocusableElements()
    if (elements.length > 0) {
      focusElement(elements[0].element)
    }
    return
  }

  // Find and focus the next element using index-based navigation
  const next = findNextElement(current, direction)
  if (next) {
    focusElement(next)
  }
}

/**
 * Handle Enter key to activate focused element
 */
function handleEnterKey(event: KeyboardEvent) {
  if (event.key !== "Enter") return
  if (event.defaultPrevented) return
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return

  const target = event.target as HTMLElement
  if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") {
    return
  }

  if (focusedElement.value) {
    event.preventDefault()
    focusedElement.value.click()
  }
}

/**
 * Install global keyboard navigation handlers
 * Should be called once at app initialization
 */
export function installKeyboardNavigation() {
  if (handlersInstalled) return
  handlersInstalled = true

  resetSyncedRows(true)

  document.addEventListener("keydown", handleKeyDown)
  document.addEventListener("keydown", handleEnterKey)
  window.addEventListener("resize", handleSyncedRowResize, { passive: true })

  // Handle mouse clicks to update focus state
  document.addEventListener("click", (event) => {
    const target = event.target as HTMLElement
    const focusable = target.closest(`[${FOCUSABLE_ATTR}]`) as HTMLElement | null
    if (focusable) {
      desiredCol.value = null // Reset desired col on mouse click
      focusElement(focusable)
    }
  })

  // Middle-click commonly opens links in a background tab.
  // Scroll after activation so the source page keeps the clicked item in the safe zone.
  document.addEventListener("auxclick", (event) => {
    if (event.button !== 1) return
    const target = event.target as HTMLElement
    const focusable = target.closest(`[${FOCUSABLE_ATTR}]`) as HTMLElement | null
    if (focusable) {
      desiredCol.value = null
      focusElement(focusable)
    }
  })

  // Handle focus events from tab navigation
  document.addEventListener("focusin", (event) => {
    const target = event.target as HTMLElement
    if (target.hasAttribute(FOCUSABLE_ATTR)) {
      if (focusedElement.value && focusedElement.value !== target) {
        focusedElement.value.classList.remove("nav-focused")
      }
      focusedElement.value = target
      target.classList.add("nav-focused")
      desiredCol.value = null // Reset desired col on focus change
    } else {
      resetSyncedRows()
    }
  })
}

/**
 * Composable to access keyboard navigation state
 * @deprecated Use installKeyboardNavigation() at app init instead
 */
export function useKeyboardNavigation() {
  return {
    focusedElement,
    isNavigating,
    focusElement,
    focusAt,
    getFocusState,
    restoreFocusState,
  }
}

/**
 * Helper to generate navigation attributes for a focusable element
 * @param entryCol - optional column to focus when entering this row vertically
 */
export function navAttrs(row: number, col: number, entryCol?: number) {
  const attrs: Record<string, string | number> = {
    [FOCUSABLE_ATTR]: "true",
    [ROW_ATTR]: String(row),
    [COL_ATTR]: String(col),
    tabindex: 0,
  }
  if (entryCol !== undefined) {
    attrs[ENTRY_COL_ATTR] = String(entryCol)
  }
  return attrs
}

export { FOCUSABLE_ATTR, ROW_ATTR, COL_ATTR, ENTRY_COL_ATTR }
