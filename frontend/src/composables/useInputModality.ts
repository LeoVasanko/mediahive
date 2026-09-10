import { reportUserActivity } from "../api"

type InputModality = "mouse" | "keyboard" | "gamepad"


const MOUSE_IDLE_MS = 1400
const MOUSE_INTENT_DISTANCE_PX = 28
const MOUSE_INTENT_WINDOW_MS = 700
const MOUSE_INTENT_SELECTOR = [
  "[data-nav-focusable]",
  "button",
  "a[href]",
  "input",
  "select",
  "textarea",
  '[role="button"]',
  ".media-card",
  ".collage-item",
  ".episode-tile",
  ".version-row",
  ".ctx-btn",
  ".header-nav-item",
].join(",")

let installed = false
let modality: InputModality = "mouse"
let mouseIdleTimer: number | null = null
let pointerVisible = false
let mouseTravelPx = 0
let lastMouseMoveAt = 0

function clearMouseIdleTimer() {
  if (mouseIdleTimer !== null) {
    window.clearTimeout(mouseIdleTimer)
    mouseIdleTimer = null
  }
}

function applyInputState(mouseActive: boolean) {
  const root = document.documentElement
  root.classList.toggle("mouse-active", mouseActive)
  root.classList.toggle("pointer-visible", pointerVisible)
}

function scheduleMouseIdle() {
  clearMouseIdleTimer()
  mouseIdleTimer = window.setTimeout(() => {
    pointerVisible = false
    applyInputState(false)
  }, MOUSE_IDLE_MS)
}

function activateMouseInput() {
  modality = "mouse"
  pointerVisible = true
  applyInputState(true)
  scheduleMouseIdle()
}

function activateNonMouseInput(next: InputModality) {
  modality = next
  pointerVisible = false
  mouseTravelPx = 0
  clearMouseIdleTimer()
  applyInputState(false)
}

function showPointerFromMotion() {
  pointerVisible = true
  applyInputState(modality === "mouse")
  scheduleMouseIdle()
}

function isMouseIntentTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false
  return Boolean(target.closest(MOUSE_INTENT_SELECTOR))
}

function registerMouseIntentTravel(event: MouseEvent): boolean {
  const now = performance.now()
  if (now - lastMouseMoveAt > MOUSE_INTENT_WINDOW_MS) {
    mouseTravelPx = 0
  }
  lastMouseMoveAt = now

  const step = Math.hypot(event.movementX || 0, event.movementY || 0)
  mouseTravelPx += step
  if (mouseTravelPx >= MOUSE_INTENT_DISTANCE_PX) {
    mouseTravelPx = 0
    return true
  }
  return false
}

function handleMouseMove(event: MouseEvent) {
  reportUserActivity()
  showPointerFromMotion()

  if (modality === "mouse") {
    applyInputState(true)
    return
  }

  if (!isMouseIntentTarget(event.target)) return
  if (registerMouseIntentTravel(event)) {
    activateMouseInput()
  }
}

function handleMouseOver(event: MouseEvent) {
  if (modality === "mouse") return
  if (!isMouseIntentTarget(event.target)) return

  // Entering an interactive target indicates likely mouse intent.
  activateMouseInput()
}

function handleMouseIntentAction(event: MouseEvent | WheelEvent) {
  reportUserActivity()
  pointerVisible = true
  if (isMouseIntentTarget(event.target)) {
    activateMouseInput()
    return
  }

  applyInputState(modality === "mouse")
  scheduleMouseIdle()
}

function handleKeyboardActivity(event: KeyboardEvent) {
  if (event.metaKey || event.ctrlKey || event.altKey) return
  reportUserActivity()
  activateNonMouseInput("keyboard")
}

function handleGamepadActivity() {
  activateNonMouseInput("gamepad")
}

export function installInputModalityTracking() {
  if (installed) return
  installed = true

  applyInputState(false)

  window.addEventListener("mousemove", handleMouseMove, { passive: true })
  window.addEventListener("mouseover", handleMouseOver, { passive: true })
  window.addEventListener("mousedown", handleMouseIntentAction, { passive: true })
  window.addEventListener("wheel", handleMouseIntentAction, { passive: true })
  window.addEventListener("keydown", handleKeyboardActivity, { passive: true })
  window.addEventListener("mediahive:gamepad-action", handleGamepadActivity as EventListener)
}
