<template>
  <section class="hero">
    <div
      v-if="coverUrl"
      class="hero-background"
      :style="{ backgroundImage: `url('${coverUrl}')` }"
    ></div>
    <div class="hero-content">
      <h1 class="hero-title">{{ item.title }}</h1>
      <div class="hero-meta">
        <span v-if="item.year" class="hero-year">{{ item.year }}</span>
        <span v-if="rating" class="hero-rating" :class="ratingClass">
          ★ {{ rating.toFixed(1) }}
        </span>
        <span v-if="resolution" class="hero-quality">{{ resolution }}</span>
        <span v-if="quality" class="hero-quality">{{ quality }}</span>
      </div>
      <p v-if="overview" class="hero-overview">{{ overview }}</p>
      <div class="hero-buttons">
        <button class="btn btn-primary" @click="handlePlay" :disabled="!playableFile">
          ▶ Play
        </button>
        <button class="btn btn-secondary" @click="$emit('info', item)">ℹ More Info</button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue"
import type { MediaItem, Movie, Series } from "../types"
import { getCoverUrl } from "../api"

const props = defineProps<{
  item: MediaItem
}>()

const emit = defineEmits<{
  play: [string]
  info: [MediaItem]
}>()

const coverUrl = computed(() => {
  return getCoverUrl(props.item.cover_path, props.item.root_id)
})

const resolution = computed(() => {
  if (props.item.type === "movies") {
    const movie = props.item.data as Movie
    const torrents = Object.values(movie.torrents || {})
    return torrents.length > 0 ? torrents[0].resolution : null
  }
  return null
})

const quality = computed(() => {
  if (props.item.type === "movies") {
    const movie = props.item.data as Movie
    const torrents = Object.values(movie.torrents || {})
    return torrents.length > 0 ? torrents[0].quality : null
  }
  return null
})

const rating = computed(() => {
  if (props.item.type === "movies") {
    return (props.item.data as Movie).info?.rating
  }
  return (props.item.data as Series).info?.rating
})

const ratingClass = computed(() => {
  if (!rating.value) return ""
  if (rating.value >= 7.5) return "rating-high"
  if (rating.value >= 6) return "rating-medium"
  return "rating-low"
})

const overview = computed(() => {
  if (props.item.type === "movies") {
    const o = (props.item.data as Movie).info?.overview
    return o ? (o.length > 200 ? o.slice(0, 200) + "..." : o) : null
  }
  const o = (props.item.data as Series).info?.overview
  return o ? (o.length > 200 ? o.slice(0, 200) + "..." : o) : null
})

const playableFile = computed(() => {
  if (props.item.type === "movies") {
    const movie = props.item.data as Movie
    const torrents = Object.values(movie.torrents || {})
    return torrents.length > 0 ? torrents[0].playable_file : null
  }
  // For series, get first available file from episodes
  const series = props.item.data as Series
  for (const season of series.seasons || []) {
    for (const episode of season.episodes || []) {
      const torrents = Object.values(episode.torrents || {})
      for (const torrent of torrents) {
        if (torrent.playable_file) {
          return torrent.playable_file
        }
      }
    }
  }
  return null
})

function handlePlay() {
  if (playableFile.value) {
    emit("play", playableFile.value)
  }
}
</script>

<style scoped>
.hero-rating {
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.6);
}

.rating-high {
  color: #46d369;
}

.rating-medium {
  color: #f9a825;
}

.rating-low {
  color: #e53935;
}

.hero-overview {
  max-width: 500px;
  color: var(--text-secondary);
  font-size: 0.95rem;
  line-height: 1.5;
  margin-top: 12px;
}
</style>
