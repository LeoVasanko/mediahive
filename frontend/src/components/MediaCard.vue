<template>
  <component
    :is="href ? 'a' : 'div'"
    class="media-card"
    v-bind="navAttributes"
    :data-item-id="item.id"
    :data-item-type="item.type"
    :href="href || undefined"
    @click="handleClick"
    @keydown.enter.prevent="$emit('click')"
  >
    <div class="media-card-poster">
      <svg class="card-focus-outline" viewBox="0 0 100 150" preserveAspectRatio="none">
        <rect x="0" y="0" width="100" height="150" />
      </svg>
      <img
        v-if="posterImageUrl && !imageError"
        :src="posterImageUrl"
        :alt="item.title || 'Unknown'"
        loading="lazy"
        decoding="async"
        @error="imageError = true"
      />
      <div v-else class="media-card-placeholder">
        {{ item.type === "movies" ? "🎬" : item.type === "episode" ? "📺" : "📺" }}
      </div>
      <div v-if="rating" class="media-card-rating" :class="ratingClass">
        ★ {{ rating.toFixed(1) }}
      </div>
    </div>
    <div class="media-card-info">
      <div class="media-card-title-row">
        <span class="media-card-title">{{ displayTitle }}</span>
        <span v-if="item.year" class="media-card-year">{{ item.year }}</span>
      </div>
      <template v-if="item.searchMatchInfo">
        <div
          v-if="matchedPeople && matchedPeople.length > 0"
          class="media-card-detail match-reason"
        >
          <template v-for="(person, idx) in matchedPeople" :key="person.name">
            <span :class="person.highlightRoles ? 'match-dim' : 'match-name'">{{
              person.name
            }}</span>
            <span :class="person.highlightRoles ? 'match-highlight' : 'match-roles'"
              >({{ person.roles }})</span
            ><span v-if="idx < matchedPeople.length - 1">, </span>
          </template>
        </div>
        <div
          v-if="
            item.searchMatchInfo.matchedEpisodes && item.searchMatchInfo.matchedEpisodes.length > 0
          "
          class="media-card-episodes"
        >
          <div
            v-for="ep in item.searchMatchInfo.matchedEpisodes.slice(0, 3)"
            :key="ep.name"
            class="matched-episode"
          >
            <span class="match-name">{{ ep.name }}</span>
            <span class="match-roles"> ({{ ep.location }})</span>
          </div>
          <div v-if="item.searchMatchInfo.matchedEpisodes.length > 3" class="matched-episode-more">
            +{{ item.searchMatchInfo.matchedEpisodes.length - 3 }} more
          </div>
        </div>
      </template>
      <template v-else>
        <div
          v-if="item.type === 'series' && formattedSeriesCreators"
          class="media-card-detail person-list"
        >
          <span
            v-for="(creatorName, creatorIndex) in formattedSeriesCreators"
            :key="`${creatorName}-${creatorIndex}`"
            class="person-token"
            >{{ creatorName }}</span
          >
        </div>
        <div v-else-if="subtitle" class="media-card-detail">{{ subtitle }}</div>
        <div v-if="directorAndCast" class="media-card-detail person-list">
          <span v-if="director" class="director-name person-token">{{
            formatPersonLabel(director)
          }}</span>
          <span
            v-for="(castName, castIndex) in formattedCastNames"
            :key="`${castName}-${castIndex}`"
            class="person-token"
            >{{ castName }}</span
          >
        </div>
      </template>
    </div>
  </component>
</template>

<script setup lang="ts">
import { computed, ref } from "vue"
import type { MediaItem, Movie, Series, EpisodeWithSeries } from "../types"
import { getCoverUrl, isVideoPath } from "../api"
import { navAttrs } from "../composables/useKeyboardNavigation"

const props = defineProps<{
  item: MediaItem
  navRow?: number
  navCol?: number
  href?: string
}>()

const emit = defineEmits<{
  click: []
}>()

function handleClick(event: MouseEvent) {
  // Let modified clicks (middle-click, ctrl+click, etc.) navigate natively
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
    return
  }
  // Prevent default navigation for plain left-clicks and synthetic clicks
  // so that parent handlers can manage side-effects and routing
  event.preventDefault()
  emit("click")
}

const navAttributes = computed(() => {
  if (props.navRow !== undefined && props.navCol !== undefined) {
    return navAttrs(props.navRow, props.navCol)
  }
  return {}
})

const imageError = ref(false)

const posterImageUrl = computed(() => {
  if (imageError.value) return null
  if (!props.item.cover_path || isVideoPath(props.item.cover_path)) {
    return null
  }
  return getCoverUrl(props.item.cover_path, props.item.root_id)
})

