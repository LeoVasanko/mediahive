<template>
  <div
    class="version-row"
    :class="{
      'version-best': best,
      'version-selectable': isSelectable,
      'version-disabled': isDisabled,
      'version-menu': variant === 'menu',
      'version-with-actions': showActions,
    }"
    tabindex="0"
    :title="resolvedTitle"
    v-bind="$attrs"
    @click="handleActivate"
    @keydown.enter.prevent="handleActivate"
    @keydown.space.prevent="handleActivate"
  >
    <div class="version-main">
      <div class="version-badges">
        <span v-if="torrent.resolution" class="v-badge res">{{ torrent.resolution }}</span>
        <span v-if="showHdrBadge" class="v-badge hdr">HDR</span>
        <span v-if="displayCodecBadge" class="v-badge codec">{{ displayCodecBadge }}</span>
        <span v-if="displayQualityBadge" class="v-badge qual">{{ displayQualityBadge }}</span>
        <span v-if="displayAudioBadge" class="v-badge audio">{{ displayAudioBadge }}</span>
        <span v-if="displayReleaseGroupBadge" class="v-badge group">{{ displayReleaseGroupBadge }}</span>
      </div>
      <div class="version-language-flags">
        <LanguageFlags class="language-flags-audio" :codes="torrent.audio_languages" :compact="compactFlags" />
        <span
          v-if="hasLanguageDisplay(torrent.audio_languages) && hasLanguageDisplay(torrent.subtitle_languages)"
          class="language-separator"
        >•</span>
        <LanguageFlags class="language-flags-subs" :codes="torrent.subtitle_languages" :compact="compactFlags" />
      </div>
    </div>
    <div class="version-dolby-cell">
      <img
        v-if="showBlurayLogo"
        class="version-disc-logo"
        :src="blurayLogoUrl"
        alt="Blu-ray"
      >
      <img
        v-else-if="showDvdLogo"
        class="version-disc-logo"
        :src="dvdLogoUrl"
        alt="DVD"
      >
      <img
        v-if="streamingServiceLogo"
        class="version-service-logo"
        :src="streamingServiceLogo.src"
        :alt="streamingServiceLogo.alt"
        :title="streamingServiceLogo.alt"
      >
        <DolbyBadges
          class="version-dolby"
          :has-dolby-vision="hasDolbyVision"
          :has-dolby-atmos="hasDolbyAtmos"
          :is-hdr="hasHdr"
        />
    </div>
    <div v-if="showActions" class="version-actions">
      <button
        class="ctx-btn ctx-btn-play"
        tabindex="0"
        @click.stop="emit('play')"
        :disabled="!torrent.playable_file"
      >▶ {{ playLabel }}</button>
      <button
        class="ctx-btn ctx-btn-folder"
        tabindex="0"
        @click.stop="emit('openFolder')"
        :disabled="!torrent.playable_file"
      >📁</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { Torrent } from '../types';
import LanguageFlags from './LanguageFlags.vue';
import DolbyBadges from './DolbyBadges.vue';
import { buildLanguageFlags } from '../utils/languageFlags';
import blurayLogoUrl from '../assets/bluray.webp';
import dvdLogoUrl from '../assets/dvd.webp';
import amazonLogoUrl from '../assets/service-amazon.webp';
import appleTvLogoUrl from '../assets/service-apple-tv.webp';
import netflixLogoUrl from '../assets/service-netflix.webp';
import hboMaxLogoUrl from '../assets/service-hbo-max.webp';
import huluLogoUrl from '../assets/service-hulu.webp';
import disneyLogoUrl from '../assets/service-disney.svg';
import itunesLogoUrl from '../assets/service-itunes.png';

defineOptions({
  inheritAttrs: false,
});

const props = withDefaults(defineProps<{
  torrent: Torrent;
  best?: boolean;
  selectable?: boolean;
  disabled?: boolean;
  compactFlags?: boolean;
  showActions?: boolean;
  playLabel?: string;
  title?: string;
  variant?: 'default' | 'menu';
}>(), {
  best: false,
  selectable: undefined,
  disabled: undefined,
  compactFlags: false,
  showActions: false,
  playLabel: 'Play',
  title: undefined,
  variant: 'default',
});

