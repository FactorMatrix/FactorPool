<script setup>
const props = defineProps({
  name: String,
  size: { type: Number, default: 18 },
});
const paths = {
  terminal: "M3 4h18v16H3z M7 8l4 4-4 4 M13 16h4",
  alpha: "M17 6c-7-5-13 2-10 9 2 5 8 3 10-9l-2 12 M15 11l5 7",
  book: "M3 5c4-2 6-2 9 0 3-2 5-2 9 0v15c-4-2-6-2-9 0-3-2-5-2-9 0z M12 5v15",
  data: "M3 6c0-5 18-5 18 0s-18 5-18 0v12c0 5 18 5 18 0V6 M3 12c0 5 18 5 18 0",
  history: "M4 5v5h5 M4 10a8 8 0 1 1 1 8 M12 8v5l3 2",
  settings:
    "M10 3h4l1 3 3 1 3 3v4l-3 1-1 3-3 3h-4l-1-3-3-1-3-3v-4l3-1 1-3z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
  chart: "M3 19l5-10 5 5 6-10 M3 21h18",
  bars: "M4 20V12h3v8 M11 20V7h3v13 M18 20V3h3v17",
  scatter: "M4 3v18h17 M8 16h.01 M13 10h.01 M17 12h.01 M18 6h.01 M9 8h.01",
  table: "M3 4h18v16H3z M3 10h18 M3 15h18 M10 4v16",
  code: "M8 6l-6 6 6 6 M16 6l6 6-6 6 M14 3l-4 18",
  logs: "M5 3h14v18H5z M8 7h8 M8 11h8 M8 15h5",
  info: "M12 10v7 M12 7h.01 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  play: "M7 4l13 8-13 8z",
  stop: "M5 5h14v14H5z",
  plus: "M12 5v14 M5 12h14",
  close: "M6 6l12 12 M6 18L18 6",
  check: "M4 12l5 5L20 6",
  save: "M4 3h13l4 4v14H3V3h1 M7 3v7h10V3 M7 21v-7h10v7",
  clone: "M8 8h13v13H8z M4 16H2V2h14v2",
  download: "M12 3v12 M7 10l5 5 5-5 M3 17v4h18v-4",
  arrow: "M9 5l7 7-7 7",
  refresh: "M20 4v6h-6 M20 10a8 8 0 1 0-1 8",
};
</script>
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] || paths.info" />
  </svg>
</template>
