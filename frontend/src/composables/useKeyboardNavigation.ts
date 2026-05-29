import { ref } from "vue"

export interface FocusableElement {
  element: HTMLElement
  row: number
  col: number
}

export type NavDirection = "up" | "down" | "left" | "right"

export interface OutOfBoundsNavigationContext {
  current: HTMLElement
  direction: NavDirection
  currentRow: number
  currentCol: number
  byRow: Map<number, FocusableElement[]>
}

export type OutOfBoundsNavigationHandler = (
  context: OutOfBoundsNavigationContext,
) => HTMLElement | null | undefined

// Global focus state
const focusedElement = ref<HTMLElement | null>(null)
const isNavigating = ref(false)
const activeNavigationScope = ref<string | null>(null)
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
const SYNC_SCROLL_RIGHT_DEADZONE_VAR = "--sync-row-right-deadzone"

// ---------------------------------------------------------------------------
// Synced scroll state — global virtual offset, per-row clamping
// ---------------------------------------------------------------------------

let syncedRowsFrame: number | null = null
let syncedRowsCurrentOffset = 0
let syncedRowsTargetOffset = 0
let lastSyncedAnchorCol: number | null = null
let lastSyncedRowsAnimationAt: number | null = null

// Global metrics measured once from the first synced row. All calculations use
// these same values for every row to avoid per-row DOM query inconsistencies.
interface ScrollMetrics {
  cardWidth: number
  stride: number // cardWidth + gap
  paddingLeft: number
  viewportWidth: number
  rightDeadzone: number
}
let gMetrics: ScrollMetrics | null = null

function invalidateMetrics() {
  gMetrics = null
}

function measureGlobalMetrics(): boolean {
  const rows = getSyncedRows()
  for (const row of rows) {
    const cards = row.querySelectorAll<HTMLElement>(`.media-card[${FOCUSABLE_ATTR}]`)
    if (cards.length < 2) continue
    const r1 = cards[0].getBoundingClientRect()
    const r2 = cards[1].getBoundingClientRect()
    if (r1.width <= 0) continue
    const cardWidth = r1.width
    const stride = r2.left - r1.left
    const rowStyle = window.getComputedStyle(row)
    const paddingLeft = parseFloat(rowStyle.paddingLeft || "0")
    const viewportWidth = row.clientWidth
    const deadzoneInset = Math.max(paddingLeft, (viewportWidth - cardWidth) * SYNC_SCROLL_DEADZONE_RATIO)
    const rightDeadzoneRaw = rowStyle.getPropertyValue(SYNC_SCROLL_RIGHT_DEADZONE_VAR).trim()
    const rightDeadzone = Number.isFinite(parseFloat(rightDeadzoneRaw))
      ? Math.max(0, parseFloat(rightDeadzoneRaw))
      : deadzoneInset
    gMetrics = { cardWidth, stride, paddingLeft, viewportWidth, rightDeadzone }
    return true
  }
  return false
}

function getMetrics(): ScrollMetrics | null {
  if (!gMetrics) {
    measureGlobalMetrics()
  }
  return gMetrics
}

// On first navigation into a view, snap the global virtual offset to whatever
// the first synced row is already scrolled to. This avoids animating from 0
// every time the view is entered.
function initCurrentOffsetFromDOM() {
  if (syncedRowsCurrentOffset !== 0) return
  const rows = getSyncedRows()
  for (const row of rows) {
    const sl = row.scrollLeft
    if (sl > 0) {
      syncedRowsCurrentOffset = sl
      syncedRowsTargetOffset = sl
      return
    }
  }
}

function getSyncedRows(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>(`[${SYNC_SCROLL_ROW_ATTR}="true"]`))
}

function getRowItemCount(row: HTMLElement): number {
  return row.querySelectorAll<HTMLElement>(`.media-card[${FOCUSABLE_ATTR}]`).length
}

