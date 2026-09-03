import { onUnmounted, ref, type Ref } from "vue"

export const PREVIEW_IDLE_MS = 30_000

const ACTIVITY_EVENTS = ["mousemove", "mousedown", "wheel", "keydown", "touchstart"] as const
const GAMEPAD_ACTIVITY_EVENT = "mediahive:gamepad-action"

interface IdlePreviewPlaybackOptions {
  idleMs?: number
  onStop: () => void
  onRestart: () => void
}

// Stops preview videos after a period without user input and restarts them
// (via the component's staggered startup) when activity resumes. Also stops
// previews while the tab is hidden. Activity listeners run in the capture
// phase so the stopped flag clears before hover/focus handlers react.
export function useIdlePreviewPlayback(options: IdlePreviewPlaybackOptions): {
  stopped: Ref<boolean>
} {
  const idleMs = options.idleMs ?? PREVIEW_IDLE_MS
  const stopped = ref(false)
  let idleTimer: ReturnType<typeof setTimeout> | null = null

  function clearIdleTimer() {
    if (idleTimer !== null) {
      clearTimeout(idleTimer)
      idleTimer = null
    }
  }

  function stop() {
    clearIdleTimer()
    if (stopped.value) return
    stopped.value = true
    options.onStop()
  }

  function handleActivity() {
    if (document.hidden) return
    if (stopped.value) {
      stopped.value = false
      options.onRestart()
    }
    clearIdleTimer()
    idleTimer = setTimeout(stop, idleMs)
  }

  function handleVisibilityChange() {
    if (document.hidden) {
      stop()
    } else {
      handleActivity()
    }
  }

  for (const eventName of ACTIVITY_EVENTS) {
    window.addEventListener(eventName, handleActivity, { passive: true, capture: true })
  }
  window.addEventListener(GAMEPAD_ACTIVITY_EVENT, handleActivity, { passive: true, capture: true })
  document.addEventListener("visibilitychange", handleVisibilityChange)
  idleTimer = setTimeout(stop, idleMs)

  onUnmounted(() => {
    for (const eventName of ACTIVITY_EVENTS) {
      window.removeEventListener(eventName, handleActivity, { capture: true })
    }
    window.removeEventListener(GAMEPAD_ACTIVITY_EVENT, handleActivity, { capture: true })
    document.removeEventListener("visibilitychange", handleVisibilityChange)
    clearIdleTimer()
  })

  return { stopped }
}
