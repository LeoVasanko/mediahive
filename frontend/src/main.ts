import { createApp } from "vue"
import App from "./App.vue"
import router from "./router"
import "./styles/main.css"
import { installKeyboardNavigation } from "./composables/useKeyboardNavigation"
import { installGamepadNavigation } from "./composables/useGamepadNavigation"
import { installInputModalityTracking } from "./composables/useInputModality"

function postClientError(payload: {
  message: string
  stack: string | null
  source: string | null
}) {
  fetch("/api/client-log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).catch(() => {})
}

function installErrorCapture() {
  window.addEventListener("error", (event) => {
    const source =
      event.filename != null ? `${event.filename}:${event.lineno ?? 0}:${event.colno ?? 0}` : null
    postClientError({
      message: event.message || String(event.error ?? "Unknown error"),
      stack: event.error?.stack ?? null,
      source,
    })
  })
  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason
    postClientError({
      message: reason instanceof Error ? reason.message : `Unhandled rejection: ${String(reason)}`,
      stack: reason instanceof Error ? (reason.stack ?? null) : null,
      source: "unhandledrejection",
    })
  })
}

function installReloadShortcut() {
  document.addEventListener(
    "keydown",
    (event) => {
      if (
        event.key === "F5" ||
        ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "r")
      ) {
        event.preventDefault()
        window.location.reload()
      }
    },
    { capture: true },
  )
}

// Install global keyboard navigation handlers immediately
installInputModalityTracking()
installKeyboardNavigation()
installGamepadNavigation()
installReloadShortcut()
installErrorCapture()

// Unregister any legacy service workers — MediaHive no longer uses a PWA/SW.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.getRegistrations().then((registrations) => {
    for (const registration of registrations) {
      registration.unregister()
    }
  })
}

createApp(App).use(router).mount("#app")