const emit = defineEmits<{
  activate: [MouseEvent | KeyboardEvent];
  play: [];
  openFolder: [];
}>();

const dolbyTagPattern = /\b(dolby|atmos|vision|dovi|dv)\b/i;
const dolbyVisionPattern = /\b(dolby\s*vision|dovi|\bdv\b)\b/i;
const dolbyAtmosPattern = /\b(dolby\s*atmos|atmos)\b/i;
const hdrPattern = /\bhdr\b|smpte\s*2084|bt\s*2020|hlg/i;
const blurayTagPattern = /\bblu[\s.-]*ray\b/i;
const blurayPlayablePattern = /(?:^|[\\/])(movieobject|index)\.bdmv$/i;
const dvdPlayablePattern = /(?:^|[\\/])video_ts\.ifo$/i;
const webQualityPattern = /^web(?:[ .-]?dl|[ .-]?rip)$/i;

const serviceLogoMap: Array<{ aliases: string[]; src: string; alt: string }> = [
  { aliases: ['amazon studios', 'amazon prime video', 'prime video', 'amazon', 'amzn'], src: amazonLogoUrl, alt: 'Amazon Prime Video' },
  { aliases: ['apple tv+', 'apple tv plus', 'apple tv', 'atvp'], src: appleTvLogoUrl, alt: 'Apple TV+' },
  { aliases: ['itunes', 'it'], src: itunesLogoUrl, alt: 'iTunes' },
  { aliases: ['netflix', 'nf', 'nflx'], src: netflixLogoUrl, alt: 'Netflix' },
  { aliases: ['hbo max', 'max', 'hmax'], src: hboMaxLogoUrl, alt: 'HBO Max' },
  { aliases: ['disney plus', 'disney+', 'disney plus hotstar', 'dsnp'], src: disneyLogoUrl, alt: 'Disney+' },
  { aliases: ['hulu'], src: huluLogoUrl, alt: 'Hulu' },
];

function hasDolbyTag(value: string | null | undefined): boolean {
  return Boolean(value && dolbyTagPattern.test(value));
}

function hasAnyTag(
  pattern: RegExp,
  ...values: Array<string | null | undefined>
): boolean {
  return values.some((value) => Boolean(value && pattern.test(value)));
}

function hasLanguageDisplay(codes: string[] | null | undefined): boolean {
  const mapped = buildLanguageFlags(codes);
  return mapped.flags.length > 0 || mapped.unmappedCodes.length > 0;
}

function hasHdrTag(value: string | null | undefined): boolean {
  return Boolean(value && hdrPattern.test(value));
}

function normalizeProviderName(value: string | null | undefined): string {
  return (value || '').toLowerCase().replace(/[^a-z0-9+]+/g, ' ').trim();
}

function normalizeQualityBadge(value: string): string {
  const normalized = value.toLowerCase().replace(/[^a-z0-9]+/g, '');

  if (normalized === 'hdtv' || normalized === 'pdtv' || normalized === 'tvrip' || normalized === 'sdtv') {
    return 'TV';
  }

  if (
    normalized.includes('cam')
    || normalized === 'telesync'
    || normalized === 'ts'
    || normalized === 'hdts'
    || normalized === 'telecine'
    || normalized === 'tc'
  ) {
    return 'CAM';
  }

  return value;
}

const hasDolbyVision = computed(() => {
  return (
    props.torrent.has_dolby_vision === true
    || hasAnyTag(dolbyVisionPattern, props.torrent.quality, props.torrent.codec, props.torrent.audio, props.torrent.title)
  );
});

const hasDolbyAtmos = computed(() => {
  return (
    props.torrent.has_dolby_atmos === true
    || hasAnyTag(dolbyAtmosPattern, props.torrent.quality, props.torrent.codec, props.torrent.audio, props.torrent.title)
  );
});

const hasHdr = computed(() => {
  return (
    props.torrent.is_hdr === true
    || hasAnyTag(hdrPattern, props.torrent.quality, props.torrent.codec, props.torrent.audio, props.torrent.title)
  );
});

const isBlurayDisc = computed(() => {
  if (!props.torrent.playable_file) return false;
  return blurayPlayablePattern.test(props.torrent.playable_file);
});

