<script setup>
import { onMounted, onBeforeUnmount, watch, ref } from "vue";
import { EditorView, basicSetup } from "codemirror";
import { python, globalCompletion, localCompletionSource } from "@codemirror/lang-python";
import { autocompletion } from "@codemirror/autocomplete";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags } from "@lezer/highlight";
const props = defineProps({ modelValue: String, operators: { type: Array, default: () => [] } });
const emit = defineEmits(["update:modelValue"]);
const host = ref(null);
let view;
function operatorCompletion(context) {
  const token = context.matchBefore(/\bself(?:\.[A-Za-z_]\w*){0,2}\.?/);
  if (!token || (!context.explicit && !token.text.includes("."))) return null;
  const methods = new Map();
  for (const operator of props.operators) {
    for (const syntax of [operator.frontend, operator.reference?.advanced_syntax]) {
      for (const match of (syntax || "").matchAll(/self\.((?:(?:ts|cs|vc|gp|tf)\.)?[A-Za-z_]\w*)\(/g)) {
        const label = `self.${match[1]}`;
        methods.set(label, { label, type: "function", detail: operator.summary, apply: label + "(" });
        const domain = match[1].split(".");
        if (domain.length === 2) methods.set(`self.${domain[0]}`, { label: `self.${domain[0]}`, type: "namespace", apply: `self.${domain[0]}.` });
      }
    }
  }
  methods.set("self.ts.is_ready", { label: "self.ts.is_ready", type: "function", detail: "检查表达式预热状态", apply: "self.ts.is_ready(" });
  return { from: token.from, options: [...methods.values()].filter(item => item.label.startsWith(token.text)), validFor: /^self(?:\.[A-Za-z_]\w*){0,2}\.?$/ };
}
const outsideOperator = source => context => context.matchBefore(/\bself(?:\.[A-Za-z_]\w*){0,2}\.?/) ? null : source(context);
function pythonGlobals(context) {
  const result = globalCompletion(context);
  return result && { ...result, options: result.options.filter(item => !["abs", "min", "max", "pow"].includes(item.label)) };
}
onMounted(() => {
  view = new EditorView({
    parent: host.value,
    doc: props.modelValue,
    extensions: [
      basicSetup,
      python(),
      autocompletion({ override: [operatorCompletion, outsideOperator(localCompletionSource), outsideOperator(pythonGlobals)] }),
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
