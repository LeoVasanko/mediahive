type GamepadAction = "up" | "down" | "left" | "right" | "select" | "back" | "menu"
type DirectionAction = "up" | "down" | "left" | "right"

const ANALOG_DEADZONE = 0.25
const DIGITAL_INITIAL_REPEAT_DELAY_MS = 150
const DIGITAL_REPEAT_START_MS = 90
const DIGITAL_REPEAT_MIN_MS = 16
const DIGITAL_ACCEL_RAMP_MS = 3500
const ANALOG_REPEAT_MAX_MS = 180
const ANALOG_REPEAT_MIN_MS = 16
const IDLE_POLL_MS = 500

const KEY_BY_ACTION: Partial<Record<GamepadAction, string>> = {
  up: "ArrowUp",
  down: "ArrowDown",
  left: "ArrowLeft",
  right: "ArrowRight",
  select: "Enter",
  back: "Escape",
}

const gamepadPressedState: Record<GamepadAction, boolean> = {
  up: false,
  down: false,
  left: false,
  right: false,
  select: false,
  back: false,
  menu: false,
}

const rawButtonPressedState: Record<number, boolean> = {
  2: false,
  3: false,
  4: false,
  5: false,
}

const analogLastTriggerAt: Record<DirectionAction, number> = {
  up: 0,
  down: 0,
  left: 0,
  right: 0,
}

let digitalHoldStartedAt = 0
let digitalLastRepeatAt = 0
let digitalRepeatCount = 0

let gamepadFrameId: number | null = null
let idleTimerId: number | null = null
let gamepadInstalled = false

function dispatchKey(key: string) {
  const active = document.activeElement
  const target = active instanceof HTMLElement ? active : document
  target.dispatchEvent(
    new KeyboardEvent("keydown", {
      key,
      bubbles: true,
      cancelable: true,
    }),
  )
}

function dispatchGamepadAction(action: GamepadAction): void {
  const actionEvent = new CustomEvent("mediahive:gamepad-action", {
    detail: { action },
    cancelable: true,
  })
  const shouldContinueWithKeyboard = window.dispatchEvent(actionEvent)
  if (!shouldContinueWithKeyboard) return

  const key = KEY_BY_ACTION[action]
  if (key) {
    dispatchKey(key)
  }
}

function dispatchRawButton(button: number): void {
  const event = new CustomEvent("mediahive:gamepad-button", {
    detail: { button },
    cancelable: true,
  })
  window.dispatchEvent(event)
}

function triggerSinglePressAction(action: GamepadAction, isPressed: boolean): void {
  const wasPressed = gamepadPressedState[action]
  gamepadPressedState[action] = isPressed
  if (isPressed && !wasPressed) {
    dispatchGamepadAction(action)
  }
}

function triggerSinglePressRawButton(button: number, isPressed: boolean): void {
  const wasPressed = rawButtonPressedState[button] ?? false
  rawButtonPressedState[button] = isPressed
  if (isPressed && !wasPressed) {
    dispatchRawButton(button)
  }
}

function getDigitalRepeatIntervalMs(holdMs: number): number {
  const progress = Math.min(Math.max(holdMs, 0), DIGITAL_ACCEL_RAMP_MS) / DIGITAL_ACCEL_RAMP_MS
  return Math.round(
    DIGITAL_REPEAT_START_MS - (DIGITAL_REPEAT_START_MS - DIGITAL_REPEAT_MIN_MS) * progress,
  )
}

function getAnalogRepeatIntervalMs(intensity: number): number {
  const normalized = Math.min(Math.max(intensity, 0), 1)
  return Math.round(
    ANALOG_REPEAT_MAX_MS - (ANALOG_REPEAT_MAX_MS - ANALOG_REPEAT_MIN_MS) * normalized,
  )
}

function normalizeAxisIntensity(rawValue: number): number {
  if (rawValue <= ANALOG_DEADZONE) return 0
  return Math.min((rawValue - ANALOG_DEADZONE) / (1 - ANALOG_DEADZONE), 1)
}

function applyAnalogDirection(action: DirectionAction, intensity: number, now: number): void {
  if (intensity <= 0) {
    analogLastTriggerAt[action] = 0
    return
  }

  const last = analogLastTriggerAt[action]
  const repeatMs = getAnalogRepeatIntervalMs(intensity)

  if (last === 0 || now - last >= repeatMs) {
    analogLastTriggerAt[action] = now
    dispatchGamepadAction(action)
  }
}

function resetPressedState() {
  gamepadPressedState.up = false
  gamepadPressedState.down = false
  gamepadPressedState.left = false
  gamepadPressedState.right = false
  gamepadPressedState.select = false
  gamepadPressedState.back = false
  gamepadPressedState.menu = false
  rawButtonPressedState[2] = false
  rawButtonPressedState[3] = false
  rawButtonPressedState[4] = false
  rawButtonPressedState[5] = false
  analogLastTriggerAt.up = 0
  analogLastTriggerAt.down = 0
  analogLastTriggerAt.left = 0
  analogLastTriggerAt.right = 0
  digitalHoldStartedAt = 0
  digitalLastRepeatAt = 0
  digitalRepeatCount = 0
}

function stopPolling() {
  if (gamepadFrameId !== null) {
    window.cancelAnimationFrame(gamepadFrameId)
    gamepadFrameId = null
  }
  if (idleTimerId !== null) {
    window.clearTimeout(idleTimerId)
    idleTimerId = null
  }
}

function scheduleIdlePoll() {
  if (idleTimerId !== null) return
  idleTimerId = window.setTimeout(() => {
    idleTimerId = null
    if (gamepadInstalled) {
      pollGamepad()
    }
  }, IDLE_POLL_MS)
}