const isDvdDisc = computed(() => {
  if (!props.torrent.playable_file) return false;
  return dvdPlayablePattern.test(props.torrent.playable_file);
});

const showBlurayLogo = computed(() => {
  return isBlurayDisc.value;
});

const showDvdLogo = computed(() => {
  return isDvdDisc.value;
});

const streamingServiceLogo = computed(() => {
  if (!props.torrent.quality || !webQualityPattern.test(props.torrent.quality)) {
    return null;
  }

  const network = normalizeProviderName(props.torrent.network);
  if (!network) return null;

  const found = serviceLogoMap.find((entry) => entry.aliases.includes(network));
  return found ? { src: found.src, alt: found.alt } : null;
});

const displayQualityBadge = computed(() => {
  if (!props.torrent.quality || hasDolbyTag(props.torrent.quality)) return null;
  if (streamingServiceLogo.value) return null;
  if (blurayTagPattern.test(props.torrent.quality)) return null;
  return normalizeQualityBadge(props.torrent.quality);
});

const displayCodecBadge = computed(() => {
  if (!props.torrent.codec || hasDolbyTag(props.torrent.codec)) return null;
  return props.torrent.codec;
});

const displayAudioBadge = computed(() => {
  if (!props.torrent.audio || hasDolbyTag(props.torrent.audio)) return null;
  return props.torrent.audio;
});

const displayReleaseGroupBadge = computed(() => {
  const value = props.torrent.encoder?.trim();
  if (!value) return null;
  return value;
});

const showHdrBadge = computed(() => {
  if (!hasHdr.value) return false;
  return !hasHdrTag(props.torrent.quality) && !hasHdrTag(props.torrent.codec) && !hasHdrTag(props.torrent.audio);
});

const isSelectable = computed(() => {
  if (props.selectable !== undefined) return props.selectable;
  return Boolean(props.torrent.playable_file);
});

const isDisabled = computed(() => {
  if (props.disabled !== undefined) return props.disabled;
  return !isSelectable.value;
});

const resolvedTitle = computed(() => {
  if (props.title !== undefined) return props.title;
  return isSelectable.value ? 'Click to play/continue. Alt+Click to open folder.' : 'No playable file';
});

function handleActivate(event: MouseEvent | KeyboardEvent) {
  if (!isSelectable.value || isDisabled.value) return;
  emit('activate', event);
}
</script>

<style scoped>
.version-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) max-content;
  align-items: stretch;
  column-gap: 8px;
  row-gap: 6px;
  padding: 10px 12px;
  background: rgba(10, 14, 22, 0.2);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  transition: background 0.2s, border-color 0.2s;
}

.version-row.version-menu {
  padding: 10px 16px;
}

.version-row.version-with-actions {
  grid-template-columns: minmax(0, 1fr) max-content max-content;
}

:global(html[data-mouse-active="true"]) .version-row:hover {
  background: rgba(10, 14, 22, 0.28);
  border-color: rgba(255, 255, 255, 0.2);
}

.version-row.version-best {
  border-color: rgba(255, 255, 255, 0.1);
  background: rgba(10, 14, 22, 0.2);
}

:global(html[data-mouse-active="true"]) .version-row.version-best:hover {
  background: rgba(10, 14, 22, 0.28);
  border-color: rgba(255, 255, 255, 0.2);
}

.version-row.version-selectable {
  cursor: pointer;
}

.version-row.version-selectable:focus-visible {
  outline: 2px solid rgba(255, 255, 255, 0.85);
  outline-offset: 2px;
}

.version-row.version-disabled {
  cursor: not-allowed;
  opacity: 0.75;
}

.version-main {
  grid-column: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 6px;
  min-width: 0;
}

.version-dolby-cell {
  grid-column: 2;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  height: 36px;
  min-width: 0;
}

.version-dolby {
  align-self: stretch;
  height: 100%;
}

.version-disc-logo {
  align-self: stretch;
  display: block;
  width: auto;
  height: 100%;
  max-height: 100%;
  object-fit: contain;
  filter: drop-shadow(0 0 0.4px rgba(0, 0, 0, 0.5));
}

