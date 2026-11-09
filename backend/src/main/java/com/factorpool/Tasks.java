package com.factorpool;
import com.fasterxml.jackson.databind.*;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class Tasks {
  final Store store;
  final Path root;
  final int workers;
  final long timeout;
  final ExecutorService executor;
  final ConcurrentHashMap<UUID, Process> processes = new ConcurrentHashMap<>();
  final Set<UUID> assigned = ConcurrentHashMap.newKeySet();
  volatile boolean closing = false;
  Tasks(Store store, @Value("${factorpool.task-root}") String root,
        @Value("${factorpool.max-concurrent}") int workers,
        @Value("${factorpool.timeout-seconds}") long timeout)
      throws IOException {
    if (workers < 1 || workers > 8 || timeout < 1)
      throw new IllegalArgumentException(
          "Invalid worker/timeout configuration");
    this.store = store;
    this.root = Path.of(root);
    this.workers = workers;
    this.timeout = timeout;
    this.executor = Executors.newFixedThreadPool(workers);
    Files.createDirectories(this.root);
  }
  @PostConstruct
  void recover() {
    store.db.update(
        "UPDATE research_tasks SET status='INTERRUPTED',error='Backend " +
        "restarted before completion',finished_at=now() WHERE status IN " +
        "('RUNNING','CANCELLING')");
  }
  @Scheduled(fixedDelay = 500)
  synchronized void dispatch() {
    if (closing)
      return;
    int capacity = workers - assigned.size();
    if (capacity <= 0)
      return;
    for (var row :
         store.db.queryForList("SELECT id FROM research_tasks WHERE " +
                               "status='QUEUED' ORDER BY created_at LIMIT ?",
                               capacity)) {
      UUID id = (UUID)row.get("id");
      if (store.db.update(
              "UPDATE research_tasks SET status='RUNNING',started_at=now() " +
              "WHERE id=? AND status='QUEUED'",
              id) == 1) {
        assigned.add(id);
        executor.submit(() -> execute(id));
      }
    }
  }
  void stop(Process p) throws InterruptedException {
    var descendants = p.descendants().toList();
    descendants.forEach(ProcessHandle::destroy);
    p.destroy();
    if (!p.waitFor(5, TimeUnit.SECONDS)) {
      descendants.forEach(ProcessHandle::destroyForcibly);
      p.destroyForcibly();
      p.waitFor();
    }
    for (var child : descendants)
      if (child.isAlive())
        child.destroyForcibly();
  }
  void execute(UUID id) {
    Process process = null;
    try {
      Path directory = root.resolve(id.toString());
      Files.createDirectories(directory);
      var task = store.task(id, true);
      if (!task.get("status").equals("RUNNING")) {
        cancelled(id);
        return;
      }
      Files.writeString(directory.resolve("request.json"),
                        task.get("snapshot").toString());
      process = new ProcessBuilder("/usr/local/bin/python3", "-m",
                                   "factorpool.runner", directory.toString())
                    .redirectErrorStream(true)
                    .redirectOutput(directory.resolve("runner.log").toFile())
                    .start();
      processes.put(id, process);
      if (store.task(id, false).get("status").equals("CANCELLING"))
        stop(process);
      if (!process.waitFor(timeout, TimeUnit.SECONDS)) {
        stop(process);
        throw new IOException("Task timed out after " + timeout + " seconds");
      }
      String state = store.task(id, false).get("status").toString();
      if (state.equals("CANCELLING")) {
        cancelled(id);
        return;
      }
      if (closing) {
        finish(id, "INTERRUPTED", "Backend stopped");
        return;
      }
      if (process.exitValue() != 0)
        throw new IOException("Research process exited " + process.exitValue() +
                              ". " + logs(id));
      importResult(id, directory);
    } catch (Exception e) {
      if (process != null && process.isAlive())
        try {
          stop(process);
        } catch (InterruptedException interrupted) {
          Thread.currentThread().interrupt();
        }
      var state = store.task(id, false).get("status");
      if (state.equals("CANCELLING"))
        cancelled(id);
      else
        finish(id, closing ? "INTERRUPTED" : "FAILED", e.getMessage());
    } finally {
      processes.remove(id);
      assigned.remove(id);
    }
  }
  void importResult(UUID id, Path directory) throws IOException {
    JsonNode result =
        store.json.readTree(directory.resolve("result.json").toFile());
    if (!result.has("metadata") || !result.has("summary") ||
        !result.has("daily"))
      throw new IOException("Incomplete research result");
    store.tx.execute(status -> {
      String current = store.db.queryForObject(
          "SELECT status FROM research_tasks WHERE id=? FOR UPDATE",
          String.class, id);
      if (!"RUNNING".equals(current)) {
        if ("CANCELLING".equals(current))
          cancelled(id);
        return null;
      }
      store.db.update(
          "INSERT INTO research_results(task_id,result) VALUES (?,?::jsonb)",
          id, result.toString());
      try (var reader =
               Files.newBufferedReader(directory.resolve("values.jsonl"))) {
        List<Object[]> batch = new ArrayList<>();
        String line;
        long ordinal = 0;
        while ((line = reader.readLine()) != null) {
          var row = store.parse(line);
          batch.add(new Object[] {
              id, ordinal++, java.sql.Date.valueOf(row.path("date").asText()),
              row.path("symbol").asText(),
              row.path("value").isNumber() ? row.get("value").asDouble() : null,
              row.path("valid").asBoolean(), row.path("ready").asBoolean(),
              row.path("reason").asText(),
              row.path("forward_return").isNumber()
                  ? row.get("forward_return").asDouble()
                  : null,
              row.path("group").isNumber() ? row.get("group").asInt() : null});
          if (batch.size() >= 1000) {
            insert(batch);
            batch.clear();
          }
        }
        if (!batch.isEmpty())
          insert(batch);
        if (ordinal != result.path("metadata").path("value_count").asLong(-1))
          throw new IOException("Incomplete factor values");
      } catch (IOException e) {
        throw new UncheckedIOException(e);
      }
      finish(id, "SUCCEEDED", null);
      return null;
    });
  }
  void insert(List<Object[]> rows) {
    store.db.batchUpdate(
        "INSERT INTO " +
        "factor_values(task_id,ordinal,trading_date,symbol,value,valid,ready," +
        "reason,forward_return,factor_group) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows);
  }
  void finish(UUID id, String state, String error) {
    store.db.update("UPDATE research_tasks SET " +
                    "status=?,error=?,finished_at=now() WHERE id=?",
                    state,
                    error == null
                        ? null
                        : error.substring(0, Math.min(4000, error.length())),
                    id);
  }
  void cancelled(UUID id) { finish(id, "CANCELLED", null); }
  synchronized Map<String, Object> cancel(UUID id) {
    store.db.update(
        "UPDATE research_tasks SET status=CASE WHEN status='QUEUED' THEN " +
        "'CANCELLED' ELSE 'CANCELLING' END,finished_at=CASE WHEN " +
        "status='QUEUED' THEN now() ELSE finished_at END WHERE id=? AND " +
        "status IN ('QUEUED','RUNNING')",
        id);
    Process p = processes.get(id);
    if (p != null)
      try {
        stop(p);
      } catch (InterruptedException e) {
        Thread.currentThread().interrupt();
      }
    return store.task(id, false);
  }
  String logs(UUID id) throws IOException {
    Path file = root.resolve(id.toString()).resolve("runner.log");
    if (!Files.exists(file))
      return "";
    try (var reader = new RandomAccessFile(file.toFile(), "r")) {
      long start = Math.max(0, reader.length() - 65536);
      reader.seek(start);
      byte[] bytes = new byte[(int)(reader.length() - start)];
      reader.readFully(bytes);
      return new String(bytes, java.nio.charset.StandardCharsets.UTF_8);
    }
  }
  @PreDestroy
  void close() {
    closing = true;
    for (Process p : processes.values())
      try {
        stop(p);
      } catch (InterruptedException e) {
        Thread.currentThread().interrupt();
      }
    executor.shutdown();
    try {
      executor.awaitTermination(10, TimeUnit.SECONDS);
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
    }
  }
}
