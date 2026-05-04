import { ref } from 'vue';

export interface FocusableElement {
  element: HTMLElement;
  row: number;
  col: number;
}

// Global focus state
const focusedElement = ref<HTMLElement | null>(null);
const isNavigating = ref(false);
// Track the "desired" column when moving vertically (to maintain column position across rows of different lengths)
const desiredCol = ref<number | null>(null);
// Track if global handlers are installed
let handlersInstalled = false;

// Data attribute names
const FOCUSABLE_ATTR = 'data-nav-focusable';
const ROW_ATTR = 'data-nav-row';
const COL_ATTR = 'data-nav-col';
const ENTRY_COL_ATTR = 'data-nav-entry-col';

/**
 * Get all focusable elements in the DOM, grouped by row
 */
function getFocusableElements(): FocusableElement[] {
  const elements = document.querySelectorAll(`[${FOCUSABLE_ATTR}]`);
  const result: FocusableElement[] = [];

  elements.forEach((el) => {
    const htmlEl = el as HTMLElement;
    // Skip hidden elements
    if (htmlEl.offsetParent === null) return;

    const rect = htmlEl.getBoundingClientRect();
    // Skip elements not in viewport or zero-sized
    if (rect.width === 0 || rect.height === 0) return;

    const row = parseInt(htmlEl.getAttribute(ROW_ATTR) || '0', 10);
    const col = parseInt(htmlEl.getAttribute(COL_ATTR) || '0', 10);

    result.push({
      element: htmlEl,
      row,
      col,
    });
  });

  return result;
}

/**
 * Get elements grouped by row
 */
function getElementsByRow(): Map<number, FocusableElement[]> {
  const elements = getFocusableElements();
  const byRow = new Map<number, FocusableElement[]>();

  for (const el of elements) {
    if (!byRow.has(el.row)) {
      byRow.set(el.row, []);
    }
    byRow.get(el.row)!.push(el);
  }

  // Sort each row by column
  for (const [, rowElements] of byRow) {
    rowElements.sort((a, b) => a.col - b.col);
  }

  return byRow;
}

/**
 * Find element by row and col indices
 * @param useEntryCol - if true, check for entry-col override on elements
 */
function findElementAt(row: number, col: number, useEntryCol: boolean = false): FocusableElement | null {
  const byRow = getElementsByRow();
  const rowElements = byRow.get(row);
  if (!rowElements || rowElements.length === 0) return null;

  // Check if any element in this row has an entry-col override
  if (useEntryCol) {
    for (const el of rowElements) {
      const entryCol = el.element.getAttribute(ENTRY_COL_ATTR);
      if (entryCol !== null) {
        const overrideCol = parseInt(entryCol, 10);
        const entryTarget = rowElements.find(e => e.col === overrideCol);
        if (entryTarget) return entryTarget;
      }
    }
  }

  // Find exact match or nearest col
  const exact = rowElements.find(e => e.col === col);
  if (exact) return exact;

  // Find nearest col in this row
  let nearest = rowElements[0];
  let nearestDist = Math.abs(nearest.col - col);

  for (const el of rowElements) {
    const dist = Math.abs(el.col - col);
    if (dist < nearestDist) {
      nearest = el;
      nearestDist = dist;
    }
  }

  return nearest;
}

/**
 * Find next element in direction using row/col indices
 */
function findNextElement(
  current: HTMLElement,
  direction: 'up' | 'down' | 'left' | 'right'
): HTMLElement | null {
  const currentRow = parseInt(current.getAttribute(ROW_ATTR) || '0', 10);
  const currentCol = parseInt(current.getAttribute(COL_ATTR) || '0', 10);
  const byRow = getElementsByRow();

  if (direction === 'left' || direction === 'right') {
    // Horizontal: move within same row by col index
    desiredCol.value = null; // Reset desired col on horizontal movement

    const rowElements = byRow.get(currentRow);
    if (!rowElements) return null;

    const delta = direction === 'right' ? 1 : -1;
    const targetCol = currentCol + delta;

    // Find element with target col in this row
    const target = rowElements.find(e => e.col === targetCol);
    return target?.element || null;
  } else {
    // Vertical: move to adjacent row, try to maintain column
    const sortedRows = Array.from(byRow.keys()).sort((a, b) => a - b);
    const currentRowIdx = sortedRows.indexOf(currentRow);

    if (currentRowIdx === -1) return null;

    const delta = direction === 'down' ? 1 : -1;
    const targetRowIdx = currentRowIdx + delta;

    if (targetRowIdx < 0 || targetRowIdx >= sortedRows.length) return null;

    const targetRow = sortedRows[targetRowIdx];

    // Use desired col if set, otherwise use current col
    const targetCol = desiredCol.value ?? currentCol;

    // Set desired col if not already set (first vertical move in a sequence)
    if (desiredCol.value === null) {
      desiredCol.value = currentCol;
    }

    // Use entry column hook for vertical navigation
    const target = findElementAt(targetRow, targetCol, true);
    return target?.element || null;
  }
}

/**
 * Focus an element and scroll it into view
 */
