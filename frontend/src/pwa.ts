import { registerSW } from 'virtual:pwa-register'

export function registerMediaHivePwa(): void {
  const updateServiceWorker = registerSW({
    immediate: true,
    onNeedRefresh: () => void updateServiceWorker(true),
  })
}
