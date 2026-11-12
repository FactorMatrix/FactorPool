<script setup>
import { computed, ref } from "vue";
import { pythonLanguage } from "@codemirror/lang-python";
import { classHighlighter, highlightTree } from "@lezer/highlight";
const props = defineProps({ code: { type: String, required: true }, label: { type: String, default: "代码示例" }, language: { type: String, default: "python" } });
const feedback = ref("");
const tokens = computed(() => {
  if (props.language !== "python") return [{ text: props.code, classes: "" }];
  const result = []; let cursor = 0;
  highlightTree(pythonLanguage.parser.parse(props.code), classHighlighter, (from, to, classes) => {
    if (from > cursor) result.push({ text: props.code.slice(cursor, from), classes: "" });
    result.push({ text: props.code.slice(from, to), classes }); cursor = to;
  });
  if (cursor < props.code.length) result.push({ text: props.code.slice(cursor), classes: "" });
  return result;
});
function copyWithoutClipboardApi() {
  const previous = document.activeElement;
  const selection = window.getSelection();
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index).cloneRange()) : [];
  const input = document.createElement("textarea");
  input.value = props.code; input.readOnly = true; input.setAttribute("aria-hidden", "true");
  input.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
  document.body.appendChild(input);
  try { input.select(); return document.execCommand("copy"); }
  finally {
    input.remove(); previous?.focus({ preventScroll: true });
    if (selection) { selection.removeAllRanges(); for (const range of ranges) selection.addRange(range); }
  }
}
async function copy() {
  let copied = false;
  try { if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(props.code); copied = true; } } catch { /* HTTP IP addresses can use the user-initiated fallback. */ }
  if (!copied) { try { copied = copyWithoutClipboardApi(); } catch { /* Selection remains available. */ } }
  feedback.value = copied ? "已复制" : "请选中代码后手动复制";
}
</script>
<template>
  <div class="doc-code">
    <div class="doc-code-bar"><span>{{ language === 'python' ? 'Python' : language.toUpperCase() }}</span><button :aria-label="label === '入门完整脚本' ? '复制完整示例' : '复制代码'" @click="copy">{{ feedback || '复制代码' }}</button></div>
    <pre tabindex="0" :aria-label="label"><code><span v-for="(token, index) in tokens" :key="index" :class="token.classes">{{ token.text }}</span></code></pre>
    <span v-if="feedback" class="copy-status" role="status">{{ feedback }}</span>
  </div>
</template>
<style scoped>
.doc-code { margin: 22px 0; border: 1px solid #e2e7ed; border-radius: 6px; overflow: hidden; min-width: 0; background: #f6f8fa; }
.doc-code-bar { display: flex; justify-content: space-between; align-items: center; padding: 9px 16px; color: #738090; font-size: 11px; border-bottom: 1px solid #e7ebf0; }
.doc-code-bar button { border: 0; background: transparent; padding: 2px 0; color: #536b83; font-size: 11px; }
pre { margin: 0; padding: 18px 20px 21px; overflow-x: auto; white-space: pre; line-height: 1.85; font-size: 12px; }
code { font-family: "Liberation Mono", Consolas, monospace; }
.tok-keyword, .tok-operator { color: #a44574; }
.tok-string, .tok-string2 { color: #2d7666; }
.tok-comment { color: #909aa6; font-style: italic; }
.tok-number, .tok-bool, .tok-atom { color: #a06626; }
.tok-variableName, .tok-propertyName { color: #355777; }
.tok-typeName, .tok-className, .tok-definition { color: #3f60a1; }
.copy-status { display: block; padding: 0 16px 9px; font-size: 11px; color: #4a7c65; }
@media (max-width: 700px) { pre { padding: 15px; font-size: 11px; } }
</style>
