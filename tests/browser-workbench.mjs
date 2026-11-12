// Exercise real API snapshots and the state that BRAIN-style controls expose.
import { createRequire } from "node:module";
import assert from "node:assert/strict";
const require = createRequire(import.meta.url);
const { chromium } = require(
  process.env.FACTORPOOL_PLAYWRIGHT_MODULE || "playwright",
);
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1512, height: 1050 } });
const base = process.env.FACTORPOOL_URL || "http://127.0.0.1:8088";
const output = process.env.FACTORPOOL_BROWSER_OUTPUT || "/tmp";
const errors = [],
  posts = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("request", (r) => {
  if (r.method() === "POST")
    posts.push({ url: r.url(), data: r.postDataJSON() });
});
await page.goto(base, { waitUntil: "networkidle" });
const editor = page.locator(".cm-content");
await editor.waitFor();
const original = await editor.innerText();
const examples = await (await page.request.get(`${base}/api/examples`)).json();
const expectedSource = examples.find(
  (e) => e.name === "low_volume.py",
).source_code;
const settings = page.getByRole("region", { name: "模拟设置" });
await page.getByRole("button", { name: "Settings", exact: true }).click();
await settings.getByLabel("构造参数 JSON").fill("[]");
await settings.getByRole("button", { name: "Apply", exact: true }).click();
assert.match(await settings.getByRole("alert").innerText(), /JSON 对象/);
await settings.getByLabel("构造参数 JSON").fill('{"period":12}');
await settings.getByRole("button", { name: "Cancel", exact: true }).click();
await page.getByRole("button", { name: "Settings", exact: true }).click();
assert.equal(
  await settings.getByLabel("构造参数 JSON").inputValue(),
  '{"period":20}',
);
await settings.getByLabel('数据源', {exact: true}).selectOption('fixture');
await settings.getByLabel("构造参数 JSON").fill('{"period":3}');
await settings.getByLabel("分析周期").fill("1,5");
await settings.getByLabel("开始日期").fill("2024-01-15");
await settings.getByLabel("结束日期").fill("2024-01-29");
await page.screenshot({
  path: `${output}/workbench-settings.png`,
  fullPage: true,
});
await settings.getByRole("button", { name: "Apply", exact: true }).click();
await page.getByLabel("因子名称", { exact: true }).fill("UI snapshot fixture");
await page.getByRole("button", { name: "运行研究", exact: true }).click();
await page.locator(".badge.SUCCEEDED").waitFor({ timeout: 40000 });
await page.locator(".metrics").waitFor();
const submission = posts.find((r) => r.url.endsWith("/api/tasks")).data;
assert.equal(submission.source_code, expectedSource);
assert.equal(submission.factor_class, "LowVolumeFactor");
assert.deepEqual(submission.factor_params, { period: 3 });
assert.deepEqual(submission.analysis.horizons, [1, 5]);
assert.equal(submission.data.provider, "fixture");
assert.equal(submission.analysis.entry, 'signal_close');
assert.equal(submission.analysis.exit, 'next_close');
const metrics = await page.locator(".metrics strong").allTextContents();
const taskId = await page.evaluate(() =>
  sessionStorage.getItem("factorpool.task"),
);
assert.ok(taskId);
const snapshot = (
  await (await page.request.get(`${base}/api/tasks/${taskId}`)).json()
).snapshot;
assert.equal(snapshot.source_code, expectedSource);
assert.deepEqual(snapshot.factor_params, { period: 3 });
await page.getByRole("button", { name: "复制为新模拟", exact: true }).click();
assert.equal(
  await page.getByRole("tab", { selected: true }).innerText(),
  "Simulation 2",
);
assert.equal(await editor.innerText(), original);
await page.getByLabel("因子名称", { exact: true }).fill("Clone draft");
await editor.click();
await page.keyboard.press("Control+End");
await page.keyboard.insertText("\n# clone-only draft");
await page.getByRole("tab", { name: "Simulation 1", exact: true }).click();
await page.locator(".badge.SUCCEEDED").waitFor();
await page.locator(".metrics").waitFor();
assert.equal(
  await page.getByLabel("因子名称", { exact: true }).inputValue(),
  "UI snapshot fixture",
);
assert.equal(await editor.innerText(), original);
assert.deepEqual(
  await page.locator(".metrics strong").allTextContents(),
  metrics,
);
await page.getByRole("tab", { name: "Simulation 2", exact: true }).click();
assert.equal(
  await page.getByLabel("因子名称", { exact: true }).inputValue(),
  "Clone draft",
);
assert.match(await editor.innerText(), /# clone-only draft/);
assert.equal(posts.length, 1, "Cloning and switching must not submit or save");
await page.getByRole("tab", { name: "Simulation 1", exact: true }).click();
await page.locator(".badge.SUCCEEDED").waitFor();
await page.locator(".metrics").waitFor();
await page.getByRole("button", { name: "IC Analysis", exact: true }).click();
await page.getByRole("button", { name: "5 期", exact: true }).click();
assert.equal(
  await page.locator(".result-periods button.active").innerText(),
  "5D",
);
await page.getByRole("button", { name: "Chart", exact: true }).click();
await page.getByRole("button", { name: "5D", exact: true }).click();
await page.getByRole("button", { name: "1D", exact: true }).click();
const separator = page.getByRole("separator", {
  name: "调整代码与研究结果比例",
});
await separator.focus();
await page.keyboard.press("ArrowLeft");
assert.equal(await separator.getAttribute("aria-valuenow"), "48");
await page.getByRole("checkbox", { name: "Code", exact: true }).uncheck();
assert.equal(await editor.isVisible(), false);
assert.equal(
  await page
    .getByRole("checkbox", { name: "Results", exact: true })
    .isDisabled(),
  true,
);
await page.getByRole("checkbox", { name: "Code", exact: true }).check();
await page.getByRole("checkbox", { name: "Results", exact: true }).uncheck();
assert.equal(await page.locator(".results-workspace").isVisible(), false);
await page.getByRole("checkbox", { name: "Results", exact: true }).check();
const downloadPromise = page.waitForEvent("download");
await page.getByRole("button", { name: "Export Results", exact: true }).click();
const download = await downloadPromise;
await download.saveAs(`${output}/workbench-export.json`);
const { readFile } = await import("node:fs/promises");
const exported = JSON.parse(
  await readFile(`${output}/workbench-export.json`, "utf8"),
);
assert.equal(exported.task.id, taskId);
assert.deepEqual(exported.task.snapshot, snapshot);
await page.getByRole("button", { name: "Source", exact: true }).click();
await page.getByRole("button", { name: "载入为新模拟", exact: true }).click();
assert.equal(
  await page.getByRole("tab", { selected: true }).innerText(),
  "Simulation 3",
);
assert.equal(await editor.innerText(), original);
await page.getByRole("button", { name: "Settings", exact: true }).click();
assert.equal(
  await settings.getByLabel("构造参数 JSON").inputValue(),
  '{"period":3}',
);
assert.equal(await settings.getByLabel("分析周期").inputValue(), "1,5");
await settings.getByRole("button", { name: "Cancel", exact: true }).click();
assert.equal(posts.length, 1);
// Use an existing report for the visual review, without submitting market-data work.
const tasks = await (await page.request.get(`${base}/api/tasks`)).json();
let real, realSnapshot;
for (const t of tasks.filter((t) => t.status === "SUCCEEDED").slice(0, 12)) {
  const d = await (await page.request.get(`${base}/api/tasks/${t.id}`)).json();
  if (["market_matrix", "ft_daily_mmap"].includes(d.snapshot.data.provider)) {
    real = t;
    realSnapshot = d.snapshot;
    break;
  }
}
if (real) {
  await page.evaluate(
    (id) => sessionStorage.setItem("factorpool.task", id),
    real.id,
  );
  await page.reload({ waitUntil: "networkidle" });
  await page.locator(".brain-summary").waitFor();
}
await page.waitForTimeout(500);
await page.screenshot({
  path: `${output}/workbench-desktop.png`,
  fullPage: true,
});
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(500);
assert.ok(
  await page.evaluate(() => document.documentElement.scrollWidth <= 392),
);
await page.screenshot({
  path: `${output}/workbench-mobile.png`,
  fullPage: true,
});
await page.getByRole("button", { name: "Settings", exact: true }).click();
if (real) {
  assert.equal(await settings.getByLabel('数据源', {exact: true}).inputValue(), 'market_matrix');
  const unchanged = await (await page.request.get(`${base}/api/tasks/${real.id}`)).json();
  assert.deepEqual(unchanged.snapshot, realSnapshot, 'Viewing a legacy report must preserve its original task snapshot');
}
await page.screenshot({
  path: `${output}/workbench-settings-mobile.png`,
  fullPage: true,
});
assert.deepEqual(errors, []);
console.log(
  JSON.stringify({
    settings: "apply/cancel/invalid",
    snapshot: "unchanged PythonIndicator source",
    workspaces: "independent drafts and results",
    layout: "resize/code/results/mobile",
    export: "immutable task snapshot",
    browser_errors: errors,
  }),
);
await browser.close();
