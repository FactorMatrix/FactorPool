<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

const props = defineProps({
  direction: { type: String, default: "horizontal" },
  initial: { type: Number, default: 42 },
  storageKey: { type: String, required: true },
  label: { type: String, required: true },
  breakpoint: { type: Number, default: 0 },
  minFirst: { type: Number, default: 0 },
  minSecond: { type: Number, default: 0 },
});
const host = ref(null);
const ratio = ref(props.initial);
const narrow = ref(false);
const dragging = ref(false);
const vertical = computed(() => props.direction === "vertical" || narrow.value);
const size = ref(0);
const bounds = computed(() => {
  if (!size.value || size.value < props.minFirst + props.minSecond + 8)
    return [10, 90];
  return [Math.max(10, (props.minFirst + 4) / size.value * 100),
    Math.min(90, (size.value - props.minSecond - 4) / size.value * 100)];
});
defineExpose({ ratio, vertical });
let media;
let handle;
let observer;
const storageKey = () => `factorpool.layout.${props.storageKey}${vertical.value ? '.vertical' : ''}`;

function setRatio(value, persist = true) {
  if (!Number.isFinite(value)) return;
  ratio.value = Math.max(bounds.value[0], Math.min(bounds.value[1], value));
  if (persist) {
    try {
      localStorage.setItem(
        storageKey(),
        String(ratio.value),
      );
    } catch {
      /* 布局存储可选。 */
    }
  }
}
function move(event) {
  if (!dragging.value || !host.value) return;
  const box = host.value.getBoundingClientRect();
  const size = vertical.value ? box.height : box.width;
  if (size > 0) {
    const position = vertical.value
      ? event.clientY - box.top
      : event.clientX - box.left;
    setRatio((position / size) * 100, false);
  }
}
function stop(event) {
  if (!dragging.value) return;
  dragging.value = false;
  if (event && handle?.hasPointerCapture(event.pointerId))
    handle.releasePointerCapture(event.pointerId);
  setRatio(ratio.value);
  handle = null;
}
function start(event) {
  if (event.button !== 0) return;
  event.preventDefault();
  dragging.value = true;
  handle = event.currentTarget;
  handle.setPointerCapture(event.pointerId);
}
function keyboard(event) {
  const previous = vertical.value ? "ArrowUp" : "ArrowLeft";
  const next = vertical.value ? "ArrowDown" : "ArrowRight";
  if (![previous, next, "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  setRatio(
    event.key === "Home"
      ? bounds.value[0]
      : event.key === "End"
        ? bounds.value[1]
        : ratio.value +
          (event.key === previous ? -1 : 1) * (event.shiftKey ? 10 : 2),
  );
}
function resizeMode() {
  const wasVertical = vertical.value;
  narrow.value = Boolean(media?.matches);
  measure();
  if (wasVertical !== vertical.value) restoreRatio();
}
function measure() {
  size.value = vertical.value ? host.value?.clientHeight : host.value?.clientWidth;
  setRatio(ratio.value, false);
}
function restoreRatio() {
  let saved;
  try {
    saved = localStorage.getItem(storageKey());
  } catch {
    /* 布局存储可选。 */
  }
  setRatio(saved == null ? (vertical.value ? 35 : props.initial) : Number(saved), false);
}
onMounted(() => {
  if (props.breakpoint) {
    media = window.matchMedia(`(max-width: ${props.breakpoint}px)`);
    media.addEventListener("change", resizeMode);
    resizeMode();
  }
  measure();
  restoreRatio();
  observer = new ResizeObserver(measure);
  observer.observe(host.value);
});
onBeforeUnmount(() => {
  stop();
  media?.removeEventListener("change", resizeMode);
  observer?.disconnect();
});
</script>

<template>
  <div
    ref="host"
    class="split-pane"
    :class="{ vertical, dragging }"
    :style="{
      '--split-ratio': ratio + '%',
    }"
  >
    <div class="split-first"><slot name="first" /></div>
    <div
      class="split-handle"
      role="separator"
      tabindex="0"
      :aria-label="label"
      :aria-orientation="vertical ? 'horizontal' : 'vertical'"
      :aria-valuenow="Math.round(ratio)"
      :aria-valuemin="Math.round(bounds[0])"
      :aria-valuemax="Math.round(bounds[1])"
      :title="label + '：拖动调整，双击复原；方向键微调'"
      @pointerdown="start"
      @pointermove="move"
      @pointerup="stop"
      @pointercancel="stop"
      @lostpointercapture="stop"
      @keydown="keyboard"
      @dblclick="setRatio(initial)"
    >
      <span />
    </div>
    <div class="split-second"><slot name="second" /></div>
  </div>
</template>

<style scoped>
.split-pane {
  display: flex;
  flex: 1;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.split-pane.vertical {
  flex-direction: column;
}
.split-first {
  flex: 0 0
    clamp(
      0px,
      calc(var(--split-ratio) - 4px),
      calc(100% - 8px)
    );
}
.split-second {
  flex: 1;
}
.split-first,
.split-second {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.split-handle {
  flex: 0 0 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: col-resize;
  touch-action: none;
  background: var(--bg);
}
.vertical > .split-handle {
  cursor: row-resize;
}
.split-handle span {
  width: 2px;
  height: 30px;
  border-radius: 2px;
  background: var(--border);
}
.vertical > .split-handle span {
  width: 30px;
  height: 2px;
}
.split-handle:hover,
.split-handle:focus-visible,
.dragging > .split-handle {
  background: var(--accent);
  outline: none;
}
.dragging {
  user-select: none;
}
</style>