.version-badges {
  --badge-row-height: 27px;
  /* Regular hex geometry: horizontal inset = h / (2 * sqrt(3)) ~= 0.288675 * h */
  --badge-hex-inset: calc(var(--badge-row-height) * 0.288675);
  display: flex;
  flex-wrap: nowrap;
  align-items: stretch;
  gap: 0;
  height: var(--badge-row-height);
}

.v-badge {
  display: flex;
  align-items: center;
  height: 100%;
  font-size: 0.78rem;
  line-height: 1;
  padding: 0 8px 0 calc(4px + var(--badge-hex-inset));
  font-weight: 600;
  text-transform: uppercase;
  margin-left: calc(-1 * var(--badge-hex-inset));
  clip-path: polygon(var(--badge-hex-inset) 0, 100% 0, 100% 100%, 0 100%);
}

.v-badge:first-child {
  margin-left: 0;
  padding-left: 9px;
  clip-path: polygon(
    var(--badge-hex-inset) 0,
    100% 0,
    100% 100%,
    var(--badge-hex-inset) 100%,
    0 50%
  );
}

.v-badge:last-child {
  padding-right: calc(6px + var(--badge-hex-inset));
  clip-path: polygon(
    var(--badge-hex-inset) 0,
    calc(100% - var(--badge-hex-inset)) 0,
    100% 50%,
    calc(100% - var(--badge-hex-inset)) 100%,
    0 100%
  );
}

.v-badge:only-child {
  margin-left: 0;
  padding-left: 6px;
  padding-right: calc(6px + var(--badge-hex-inset));
  clip-path: polygon(
    var(--badge-hex-inset) 0,
    calc(100% - var(--badge-hex-inset)) 0,
    100% 50%,
    calc(100% - var(--badge-hex-inset)) 100%,
    var(--badge-hex-inset) 100%,
    0 50%
  );
}

.version-service-logo {
  align-self: stretch;
  display: block;
  width: auto;
  height: 100%;
  max-height: 100%;
  object-fit: contain;
}

.v-badge.res {
  background: #111111;
  color: #f8fafc;
}

.v-badge.qual {
  background: #334155;
  color: #f1f5f9;
}

.v-badge.codec {
  background: #1f2937;
  color: #f3f4f6;
}

.v-badge.audio {
  background: #475569;
  color: #f1f5f9;
}

.v-badge.hdr {
  background: #d4a017;
  color: #1c1917;
}

.v-badge.group {
  background: #64748b;
  color: #f8fafc;
}

.version-language-flags {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  width: 100%;
  white-space: nowrap;
  overflow: hidden;
  min-width: 0;
}

.version-language-flags > .language-flags-audio {
  flex: 0 0 auto;
}

.version-language-flags > .language-flags-subs {
  flex: 1 1 auto;
  min-width: 0;
  -webkit-mask-image: linear-gradient(to right, black calc(100% - 14px), transparent);
  mask-image: linear-gradient(to right, black calc(100% - 14px), transparent);
}

.version-language-flags > .language-flags-subs :deep(.language-flags) {
  display: inline-flex;
  max-width: 100%;
  overflow: hidden;
}

.version-language-flags > .language-flags-subs :deep(.language-flag-list) {
  width: max-content;
  max-width: none;
  overflow: hidden;
}

.language-separator {
  color: rgba(255, 255, 255, 0.8);
  font-size: 0.9rem;
  font-weight: 700;
  line-height: 1;
  margin: 0;
}

.version-actions {
  grid-column: 3;
  display: flex;
  align-items: center;
  gap: 6px;
}

.ctx-btn {
  border: 1px solid rgba(255, 255, 255, 0.18);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 0.78rem;
  cursor: pointer;
}

:global(html[data-mouse-active="true"]) .ctx-btn:hover:not(:disabled),
:global(html[data-mouse-active="false"]) .ctx-btn.nav-focused:not(:disabled),
.ctx-btn:focus-visible:not(:disabled) {
  background: rgba(255, 255, 255, 0.16);
  border-color: rgba(255, 255, 255, 0.35);
  outline: none;
}

.ctx-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.ctx-btn-folder {
  width: 34px;
  text-align: center;
  padding: 6px 0;
}
</style>
