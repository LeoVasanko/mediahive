<template>
  <div
    class="media-row"
    :class="{ 'media-row-wrap': wrap }"
    :data-sync-scroll-row="!wrap && rowIndex !== undefined ? 'true' : undefined"
  >
    <MediaCard
      v-for="(item, index) in items"
      :key="item.id"
      :item="item"
      :nav-row="rowIndex"
      :nav-col="index"
      :href="getItemHref(item)"
      @click="$emit('select', item)"
    />
  </div>
</template>

<script setup lang="ts">
import type { MediaItem, EpisodeWithSeries } from "../types"
import MediaCard from "./MediaCard.vue"

defineProps<{
  items: MediaItem[]
  wrap?: boolean
  rowIndex?: number
}>()

defineEmits<{
  select: [MediaItem]
}>()

function getItemHref(item: MediaItem): string | undefined {
  if (item.type === "episode") {
    const epData = item.data as EpisodeWithSeries
    return `#/series/${epData.series.id}`
  }
  return `#/${item.type}/${item.id}`
}
</script>
