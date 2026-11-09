<script setup>
import { onMounted, onBeforeUnmount, watch, ref } from "vue";
import { EditorView, basicSetup } from "codemirror";
import { python } from "@codemirror/lang-python";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags } from "@lezer/highlight";
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
      syntaxHighlighting(
        HighlightStyle.define([
          { tag: tags.keyword, color: "#c792ea" },
          {
            tag: [tags.className, tags.function(tags.variableName)],
            color: "#ffcb6b",
          },
          { tag: tags.number, color: "#f78c6c" },
          { tag: tags.string, color: "#c3e88d" },
          { tag: tags.comment, color: "#78879b" },
          { tag: tags.variableName, color: "#cad8e7" },
          { tag: tags.operator, color: "#89d8e9" },
        ]),
      ),
      EditorView.theme(
        {
          "&": { height: "100%", backgroundColor: "#000000", color: "#cfd9e2" },
          ".cm-scroller": {
            overflow: "auto",
            fontFamily: "monospace",
            lineHeight: "1.6",
            fontSize: "13px",
          },
          ".cm-content": { caretColor: "#6ecb4b", padding: "16px 0" },
          ".cm-gutters": {
            backgroundColor: "#000000",
            color: "#606c80",
            border: "none",
          },
          ".cm-activeLine, .cm-activeLineGutter": {
            backgroundColor: "#11151d",
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
  min-height: 0;
  overflow: hidden;
  height: 100%;
}
</style>