function focusElement(element: HTMLElement | null) {
  if (!element) return;

  // Remove focus from previous element
  if (focusedElement.value && focusedElement.value !== element) {
    focusedElement.value.classList.remove('nav-focused');
    focusedElement.value.blur();
  }

  // Add focus to new element
  element.classList.add('nav-focused');
  element.focus({ preventScroll: true });

  // Smooth scroll for vertical (block), instant for horizontal (inline)
  element.scrollIntoView({
    behavior: 'smooth',
    block: 'nearest',
    inline: 'nearest',
  });

  focusedElement.value = element;
}

/**
 * Get current focus state (row, col) for saving
 */
function getFocusState(): { row: number; col: number } | null {
  if (!focusedElement.value) return null;
  const row = parseInt(focusedElement.value.getAttribute(ROW_ATTR) || '0', 10);
  const col = parseInt(focusedElement.value.getAttribute(COL_ATTR) || '0', 10);
  return { row, col };
}

/**
 * Restore focus to element with given row/col
 */
function restoreFocusState(state: { row: number; col: number } | null) {
  if (!state) return;

  const target = findElementAt(state.row, state.col);
  if (target) {
    setTimeout(() => {
      focusElement(target.element);
    }, 50);
  }
}

/**
 * Focus element at specific row/col after a delay (for page transitions)
 */
function focusAt(row: number, col: number, delay: number = 100) {
  setTimeout(() => {
    const target = findElementAt(row, col);
    if (target) {
      focusElement(target.element);
    }
  }, delay);
}

/**
 * Check if we should allow navigation from an input element
 */
function shouldAllowNavigationFromInput(target: HTMLElement, direction: string): boolean {
  if (target.tagName !== 'INPUT' && target.tagName !== 'TEXTAREA' && !target.isContentEditable) {
    return true; // Not an input, allow navigation
  }

  // Always allow up/down navigation from inputs
  if (direction === 'up' || direction === 'down') {
    return true;
  }

  // For left/right, only capture if input is empty
  const inputEl = target as HTMLInputElement | HTMLTextAreaElement;
  const value = inputEl.value || '';
  return value.length === 0;
}

/**
 * Handle keyboard navigation
 */
function handleKeyDown(event: KeyboardEvent) {
  const target = event.target as HTMLElement;

  const direction = {
    ArrowUp: 'up',
    ArrowDown: 'down',
    ArrowLeft: 'left',
    ArrowRight: 'right',
  }[event.key] as 'up' | 'down' | 'left' | 'right' | undefined;

  if (!direction) return;

  // Check if we should allow navigation from this element
  if (!shouldAllowNavigationFromInput(target, direction)) {
    return;
  }

  event.preventDefault();
  isNavigating.value = true;

  // Get current focused element or find the first one
  let current = focusedElement.value;

  // If no element is focused, try to get the currently focused element from DOM
  if (!current) {
    const activeElement = document.activeElement as HTMLElement;
    if (activeElement && activeElement.hasAttribute(FOCUSABLE_ATTR)) {
      current = activeElement;
    }
  }

  // If still no current, focus the first available element
  if (!current) {
    const elements = getFocusableElements();
    if (elements.length > 0) {
      focusElement(elements[0].element);
    }
    return;
  }

  // Find and focus the next element using index-based navigation
  const next = findNextElement(current, direction);
  if (next) {
    focusElement(next);
  }
}

/**
 * Handle Enter key to activate focused element
 */
function handleEnterKey(event: KeyboardEvent) {
  if (event.key !== 'Enter') return;

  const target = event.target as HTMLElement;
  if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') {
    return;
  }

  if (focusedElement.value) {
    event.preventDefault();
    focusedElement.value.click();
  }
}

/**
 * Install global keyboard navigation handlers
 * Should be called once at app initialization
 */
export function installKeyboardNavigation() {
  if (handlersInstalled) return;
  handlersInstalled = true;

  document.addEventListener('keydown', handleKeyDown);
  document.addEventListener('keydown', handleEnterKey);

  // Handle mouse clicks to update focus state
  document.addEventListener('click', (event) => {
    const target = event.target as HTMLElement;
    const focusable = target.closest(`[${FOCUSABLE_ATTR}]`) as HTMLElement | null;
    if (focusable) {
      desiredCol.value = null; // Reset desired col on mouse click
      focusElement(focusable);
    }
  });

  // Handle focus events from tab navigation
  document.addEventListener('focusin', (event) => {
    const target = event.target as HTMLElement;
    if (target.hasAttribute(FOCUSABLE_ATTR)) {
      if (focusedElement.value && focusedElement.value !== target) {
        focusedElement.value.classList.remove('nav-focused');
      }
      focusedElement.value = target;
      target.classList.add('nav-focused');
      desiredCol.value = null; // Reset desired col on focus change
    }
  });
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
  };
}

/**
 * Helper to generate navigation attributes for a focusable element
 * @param entryCol - optional column to focus when entering this row vertically
 */
export function navAttrs(row: number, col: number, entryCol?: number) {
  const attrs: Record<string, string | number> = {
    [FOCUSABLE_ATTR]: 'true',
    [ROW_ATTR]: String(row),
    [COL_ATTR]: String(col),
    tabindex: 0,
  };
  if (entryCol !== undefined) {
    attrs[ENTRY_COL_ATTR] = String(entryCol);
  }
  return attrs;
}

export { FOCUSABLE_ATTR, ROW_ATTR, COL_ATTR, ENTRY_COL_ATTR };
