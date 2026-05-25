<template>
  <div v-if="hasContent" class="language-flags" :class="{ compact }">
    <span v-if="label" class="language-flags-label">{{ label }}</span>
    <span class="language-flag-list">
      <span
        v-for="entry in flagEntries"
        :key="entry.countryCode"
        class="language-flag"
        :title="`${entry.countryCode}: ${entry.sourceCodes.join(', ')}`"
        v-html="entry.svg"
      ></span>
      <span
        v-for="code in unmappedCodes"
        :key="`raw-${code}`"
        class="language-code-fallback"
        :title="code"
        >{{ code }}</span
      >
    </span>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue"
import { buildLanguageFlags } from "../utils/languageFlags"

const props = defineProps<{
  label?: string
  codes: string[] | null | undefined
  compact?: boolean
}>()

const mapped = computed(() => buildLanguageFlags(props.codes))
const flagEntries = computed(() => mapped.value.flags)
const unmappedCodes = computed(() => mapped.value.unmappedCodes)
const hasContent = computed(() => flagEntries.value.length > 0 || unmappedCodes.value.length > 0)
const compact = computed(() => props.compact === true)
</script>

<style scoped>
.language-flags {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  min-width: 0;
  white-space: nowrap;
  vertical-align: middle;
}

.language-flags-label {
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: rgba(255, 255, 255, 0.7);
  flex: 0 0 auto;
}

.language-flag-list {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  min-width: 0;
  flex-wrap: nowrap;
  white-space: nowrap;
}

.language-flag {
  display: inline-flex;
  flex: 0 0 auto;
  width: 18px;
  height: 12px;
  border-radius: 2px;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.28);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.35) inset;
}

.language-flag :deep(svg) {
  width: 100%;
  height: 100%;
  display: block;
}

.language-code-fallback {
  flex: 0 0 auto;
  font-size: 0.58rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: rgba(255, 255, 255, 0.9);
  background: rgba(255, 255, 255, 0.14);
  border: 1px solid rgba(255, 255, 255, 0.25);
  border-radius: 3px;
  padding: 0.08rem 0.25rem;
  text-transform: uppercase;
}

.language-flags.compact .language-flags-label {
  font-size: 0.58rem;
}

.language-flags.compact .language-flag {
  width: 16px;
  height: 11px;
}

.language-flags.compact .language-code-fallback {
  font-size: 0.52rem;
}
</style>
