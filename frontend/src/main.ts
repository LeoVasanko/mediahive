import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './styles/main.css'
import { installKeyboardNavigation } from './composables/useKeyboardNavigation'
import { installGamepadNavigation } from './composables/useGamepadNavigation'

// Install global keyboard navigation handlers immediately
installKeyboardNavigation()
installGamepadNavigation()

createApp(App).use(router).mount('#app')
