package com.factorpool;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class TasksTest {
  final ObjectMapper json=new ObjectMapper();
  final JdbcTemplate db=mock(JdbcTemplate.class);
  final DataClient data=mock(DataClient.class);
  final Store store=new Store(db,json,mock(PlatformTransactionManager.class),mock(ScriptResolver.class),data);
  final Tasks tasks=new Tasks(store,1,1800,30,3);
  final UUID id=UUID.randomUUID(),token=UUID.randomUUID();
  ObjectNode body() {
    return json.createObjectNode().put("task_id",id.toString()).put("attempt",2)
      .put("lease_token",token.toString()).put("worker_id","worker-two");
  }
  void active(String state,boolean live) {
    when(db.queryForList(contains("FOR UPDATE"),eq(id))).thenReturn(List.of(new HashMap<>(Map.of(
      "id",id,"status",state,"execution_attempt",2,"lease_token",token,"worker_id","worker-two","live",live))));
    when(db.queryForList(startsWith("SELECT id,batch_id,name,status,error,created_at,started_at,finished_at FROM"),eq(id)))
      .thenReturn(List.of(new HashMap<>(Map.of("id",id,"status",state))));
  }
  @Test void oldTokenCannotRenewOrFinishAnotherAttempt() {
    active("RUNNING",true);
    var body=body().put("lease_token",UUID.randomUUID().toString());
    assertThrows(IllegalStateException.class,()->tasks.heartbeat(body));
    assertThrows(IllegalStateException.class,()->tasks.fail(body));
    verify(db,never()).update(anyString(),any(Object[].class));
  }
  @Test void expiredLeaseCannotRenewOrSubmitFailure() {
    active("RUNNING",false);
    assertThrows(IllegalStateException.class,()->tasks.heartbeat(body()));
    assertThrows(IllegalStateException.class,()->tasks.fail(body()));
    verify(db,never()).update(anyString(),any(Object[].class));
  }
  @Test void liveCancellationReachesWorkerAndCannotBecomeFailed() {
    active("CANCELLING",true);
    assertEquals("CANCELLING",tasks.heartbeat(body()).get("status"));
    tasks.fail(body().put("status","FAILED").put("error","killed"));
    verify(db).update(contains("SET status=?,error=?"),eq("CANCELLED"),isNull(),eq(id));
  }
  @Test void completeChecksPublishedValuesBeforeAnyStateOrSummaryWrite() {
    var body=body();
    var result=body.putObject("result");result.putObject("metadata").put("value_count",4);result.putObject("summary");result.putArray("daily");
    doThrow(new IllegalStateException("Incomplete factor values")).when(data).verifyPublication(body,4);
    assertThrows(IllegalStateException.class,()->tasks.complete(body));
    verifyNoInteractions(db);
  }
  @Test void successfulResponseLossRetryReturnsSameResultWithoutRepublishing() {
    active("SUCCEEDED",false);
    var body=body();
    var result=body.putObject("result");result.putObject("metadata").put("value_count",4);result.putObject("summary");result.putArray("daily");
    when(db.queryForObject("SELECT result::text FROM research_results WHERE task_id=?",String.class,id))
      .thenReturn(result.toString());
    assertEquals("SUCCEEDED",tasks.complete(body).get("status"));
    verify(data).verifyPublication(body,4);
    verify(db,never()).update(anyString(),any(Object[].class));
    result.put("unexpected_late_mutation",true);
    assertThrows(IllegalStateException.class,()->tasks.complete(body));
  }
  @Test void globalCapacityIsCheckedWhileHoldingDatabaseQueueLock() {
    when(db.queryForObject(startsWith("SELECT count(*)"),eq(Integer.class))).thenReturn(1);
    assertNull(tasks.claim("worker-two"));
    var order=inOrder(db);
    order.verify(db).execute("SELECT pg_advisory_xact_lock(194736281)");
    order.verify(db).update(contains("Worker lease expired"),eq(3),eq(3));
    order.verify(db).update(contains("Task timed out"),eq(1800L));
    order.verify(db).queryForObject(startsWith("SELECT count(*)"),eq(Integer.class));
    verify(db,never()).queryForList(contains("SKIP LOCKED"));
  }
  @Test void completedTaskCannotBeOverwrittenByLateFailureOrLogs() {
    active("SUCCEEDED",false);
    assertThrows(IllegalStateException.class,()->tasks.fail(body()));
    assertThrows(IllegalStateException.class,()->tasks.log(body().put("text","late")));
    verify(db,never()).update(anyString(),any(Object[].class));
  }
}
