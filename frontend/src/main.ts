import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './styles/main.css'
import { installKeyboardNavigation } from './composables/useKeyboardNavigation'

// Install global keyboard navigation handlers immediately
installKeyboardNavigation()

createApp(App).use(router).mount('#app')
