import { ref } from "vue"

// Settings is an overlay, not a route: opening it must not change the URL or
// the view behind it, so the open state is plain shared local state.
const settingsOpen = ref(false)

export function useSettingsOpen() {
  return settingsOpen
}
