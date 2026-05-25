<template>
  <div v-if="showDolbyLogo" class="dolby-badges" :class="{ compact }">
    <img :src="dolbyLogoSrc" :alt="dolbyLogoAlt" class="dolby-logo" />
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue"
import dolbyAtmosUrl from "../assets/dolby-atmos.webp"
import dolbyVisionUrl from "../assets/dolby-vision.webp"
import dolbyVisionAtmosUrl from "../assets/dolby-vision-atmos.webp"

const props = defineProps<{
  hasDolbyVision?: boolean | null
  hasDolbyAtmos?: boolean | null
  isHdr?: boolean | null
  compact?: boolean
}>()

const hasDolbyVision = computed(() => props.hasDolbyVision === true)
const hasDolbyAtmos = computed(() => props.hasDolbyAtmos === true)
const compact = computed(() => props.compact === true)

const showDolbyLogo = computed(() => hasDolbyVision.value || hasDolbyAtmos.value)

const dolbyLogoSrc = computed(() => {
  if (hasDolbyVision.value && hasDolbyAtmos.value) return dolbyVisionAtmosUrl
  if (hasDolbyVision.value) return dolbyVisionUrl
  return dolbyAtmosUrl
})

const dolbyLogoAlt = computed(() => {
  if (hasDolbyVision.value && hasDolbyAtmos.value) return "Dolby Vision + Dolby Atmos"
  if (hasDolbyVision.value) return "Dolby Vision"
  return "Dolby Atmos"
})
</script>

<style scoped>
.dolby-badges {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  height: 100%;
  width: max-content;
}

.dolby-logo {
  height: 100%;
  max-height: 44px;
  min-height: 30px;
  width: auto;
  display: block;
  border-radius: 3px;
  filter: invert(1) brightness(1.1) contrast(1.05);
}

.dolby-badges.compact .dolby-logo {
  max-height: 34px;
  min-height: 24px;
}
</style>
