<script setup>
import { onMounted, onBeforeUnmount, watch, ref } from "vue";
import { EditorView, basicSetup } from "codemirror";
import { python } from "@codemirror/lang-python";
const props = defineProps({ modelValue: String });
const emit = defineEmits(["update:modelValue"]);
const host = ref(null);
let view;
onMounted(() => {
  view = new EditorView({
    parent: host.value,
    doc: props.modelValue,
    extensions: [
      basicSetup,
      python(),
      EditorView.theme(
        {
          "&": { height: "100%", backgroundColor: "#111923", color: "#d6e1ed" },
          ".cm-scroller": {
            overflow: "auto",
            fontFamily: "monospace",
            lineHeight: "1.7",
            fontSize: "13px",
          },
          ".cm-content": { caretColor: "#4adcb1", padding: "16px 0" },
          ".cm-gutters": {
            backgroundColor: "#111923",
            color: "#66798d",
            border: "none",
          },
          ".cm-activeLine, .cm-activeLineGutter": {
            backgroundColor: "#1a2935",
          },
        },
        { dark: true },
      ),
      EditorView.updateListener.of((update) => {
        if (update.docChanged)
          emit("update:modelValue", update.state.doc.toString());
      }),
    ],
  });
});
watch(
  () => props.modelValue,
  (value) => {
    if (view && view.state.doc.toString() !== value)
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: value },
      });
  },
);
onBeforeUnmount(() => view?.destroy());
</script>
<template><div ref="host" class="editor-host"></div></template>
<style scoped>
.editor-host {
  flex: 1;
  min-height: 320px;
  overflow: hidden;
  height: 100%;
}
</style>
