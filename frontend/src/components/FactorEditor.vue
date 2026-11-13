<script setup>
import { onMounted, onBeforeUnmount, watch, ref } from "vue";
import { EditorView, basicSetup } from "codemirror";
import { python, globalCompletion, localCompletionSource } from "@codemirror/lang-python";
import { autocompletion } from "@codemirror/autocomplete";
import { darculaTheme } from "../editorTheme";
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
      darculaTheme,
      EditorView.theme({
        "&": { height: "100%" },
        ".cm-scroller": {
          overflow: "auto",
          fontFamily: "monospace",
          lineHeight: "1.6",
          fontSize: "13px",
        },
        ".cm-content": { padding: "16px 0" },
      }),
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