// The maximum scroll offset for a row: the exact offset that would put its
// last item at the right edge of the safe zone. Computed purely from global
// metrics and item count — no DOM queries.
function getRowMaxScroll(row: HTMLElement): number {
  const m = getMetrics()
  if (!m) return 0
  const n = getRowItemCount(row)
  if (n === 0) return 0
  const lastCol = n - 1
  const lastItemLeft = m.paddingLeft + lastCol * m.stride
  const maxVisibleLeft = Math.max(
    m.paddingLeft,
    m.viewportWidth - m.cardWidth - m.rightDeadzone,
  )
  return Math.max(0, lastItemLeft - maxVisibleLeft)
}

function clampRowScrollOffset(row: HTMLElement, offset: number): number {
  return Math.min(Math.max(offset, 0), getRowMaxScroll(row))
}

function applySyncedRowScroll(offset: number, rows: HTMLElement[] = getSyncedRows()) {
  for (const row of rows) {
    row.scrollLeft = clampRowScrollOffset(row, offset)
  }
}

function setAllRowTails(tailPx: number, rows: HTMLElement[] = getSyncedRows()) {
  const value = `${Math.max(0, tailPx)}px`
  for (const row of rows) {
    row.style.setProperty(SYNC_SCROLL_TAIL_VAR, value)
  }
}

function resetSyncedRows(immediate: boolean = false) {
  lastSyncedAnchorCol = null
  syncedRowsTargetOffset = 0
  invalidateMetrics()
  setAllRowTails(0)

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

  startSyncedRowAnimation()
}

function startSyncedRowAnimation() {
  if (syncedRowsFrame !== null) return
  syncedRowsFrame = window.requestAnimationFrame(animateSyncedRows)
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

  initCurrentOffsetFromDOM()

  const m = getMetrics()
  if (!m) return

  const itemLeft = m.paddingLeft + anchorCol * m.stride
  const maxVisibleLeft = Math.max(
    m.paddingLeft,
    m.viewportWidth - m.cardWidth - m.rightDeadzone,
  )

  // Ideal unclamped offset: position the anchor column at the right edge of
  // the safe zone, or 0 if it fits without scrolling.
  let desiredOffset = Math.max(0, itemLeft - maxVisibleLeft)

  // Clamp to the anchor row's own limit so the global target is always
  // reachable by the row that drove the navigation.
  if (anchorRow) {
    desiredOffset = Math.min(desiredOffset, getRowMaxScroll(anchorRow))
  }

  // If the anchor row fits entirely on screen, keep it left-aligned.
  if (anchorRow && getRowMaxScroll(anchorRow) === 0) {
    desiredOffset = 0
  }

  // Tail is a fixed viewport constant applied to all rows.
  const fixedTail = m.rightDeadzone
  let anyTailChanged = false
  for (const row of rows) {
    const currentTailRaw = row.style.getPropertyValue(SYNC_SCROLL_TAIL_VAR).trim()
    const currentTail = currentTailRaw ? parseFloat(currentTailRaw) : 0
    if (Math.abs(fixedTail - currentTail) >= 0.5) {
      row.style.setProperty(SYNC_SCROLL_TAIL_VAR, `${fixedTail}px`)
      anyTailChanged = true
    }
  }
  if (anyTailChanged) {
    // Force layout recalc so scrollWidth is up to date before we clamp.
    for (const row of rows) {
      void row.scrollWidth
    }
  }

  lastSyncedAnchorCol = anchorCol
  syncedRowsTargetOffset = desiredOffset

  if (!anchorRow) {
    setAllRowTails(0, rows)
    for (const row of rows) {
      void row.scrollWidth
    }
  }

  if (Math.abs(syncedRowsTargetOffset - syncedRowsCurrentOffset) < 0.5) {
    syncedRowsCurrentOffset = syncedRowsTargetOffset
    applySyncedRowScroll(syncedRowsCurrentOffset, rows)
    stopSyncedRowAnimation()
    return
  }

  startSyncedRowAnimation()
}