function pollGamepad() {
  const gamepads = navigator.getGamepads?.() ?? []
  const now = performance.now()
  const connectedGamepads = gamepads.filter((gp): gp is Gamepad => Boolean(gp && gp.connected))

  if (connectedGamepads.length > 0) {
    let digitalUp = false
    let digitalDown = false
    let digitalLeft = false
    let digitalRight = false
    let analogUpIntensity = 0
    let analogDownIntensity = 0
    let analogLeftIntensity = 0
    let analogRightIntensity = 0
    let select = false
    let back = false
    let menu = false
    let raw2 = false
    let raw3 = false
    let raw4 = false
    let raw5 = false

    for (const gamepad of connectedGamepads) {
      const axisX = gamepad.axes[0] ?? 0
      const axisY = gamepad.axes[1] ?? 0

      digitalUp = digitalUp || Boolean(gamepad.buttons[12]?.pressed)
      digitalDown = digitalDown || Boolean(gamepad.buttons[13]?.pressed)
      digitalLeft = digitalLeft || Boolean(gamepad.buttons[14]?.pressed)
      digitalRight = digitalRight || Boolean(gamepad.buttons[15]?.pressed)

      const upIntensity = normalizeAxisIntensity(-axisY)
      const downIntensity = normalizeAxisIntensity(axisY)
      const leftIntensity = normalizeAxisIntensity(-axisX)
      const rightIntensity = normalizeAxisIntensity(axisX)
      if (upIntensity > analogUpIntensity) analogUpIntensity = upIntensity
      if (downIntensity > analogDownIntensity) analogDownIntensity = downIntensity
      if (leftIntensity > analogLeftIntensity) analogLeftIntensity = leftIntensity
      if (rightIntensity > analogRightIntensity) analogRightIntensity = rightIntensity

      // Xbox mapping on standard gamepads: A=0, B=1, X=2, Y=3
      select = select || Boolean(gamepad.buttons[0]?.pressed)
      back = back || Boolean(gamepad.buttons[1]?.pressed)
      menu = menu || Boolean(gamepad.buttons[3]?.pressed)

      raw2 = raw2 || Boolean(gamepad.buttons[2]?.pressed)
      raw3 = raw3 || Boolean(gamepad.buttons[3]?.pressed)
      raw4 = raw4 || Boolean(gamepad.buttons[4]?.pressed)
      raw5 = raw5 || Boolean(gamepad.buttons[5]?.pressed)
    }

    triggerSinglePressAction("up", digitalUp)
    triggerSinglePressAction("down", digitalDown)
    triggerSinglePressAction("left", digitalLeft)
    triggerSinglePressAction("right", digitalRight)
    triggerSinglePressAction("select", select)
    triggerSinglePressAction("back", back)
    triggerSinglePressAction("menu", menu)
    triggerSinglePressRawButton(2, raw2)
    triggerSinglePressRawButton(3, raw3)
    triggerSinglePressRawButton(4, raw4)
    triggerSinglePressRawButton(5, raw5)

    const hasDigitalRepeatable =
      digitalUp || digitalDown || digitalLeft || digitalRight || raw2 || raw3 || raw4 || raw5

    if (!hasDigitalRepeatable) {
      digitalHoldStartedAt = 0
      digitalLastRepeatAt = 0
      digitalRepeatCount = 0
    } else {
      if (digitalHoldStartedAt === 0) {
        digitalHoldStartedAt = now
        digitalLastRepeatAt = now
        digitalRepeatCount = 0
      }

      const holdMs = now - digitalHoldStartedAt
      const repeatMs =
        digitalRepeatCount === 0
          ? DIGITAL_INITIAL_REPEAT_DELAY_MS
          : getDigitalRepeatIntervalMs(holdMs)

      if (now - digitalLastRepeatAt >= repeatMs) {
        if (digitalUp) dispatchGamepadAction("up")
        if (digitalDown) dispatchGamepadAction("down")
        if (digitalLeft) dispatchGamepadAction("left")
        if (digitalRight) dispatchGamepadAction("right")
        if (raw2) dispatchRawButton(2)
        if (raw3) dispatchRawButton(3)
        if (raw4) dispatchRawButton(4)
        if (raw5) dispatchRawButton(5)

        digitalLastRepeatAt = now
        digitalRepeatCount += 1
      }
    }

    // Analog repeat has no initial delay and speed increases with stick deflection.
    // If digital d-pad direction is held, it owns that direction to avoid duplicate repeats.
    applyAnalogDirection("up", digitalUp ? 0 : analogUpIntensity, now)
    applyAnalogDirection("down", digitalDown ? 0 : analogDownIntensity, now)
    applyAnalogDirection("left", digitalLeft ? 0 : analogLeftIntensity, now)
    applyAnalogDirection("right", digitalRight ? 0 : analogRightIntensity, now)

    // Keep using rAF while gamepads are active for responsive input
    gamepadFrameId = window.requestAnimationFrame(pollGamepad)
  } else {
    resetPressedState()
    // No gamepads connected — drop to slow polling to save CPU
    scheduleIdlePoll()
  }
}

function handleGamepadConnected() {
  if (!gamepadInstalled) return
  // A gamepad was plugged in; make sure we're polling
  stopPolling()
  gamepadFrameId = window.requestAnimationFrame(pollGamepad)
}

export function installGamepadNavigation() {
  if (gamepadInstalled) return
  gamepadInstalled = true
  gamepadFrameId = window.requestAnimationFrame(pollGamepad)
  window.addEventListener("gamepadconnected", handleGamepadConnected)
}

export function uninstallGamepadNavigation() {
  if (!gamepadInstalled) return
  gamepadInstalled = false
  stopPolling()
  resetPressedState()
  window.removeEventListener("gamepadconnected", handleGamepadConnected)
}
