import { createApp } from "vue"
import App from "./App.vue"
import router from "./router"
import "./styles/main.css"
import { installKeyboardNavigation } from "./composables/useKeyboardNavigation"
import { installGamepadNavigation } from "./composables/useGamepadNavigation"
import { installInputModalityTracking } from "./composables/useInputModality"

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

// Unregister any legacy service workers — MediaHive no longer uses a PWA/SW.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.getRegistrations().then((registrations) => {
    for (const registration of registrations) {
      registration.unregister()
    }
  })
}

createApp(App).use(router).mount("#app")