function getLocalSyncedRowCol(
  anchorRow: HTMLElement,
  element: HTMLElement,
  requestedCol: number,
): number {
  const cards = Array.from(anchorRow.querySelectorAll<HTMLElement>(`.media-card[${FOCUSABLE_ATTR}]`))
  if (cards.length === 0) return Math.max(0, requestedCol)

  const cardCols = cards
    .map((card) => parseInt(card.getAttribute(COL_ATTR) || "", 10))
    .filter((col) => Number.isFinite(col))

  if (cardCols.length > 0) {
    const minCol = Math.min(...cardCols)
    const localFromRequested = requestedCol - minCol
    return Math.max(0, Math.min(localFromRequested, cards.length - 1))
  }

  const fallbackIndex = cards.indexOf(element)
  if (fallbackIndex >= 0) return fallbackIndex

  return Math.max(0, Math.min(requestedCol, cards.length - 1))
}

function syncRowsToElement(element: HTMLElement) {
  const row = parseInt(element.getAttribute(ROW_ATTR) || "0", 10)
  if (row < SYNC_SCROLL_FIRST_CONTENT_ROW) {
    resetSyncedRows()
    return
  }

  const currentCol = parseInt(element.getAttribute(COL_ATTR) || "0", 10)
  const anchorRow = element.closest<HTMLElement>(`[${SYNC_SCROLL_ROW_ATTR}="true"]`)
  if (!anchorRow) {
    resetSyncedRows()
    return
  }

  const requestedCol = desiredCol.value ?? currentCol
  const anchorCol = getLocalSyncedRowCol(anchorRow, element, requestedCol)
  updateSyncedRowTarget(anchorCol, anchorRow)
}

function handleSyncedRowResize() {
  invalidateMetrics()
  if (lastSyncedAnchorCol === null) {
    resetSyncedRows(true)
    return
  }
  updateSyncedRowTarget(lastSyncedAnchorCol)
}

