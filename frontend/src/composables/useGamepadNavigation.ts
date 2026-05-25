type GamepadAction = "up" | "down" | "left" | "right" | "select" | "back" | "menu"

const GAMEPAD_AXIS_THRESHOLD = 0.55
const GAMEPAD_REPEAT_MS = 180

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

const gamepadLastTriggerAt: Record<GamepadAction, number> = {
  up: 0,
  down: 0,
  left: 0,
  right: 0,
  select: 0,
  back: 0,
  menu: 0,
}

let gamepadFrameId: number | null = null
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

function applyGamepadAction(action: GamepadAction, isPressed: boolean, now: number) {
  const wasPressed = gamepadPressedState[action]
  gamepadPressedState[action] = isPressed

  if (!isPressed) return

  const shouldRepeat =
    action === "up" || action === "down" || action === "left" || action === "right"
  const canTrigger =
    !wasPressed || (shouldRepeat && now - gamepadLastTriggerAt[action] >= GAMEPAD_REPEAT_MS)
  if (!canTrigger) return

  gamepadLastTriggerAt[action] = now
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

function resetPressedState() {
  gamepadPressedState.up = false
  gamepadPressedState.down = false
  gamepadPressedState.left = false
  gamepadPressedState.right = false
  gamepadPressedState.select = false
  gamepadPressedState.back = false
  gamepadPressedState.menu = false
}

function pollGamepad() {
  const gamepads = navigator.getGamepads?.() ?? []
  const now = performance.now()
  const connectedGamepads = gamepads.filter((gp): gp is Gamepad => Boolean(gp && gp.connected))

  if (connectedGamepads.length > 0) {
    let up = false
    let down = false
    let left = false
    let right = false
    let select = false
    let back = false
    let menu = false

    for (const gamepad of connectedGamepads) {
      const axisX = gamepad.axes[0] ?? 0
      const axisY = gamepad.axes[1] ?? 0

      up = up || Boolean(gamepad.buttons[12]?.pressed) || axisY <= -GAMEPAD_AXIS_THRESHOLD
      down = down || Boolean(gamepad.buttons[13]?.pressed) || axisY >= GAMEPAD_AXIS_THRESHOLD
      left = left || Boolean(gamepad.buttons[14]?.pressed) || axisX <= -GAMEPAD_AXIS_THRESHOLD
      right = right || Boolean(gamepad.buttons[15]?.pressed) || axisX >= GAMEPAD_AXIS_THRESHOLD

      // Xbox mapping on standard gamepads: A=0, B=1
      select = select || Boolean(gamepad.buttons[0]?.pressed)
      back = back || Boolean(gamepad.buttons[1]?.pressed)
      // Y/Triangle button opens contextual release list where supported.
      menu = menu || Boolean(gamepad.buttons[3]?.pressed)
    }

    applyGamepadAction("up", up, now)
    applyGamepadAction("down", down, now)
    applyGamepadAction("left", left, now)
    applyGamepadAction("right", right, now)
    applyGamepadAction("select", select, now)
    applyGamepadAction("back", back, now)
    applyGamepadAction("menu", menu, now)
  } else {
    resetPressedState()
  }

  gamepadFrameId = window.requestAnimationFrame(pollGamepad)
}

export function installGamepadNavigation() {
  if (gamepadInstalled) return
  gamepadInstalled = true
  gamepadFrameId = window.requestAnimationFrame(pollGamepad)
}

export function uninstallGamepadNavigation() {
  if (!gamepadInstalled) return
  gamepadInstalled = false
  if (gamepadFrameId !== null) {
    window.cancelAnimationFrame(gamepadFrameId)
    gamepadFrameId = null
  }
  resetPressedState()
}