const rating = computed(() => {
  if (props.item.type === "movies") {
    return (props.item.data as Movie).info?.rating
  }
  if (props.item.type === "episode") {
    const epData = props.item.data as EpisodeWithSeries
    return epData.episode.rating ?? epData.series.info?.rating
  }
  return (props.item.data as Series).info?.rating
})

const ratingClass = computed(() => {
  if (!rating.value) return ""
  if (rating.value >= 7.5) return "rating-high"
  if (rating.value >= 6) return "rating-medium"
  return "rating-low"
})

const displayTitle = computed(() => {
  if (props.item.type === "episode") {
    const epData = props.item.data as EpisodeWithSeries
    return epData.episode.name || `Episode ${epData.episode.episode_number}`
  }
  return props.item.title
})

const subtitle = computed(() => {
  if (props.item.type === "episode") {
    const epData = props.item.data as EpisodeWithSeries
    return `${epData.series.title} S${epData.seasonNumber}E${epData.episode.episode_number}`
  }
  return null
})

const seriesCreators = computed(() => {
  if (props.item.type !== "series") return null
  const creators = (props.item.data as Series).info?.creators
  return creators && creators.length > 0 ? creators : null
})

function formatPersonLabel(name: string): string {
  return name.trim().replace(/\s+/g, "\u202F")
}

const formattedSeriesCreators = computed(() => {
  if (!seriesCreators.value) return null
  return seriesCreators.value.map((name) => formatPersonLabel(name))
})

const director = computed(() => {
  if (props.item.type !== "movies") return null
  return (props.item.data as Movie).info?.director
})

const directorAndCast = computed(() => {
  if (props.item.type !== "movies") return false
  return !!director.value || filteredCastNames.value.length > 0
})

const filteredCastNames = computed(() => {
  if (props.item.type !== "movies") return []
  const cast = (props.item.data as Movie).info?.cast
  if (!cast || cast.length === 0) return []

  const directorName = director.value?.toLowerCase()
  const filteredCast = directorName
    ? cast.filter((c) => c.name.toLowerCase() !== directorName)
    : cast

  if (filteredCast.length === 0) return []

  return filteredCast.slice(0, 3).map((c) => c.name)
})

const formattedCastNames = computed(() => {
  return filteredCastNames.value.map((name) => formatPersonLabel(name))
})

const matchedPeople = computed(() => {
  const info = props.item.searchMatchInfo
  if (!info || !info.matchedPeople) return null
  return info.matchedPeople
})
</script>

<style scoped>
@keyframes card-outline-blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.4;
  }
}

.card-focus-outline {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 5;
  opacity: 0;
  transition: opacity 0.2s ease;
}

.card-focus-outline rect {
  fill: none;
  stroke: rgba(255, 255, 255, 0.9);
  stroke-width: 4;
  vector-effect: non-scaling-stroke;
}

.media-card-poster img,
.media-card-poster video {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

html.mouse-active .media-card:hover .card-focus-outline,
html:not(.mouse-active) .media-card.nav-focused .card-focus-outline {
  opacity: 1;
  animation: card-outline-blink 1s ease-in-out infinite;
}

html:not(.mouse-active) .media-card.nav-focused .card-focus-outline rect {
  stroke: #ffffff;
  stroke-width: 5;
  filter: drop-shadow(0 0 6px rgba(255, 255, 255, 0.8));
}

.media-card-rating {
  position: absolute;
  top: 6px;
  right: 6px;
  background: rgba(0, 0, 0, 0.85);
  padding: 3px 6px;
  border-radius: 3px;
  font-size: 0.65rem;
  font-weight: 600;
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

.media-card-detail {
  font-size: 0.65rem;
  color: var(--text-muted);
  margin-top: 1px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.3;
}

.media-card-detail.person-list {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  row-gap: 1px;
  column-gap: 0.5ch;
  -webkit-line-clamp: unset;
  -webkit-box-orient: unset;
  max-height: calc(1.3em * 2);
}

.person-token {
  white-space: nowrap;
}

.director-name {
  font-weight: 600;
  color: var(--text-secondary);
}

.match-reason {
  color: var(--text-secondary);
}

.match-name {
  font-weight: 600;
  color: var(--text-secondary);
}

.match-roles {
  color: var(--text-muted);
  font-weight: 400;
}

.match-dim {
  color: var(--text-muted);
  font-weight: 400;
}

.match-highlight {
  font-weight: 600;
  color: var(--text-secondary);
}

.media-card-episodes {
  margin-top: 2px;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.matched-episode {
  font-size: 0.6rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.matched-episode-more {
  font-size: 0.55rem;
  color: var(--text-muted);
  font-style: italic;
}
</style>
