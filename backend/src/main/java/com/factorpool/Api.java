package com.factorpool;
import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.*;
import java.nio.file.*;
import java.util.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class Api {
  final Store store;
  final Tasks tasks;
  final Path settingsCatalog;
  @Autowired
  Api(Store store, Tasks tasks) {
    this(store, tasks, Path.of("/opt/factorpool/src/factorpool/settings_catalog.json"));
  }
  Api(Store store, Tasks tasks, Path settingsCatalog) {
    this.store = store;
    this.tasks = tasks;
    this.settingsCatalog = settingsCatalog;
  }
  @GetMapping("/factors")
  Object factors() {
    return store.factors();
  }
  @PostMapping("/factors")
  Object save(@RequestBody JsonNode body) {
    return store.factor(body);
  }
  @PostMapping("/script/resolve")
  Object resolve(@RequestBody JsonNode body) {
    if (!body.path("source_code").isTextual())
      throw new IllegalArgumentException("source_code is required");
    return store.scripts.resolve(body.path("source_code").asText());
  }
  @GetMapping("/factors/{id}")
  Object factor(@PathVariable UUID id) {
    return store.factor(id);
  }
  @GetMapping("/tasks")
  Object tasks() {
    return store.db.queryForList(
        "SELECT " +
        "id,batch_id,name,status,error,created_at,started_at,finished_at " +
        "FROM research_tasks ORDER BY created_at DESC LIMIT 200");
  }
  @PostMapping("/tasks")
  Object submit(@RequestBody JsonNode body) {
    return Map.of("id", store.enqueue(body, null));
  }
  @GetMapping("/tasks/{id}")
  Object task(@PathVariable UUID id) {
    return store.task(id, true);
  }
  @PostMapping("/tasks/{id}/cancel")
  Object cancel(@PathVariable UUID id) {
    return tasks.cancel(id);
  }
  @GetMapping("/tasks/{id}/logs")
  Object logs(@PathVariable UUID id) throws Exception {
    store.task(id, false);
    return Map.of("text", tasks.logs(id));
  }
  @GetMapping("/tasks/{id}/result")
  Object result(@PathVariable UUID id) {
    if (!store.task(id, false).get("status").equals("SUCCEEDED"))
      throw new IllegalStateException("Task has no complete result");
    return store.parse(store.db.queryForObject(
        "SELECT result::text FROM research_results WHERE task_id=?",
        String.class, id));
  }
  @GetMapping("/tasks/{id}/values")
  Object values(@PathVariable UUID id,
                @RequestParam(defaultValue = "0") long offset,
                @RequestParam(defaultValue = "100") int limit) {
    if (offset < 0 || limit < 1 || limit > 1000)
      throw new IllegalArgumentException("Invalid pagination");
    if (!store.task(id, false).get("status").equals("SUCCEEDED"))
      throw new IllegalStateException("Task has no complete values");
    return store.db.queryForList(
        "SELECT " +
        "trading_date,symbol,value,valid,ready,reason,forward_return,factor_" +
        "group FROM factor_values WHERE task_id=? ORDER BY ordinal LIMIT ? " +
        "OFFSET ?",
        id, limit, offset);
  }
  @PostMapping("/tasks/batch")
  Object batch(@RequestBody JsonNode body) {
    return store.batch(body);
  }
  @GetMapping("/batches/{id}")
  Object batch(@PathVariable UUID id) {
    return Map.of("id", id, "tasks",
                  store.db.queryForList(
                      "SELECT id,name,status,error FROM research_tasks WHERE " +
                      "batch_id=? ORDER BY created_at",
                      id));
  }
  @GetMapping("/examples")
  Object examples() throws Exception {
    List<Object> out = new ArrayList<>();
    try (var files = Files.list(Path.of("/opt/factorpool/examples/scripts"))) {
      for (Path p :
           files.filter(x -> x.toString().endsWith(".py")).sorted().toList())
        out.add(Map.of("name", p.getFileName().toString(), "source_code",
                       Files.readString(p)));
    }
    return out;
  }
  @GetMapping("/operators")
  Object operators() throws Exception {
    return store.json.readTree(
        Path.of("/opt/factorpool/docs/operators.json").toFile());
  }
  @GetMapping("/simulation-settings")
  Object simulationSettings() throws Exception {
    return store.json.readTree(settingsCatalog.toFile());
  }
  @GetMapping("/data-sources")
  Object sources() {
    return List.of(store.marketSource(), Map.of("id", "fixture", "name", "手算样例 · 合成数据 A/B/C",
                          "available", true, "frequency", List.of("1d"),
                          "adjustment", List.of("raw"), "synthetic", true));
  }
  @ExceptionHandler({IllegalArgumentException.class,
                     java.time.DateTimeException.class})
  ResponseEntity<?> bad(Exception e) {
    return ResponseEntity.badRequest().body(
        Map.of("error", Objects.toString(e.getMessage(), "Invalid request")));
  }
  @ExceptionHandler(NoSuchElementException.class)
  ResponseEntity<?> missing(Exception e) {
    return ResponseEntity.status(404).body(Map.of("error", e.getMessage()));
  }
  @ExceptionHandler(IllegalStateException.class)
  ResponseEntity<?> conflict(Exception e) {
    return ResponseEntity.status(409).body(Map.of("error", e.getMessage()));
  }
}
