package com.factorpool;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** Durable queue and fenced leases. Computation belongs to worker-service. */
@Component
public class Tasks {
  final Store store;
  final int workers, leaseSeconds, maxAttempts;
  final long timeout;
  Tasks(Store store, @Value("${factorpool.max-concurrent}") int workers,
        @Value("${factorpool.timeout-seconds}") long timeout,
        @Value("${factorpool.lease-seconds:30}") int leaseSeconds,
        @Value("${factorpool.max-attempts:3}") int maxAttempts) {
    if (workers < 1 || workers > 8 || timeout < 1 || leaseSeconds < 5 || leaseSeconds > 300 || maxAttempts < 1 || maxAttempts > 10)
      throw new IllegalArgumentException("Invalid worker/lease/timeout configuration");
    this.store = store;
    this.workers = workers;
    this.timeout = timeout;
    this.leaseSeconds = leaseSeconds;
    this.maxAttempts = maxAttempts;
  }
  @Scheduled(fixedDelay = 2000)
  void recover() {
    store.tx.execute(status -> {
      store.db.execute("SELECT pg_advisory_xact_lock(194736281)");
      recoverLocked();
      return null;
    });
  }
  void recoverLocked() {
    // An API restart does not invalidate healthy workers on another host.
    store.db.update("UPDATE research_tasks SET status=CASE WHEN status='CANCELLING' THEN 'CANCELLED' "
        + "WHEN execution_attempt>=? THEN 'INTERRUPTED' ELSE 'QUEUED' END, "
        + "error=CASE WHEN status='CANCELLING' THEN NULL ELSE 'Worker lease expired' END, "
        + "finished_at=CASE WHEN status='CANCELLING' OR execution_attempt>=? THEN now() ELSE NULL END, "
        + "lease_expires_at=NULL WHERE status IN ('RUNNING','CANCELLING') AND "
        + "(lease_expires_at IS NULL OR lease_expires_at<=clock_timestamp())", maxAttempts, maxAttempts);
    store.db.update("UPDATE research_tasks SET status='FAILED',error='Task timed out',finished_at=now(), "
        + "lease_expires_at=NULL WHERE status='RUNNING' AND started_at<clock_timestamp()-(? * interval '1 second')", timeout);
  }
  Map<String,Object> claim(String worker) {
    if (worker == null || !worker.matches("[A-Za-z0-9_.:-]{1,128}"))
      throw new IllegalArgumentException("Invalid worker_id");
    return store.tx.execute(status -> {
      store.db.execute("SELECT pg_advisory_xact_lock(194736281)");
      recoverLocked();
      int running = store.db.queryForObject("SELECT count(*) FROM research_tasks WHERE status IN ('RUNNING','CANCELLING')", Integer.class);
      if (running >= workers) return null;
      var queued = store.db.queryForList("SELECT id FROM research_tasks WHERE status='QUEUED' ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1");
      if (queued.isEmpty()) return null;
      UUID id = (UUID)queued.get(0).get("id");
      UUID token = UUID.randomUUID();
      var row = store.db.queryForMap("UPDATE research_tasks SET status='RUNNING',execution_attempt=execution_attempt+1, "
          + "worker_id=?,lease_token=?,lease_expires_at=clock_timestamp()+(? * interval '1 second'),heartbeat_at=clock_timestamp(), "
          + "started_at=clock_timestamp(),finished_at=NULL,error=NULL,runner_log='' WHERE id=? "
          + "RETURNING snapshot::text,execution_attempt", worker, token, leaseSeconds, id);
      return Map.of("task_id",id,"attempt",row.get("execution_attempt"),"lease_token",token,
          "worker_id",worker,"snapshot",store.parse((String)row.get("snapshot")),
          "lease_seconds",leaseSeconds,"timeout_seconds",timeout);
    });
  }
  Map<String,Object> lease(JsonNode request) {
    UUID id = UUID.fromString(request.path("task_id").asText());
    UUID token = UUID.fromString(request.path("lease_token").asText());
    if (!request.path("attempt").isIntegralNumber() || !request.path("attempt").canConvertToInt() || request.path("attempt").asLong() < 1)
      throw new IllegalArgumentException("Invalid attempt");
    int attempt = request.path("attempt").intValue();
    var rows = store.db.queryForList("WITH locked AS MATERIALIZED (SELECT id,status,execution_attempt,lease_token,worker_id,lease_expires_at "
        + "FROM research_tasks WHERE id=? FOR UPDATE) SELECT id,status,execution_attempt,lease_token,worker_id,"
        + "lease_expires_at>clock_timestamp() AS live FROM locked",id);
    if (rows.isEmpty()) throw new NoSuchElementException("Task not found");
    var row = rows.get(0);
    if (((Number)row.get("execution_attempt")).intValue()!=attempt || !token.equals(row.get("lease_token"))
        || !request.path("worker_id").asText().equals(row.get("worker_id")))
      throw new IllegalStateException("Stale worker lease");
    return row;
  }
  Map<String,Object> heartbeat(JsonNode body) {
    return store.tx.execute(status -> {
      var row = lease(body);
      String state = (String)row.get("status");
      if (!Set.of("RUNNING","CANCELLING").contains(state) || !Boolean.TRUE.equals(row.get("live")))
        throw new IllegalStateException("Worker lease is no longer active");
      store.db.update("UPDATE research_tasks SET heartbeat_at=clock_timestamp(),lease_expires_at=clock_timestamp()+(? * interval '1 second') WHERE id=?",leaseSeconds,row.get("id"));
      return Map.of("status",state,"lease_seconds",leaseSeconds);
    });
  }
  Map<String,Object> complete(JsonNode body) {
    UUID id = UUID.fromString(body.path("task_id").asText());
    var result = body.path("result");
    if (!result.isObject() || !result.has("metadata") || !result.has("summary") || !result.has("daily")
        || !result.path("metadata").path("value_count").isIntegralNumber()
        || !result.path("metadata").path("value_count").canConvertToLong()
        || result.path("metadata").path("value_count").asLong(-1)<0)
      throw new IllegalArgumentException("Incomplete research result");
    long expected = result.path("metadata").path("value_count").asLong();
    // Data service atomically stages/publishes values first. Public readers still
    // require SUCCEEDED and matching result_attempt, so this receipt is not visibility.
    store.data.verifyPublication(body,expected);
    return store.tx.execute(status -> {
      var row = lease(body);
      String current = (String)row.get("status");
      if (current.equals("SUCCEEDED")) {
        var previous = store.parse(store.db.queryForObject("SELECT result::text FROM research_results WHERE task_id=?",String.class,id));
        if (!previous.equals(result)) throw new IllegalStateException("Completed result differs from this execution");
        return store.task(id,false);
      }
      if (!current.equals("RUNNING") || !Boolean.TRUE.equals(row.get("live")))
        throw new IllegalStateException("Worker lease is no longer active");
      store.db.update("INSERT INTO research_results(task_id,result) VALUES (?,?::jsonb) "
          + "ON CONFLICT(task_id) DO UPDATE SET result=excluded.result",id,result.toString());
      store.db.update("UPDATE research_tasks SET status='SUCCEEDED',result_attempt=execution_attempt,error=NULL,finished_at=now(),lease_expires_at=NULL WHERE id=?",id);
      return store.task(id,false);
    });
  }
  Map<String,Object> fail(JsonNode body) {
    String target = body.path("status").asText("FAILED");
    if (!Set.of("FAILED","INTERRUPTED","CANCELLED").contains(target))
      throw new IllegalArgumentException("Invalid failure status");
    String error = body.path("error").asText("");
    if (error.length()>4000) error=error.substring(0,4000);
    final String message=error;
    return store.tx.execute(status -> {
      var row = lease(body);
      String current=(String)row.get("status");
      if (current.equals(target)) return store.task((UUID)row.get("id"),false);
      if (!Set.of("RUNNING","CANCELLING").contains(current) || !Boolean.TRUE.equals(row.get("live")))
        throw new IllegalStateException("Worker lease is no longer active");
      String next=current.equals("CANCELLING") ? "CANCELLED" : target;
      store.db.update("UPDATE research_tasks SET status=?,error=?,finished_at=now(),lease_expires_at=NULL WHERE id=?",next,
          next.equals("CANCELLED")?null:message,row.get("id"));
      return store.task((UUID)row.get("id"),false);
    });
  }
  Map<String,Object> log(JsonNode body) {
    String text=body.path("text").asText("");
    if (text.length()>65536) throw new IllegalArgumentException("Log tail exceeds 64 KiB");
    return store.tx.execute(status -> {
      var row=lease(body);
      if (!Set.of("RUNNING","CANCELLING").contains(row.get("status")) || !Boolean.TRUE.equals(row.get("live")))
        throw new IllegalStateException("Worker lease is no longer active");
      store.db.update("UPDATE research_tasks SET runner_log=? WHERE id=?",text,row.get("id"));
      return Map.of("accepted",true);
    });
  }
  Map<String,Object> cancel(UUID id) {
    store.db.update("UPDATE research_tasks SET status=CASE WHEN status='QUEUED' THEN 'CANCELLED' ELSE 'CANCELLING' END, "
        + "finished_at=CASE WHEN status='QUEUED' THEN now() ELSE finished_at END WHERE id=? AND status IN ('QUEUED','RUNNING')",id);
    return store.task(id,false);
  }
  String logs(UUID id) {
    return store.db.queryForObject("SELECT runner_log FROM research_tasks WHERE id=?",String.class,id);
  }
}