function ensureElementVisibleVertically(element: HTMLElement) {
  if (element.hasAttribute("data-nav-release-item")) {
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
    return
  }

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

// ---------------------------------------------------------------------------
// Element finding / navigation (unchanged logic, uses getMetrics() now)
// ---------------------------------------------------------------------------

function resolveOutOfBoundsNavigation(
  context: OutOfBoundsNavigationContext,
): HTMLElement | null {
  const handlers = Array.from(outOfBoundsHandlers)
  for (let i = handlers.length - 1; i >= 0; i--) {
    const result = handlers[i]?.(context)
    if (result) return result
  }
  return null
}

const outOfBoundsHandlers = new Set<OutOfBoundsNavigationHandler>()

function getElementNavigationScope(element: HTMLElement): string | null {
  const owner = element.closest<HTMLElement>("[data-nav-scope]")
  return owner?.getAttribute("data-nav-scope") || null
}

function isElementInActiveScope(element: HTMLElement): boolean {
  const scope = activeNavigationScope.value
  if (!scope) return true
  const elementScope = getElementNavigationScope(element)
  // Elements without a scope (for example global header controls) stay reachable.
  if (!elementScope) return true
  return elementScope === scope
}

function clearFocusedElement() {
  if (!focusedElement.value) return
  focusedElement.value.classList.remove("nav-focused")
  focusedElement.value.blur()
  focusedElement.value = null
}

function getFocusableElements(): FocusableElement[] {
  const elements = document.querySelectorAll(`[${FOCUSABLE_ATTR}]`)
  const result: FocusableElement[] = []

  elements.forEach((el) => {
    const htmlEl = el as HTMLElement
    if (!isElementInActiveScope(htmlEl)) return
    if (htmlEl.offsetParent === null) return

    const rect = htmlEl.getBoundingClientRect()
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

function getElementsByRow(): Map<number, FocusableElement[]> {
  const elements = getFocusableElements()
  const byRow = new Map<number, FocusableElement[]>()

  for (const el of elements) {
    if (!byRow.has(el.row)) {
      byRow.set(el.row, [])
    }
    byRow.get(el.row)!.push(el)
  }

  for (const [, rowElements] of byRow) {
    rowElements.sort((a, b) => a.col - b.col)
  }

  return byRow
}

function findElementAt(
  row: number,
  col: number,
  useEntryCol: boolean = false,
): FocusableElement | null {
  const byRow = getElementsByRow()
  const rowElements = byRow.get(row)
  if (!rowElements || rowElements.length === 0) return null

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

  const exact = rowElements.find((e) => e.col === col)
  if (exact) return exact

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

function findNextElement(
  current: HTMLElement,
  direction: NavDirection,
): HTMLElement | null {
  const currentRow = parseInt(current.getAttribute(ROW_ATTR) || "0", 10)
  const currentCol = parseInt(current.getAttribute(COL_ATTR) || "0", 10)
  const byRow = getElementsByRow()

  if (direction === "left" || direction === "right") {
    desiredCol.value = null

    const rowElements = byRow.get(currentRow)
    if (!rowElements) return null

    const delta = direction === "right" ? 1 : -1
    const targetCol = currentCol + delta

    const target = rowElements.find((e) => e.col === targetCol)
    if (target?.element) return target.element

    return resolveOutOfBoundsNavigation({
      current,
      direction,
      currentRow,
      currentCol,
      byRow,
    })
  } else {
    const sortedRows = Array.from(byRow.keys()).sort((a, b) => a - b)
    const currentRowIdx = sortedRows.indexOf(currentRow)

    if (currentRowIdx === -1) return null

    const delta = direction === "down" ? 1 : -1
    const targetRowIdx = currentRowIdx + delta

    if (targetRowIdx < 0 || targetRowIdx >= sortedRows.length) {
      return resolveOutOfBoundsNavigation({
        current,
        direction,
        currentRow,
        currentCol,
        byRow,
      })
    }

    const targetRow = sortedRows[targetRowIdx]
    const targetCol = desiredCol.value ?? currentCol

    if (desiredCol.value === null) {
      desiredCol.value = currentCol
    }

    const entryTarget = findElementAt(targetRow, targetCol, true)
    const targetRowElements = byRow.get(targetRow) ?? []
    const hasEntryOverride = targetRowElements.some((el) => el.element.hasAttribute(ENTRY_COL_ATTR))
    if (hasEntryOverride) {
      return entryTarget?.element || null
    }

    const m = getMetrics()
    const currentRect = current.getBoundingClientRect()
    const actualCurrentCenterX = currentRect.left + currentRect.width / 2
    const closestByViewport = findElementClosestToLogicalViewportX(
      targetRow,
      actualCurrentCenterX,
      targetCol,
      m,
    )
    const resolved = closestByViewport?.element || entryTarget?.element || null
    if (resolved) return resolved

    return resolveOutOfBoundsNavigation({
      current,
      direction,
      currentRow,
      currentCol,
      byRow,
    })
  }
}

function focusElement(element: HTMLElement | null) {
  if (!element) return
  if (!isElementInActiveScope(element)) return

  if (focusedElement.value && focusedElement.value !== element) {
    focusedElement.value.classList.remove("nav-focused")
    focusedElement.value.blur()
  }

  element.classList.add("nav-focused")
  element.focus({ preventScroll: true })

  ensureElementVisibleVertically(element)
  syncRowsToElement(element)

  focusedElement.value = element
}

function getFocusState(): { row: number; col: number } | null {
  if (!focusedElement.value) return null
  const row = parseInt(focusedElement.value.getAttribute(ROW_ATTR) || "0", 10)
  const col = parseInt(focusedElement.value.getAttribute(COL_ATTR) || "0", 10)
  return { row, col }
}

function restoreFocusState(state: { row: number; col: number } | null) {
  if (!state) return

  const target = findElementAt(state.row, state.col)
  if (target) {
    setTimeout(() => {
      focusElement(target.element)
    }, 50)
  }
}

function focusAt(row: number, col: number, delay: number = 100) {
  setTimeout(() => {
    const target = findElementAt(row, col)
    if (target) {
      focusElement(target.element)
    }
  }, delay)
}

function shouldAllowNavigationFromInput(target: HTMLElement, direction: string): boolean {
  if (target.tagName !== "INPUT" && target.tagName !== "TEXTAREA" && !target.isContentEditable) {
    return true
  }

  if (direction === "up" || direction === "down") {
    return true
  }

  const inputEl = target as HTMLInputElement | HTMLTextAreaElement
  const value = inputEl.value || ""
  return value.length === 0
}

function handleKeyDown(event: KeyboardEvent) {
  const target = event.target as HTMLElement

  const direction = {
    ArrowUp: "up",
    ArrowDown: "down",
    ArrowLeft: "left",
    ArrowRight: "right",
  }[event.key] as "up" | "down" | "left" | "right" | undefined

  if (!direction) return

  if (!shouldAllowNavigationFromInput(target, direction)) {
    return
  }

  event.preventDefault()
  isNavigating.value = true

  let current = focusedElement.value
  if (current && !isElementInActiveScope(current)) {
    current = null
  }

  if (!current) {
    const activeElement = document.activeElement as HTMLElement
    if (
      activeElement &&
      activeElement.hasAttribute(FOCUSABLE_ATTR) &&
      isElementInActiveScope(activeElement)
    ) {
      current = activeElement
    }
  }

  if (!current) {
    const elements = getFocusableElements()
    if (elements.length > 0) {
      focusElement(elements[0].element)
    }
    return
  }

  const next = findNextElement(current, direction)
  if (next) {
    focusElement(next)
  }
}

function handleEnterKey(event: KeyboardEvent) {
  if (event.key !== "Enter") return
  if (event.defaultPrevented) return
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return

  const target = event.target as HTMLElement
  if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") {
    return
  }

  if (focusedElement.value && isElementInActiveScope(focusedElement.value)) {
    event.preventDefault()
    focusedElement.value.click()
  }
}

export function setActiveNavigationScope(scope: string | null) {
  if (activeNavigationScope.value === scope) return
  activeNavigationScope.value = scope
  desiredCol.value = null
  resetSyncedRows(true)

  if (focusedElement.value && !isElementInActiveScope(focusedElement.value)) {
    clearFocusedElement()
  }
}

export function installKeyboardNavigation() {
  if (handlersInstalled) return
  handlersInstalled = true

  resetSyncedRows(true)

  document.addEventListener("keydown", handleKeyDown)
  document.addEventListener("keydown", handleEnterKey)
  window.addEventListener("resize", handleSyncedRowResize, { passive: true })

  document.addEventListener("click", (event) => {
    const target = event.target as HTMLElement
    const focusable = target.closest(`[${FOCUSABLE_ATTR}]`) as HTMLElement | null
    if (focusable && isElementInActiveScope(focusable)) {
      desiredCol.value = null
      focusElement(focusable)
    }
  })

  document.addEventListener("auxclick", (event) => {
    if (event.button !== 1) return
    const target = event.target as HTMLElement
    const focusable = target.closest(`[${FOCUSABLE_ATTR}]`) as HTMLElement | null
    if (focusable && isElementInActiveScope(focusable)) {
      desiredCol.value = null
      focusElement(focusable)
    }
  })

  document.addEventListener("focusin", (event) => {
    const target = event.target as HTMLElement
    if (target.hasAttribute(FOCUSABLE_ATTR) && isElementInActiveScope(target)) {
      if (focusedElement.value && focusedElement.value !== target) {
        focusedElement.value.classList.remove("nav-focused")
      }
      focusedElement.value = target
      target.classList.add("nav-focused")
      desiredCol.value = null
    } else {
      resetSyncedRows()
    }
  })
}

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

export function registerOutOfBoundsNavigationHandler(handler: OutOfBoundsNavigationHandler) {
  outOfBoundsHandlers.add(handler)
  return () => {
    outOfBoundsHandlers.delete(handler)
  }
}

export { FOCUSABLE_ATTR, ROW_ATTR, COL_ATTR, ENTRY_COL_ATTR }
