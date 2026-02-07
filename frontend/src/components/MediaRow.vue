<template>
  <div class="media-row" :class="{ 'media-row-wrap': wrap }">
    <MediaCard
      v-for="(item, index) in items"
      :key="item.id"
      :item="item"
      :nav-row="rowIndex"
      :nav-col="index"
      @click="$emit('select', item)"
    />
  </div>
</template>

<script setup lang="ts">
import type { MediaItem } from '../types';
import MediaCard from './MediaCard.vue';

defineProps<{
  items: MediaItem[];
  wrap?: boolean;
  rowIndex?: number;
}>();

defineEmits<{
  select: [MediaItem];
}>();
</script>
