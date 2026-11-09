<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

const props = defineProps({
  direction: { type: String, default: "horizontal" },
  initial: { type: Number, default: 42 },
  storageKey: { type: String, required: true },
  label: { type: String, required: true },
  breakpoint: { type: Number, default: 0 },
  firstExtra: { type: Number, default: 0 },
});
const host = ref(null);
const ratio = ref(props.initial);
const narrow = ref(false);
const dragging = ref(false);
const vertical = computed(() => props.direction === "vertical" || narrow.value);
const extra = computed(() =>
  Number.isFinite(props.firstExtra) ? Math.max(0, props.firstExtra) : 0,
);
defineExpose({ ratio, vertical });
let media;
let handle;

function setRatio(value, persist = true) {
  if (!Number.isFinite(value)) return;
  ratio.value = Math.max(10, Math.min(90, value));
  if (persist) {
    try {
      localStorage.setItem(
        `factorpool.layout.${props.storageKey}`,
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
  const available = size - extra.value;
  // firstExtra 全部分给第一栏，其余尺寸继续使用原有持久化比例。
  if (available > 0) {
    const position = vertical.value
      ? event.clientY - box.top
      : event.clientX - box.left;
    setRatio(((position - extra.value) / available) * 100, false);
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
      ? 10
      : event.key === "End"
        ? 90
        : ratio.value +
          (event.key === previous ? -1 : 1) * (event.shiftKey ? 10 : 2),
  );
}
function resizeMode() {
  narrow.value = Boolean(media?.matches);
}
onMounted(() => {
  try {
    // 仅兼容读取旧布局偏好；之后写入新前缀，不迁移或读取账号私有数据。
    const saved = localStorage.getItem(`factorpool.layout.${props.storageKey}`);
    if (saved !== null) setRatio(Number(saved), false);
  } catch {
    /* 隐私模式不影响拖动。 */
  }
  if (props.breakpoint) {
    media = window.matchMedia(`(max-width: ${props.breakpoint}px)`);
    media.addEventListener("change", resizeMode);
    resizeMode();
  }
});
onBeforeUnmount(() => {
  stop();
  media?.removeEventListener("change", resizeMode);
});
</script>

<template>
  <div
    ref="host"
    class="split-pane"
    :class="{ vertical, dragging }"
    :style="{
      '--split-ratio': ratio + '%',
      '--split-first-bonus': extra * (1 - ratio / 100) + 'px',
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
      aria-valuemin="10"
      aria-valuemax="90"
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
      calc(var(--split-ratio) + var(--split-first-bonus) - 4px),
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
