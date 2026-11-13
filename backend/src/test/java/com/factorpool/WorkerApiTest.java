package com.factorpool;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class WorkerApiTest {
  final String secret="isolated-test-service-secret-32";
  final Tasks tasks=mock(Tasks.class);
  final ObjectMapper json=new ObjectMapper();
  @Test void internalTokenIsRequiredBeforeClaim() throws Exception {
    var api=MockMvcBuilders.standaloneSetup(new WorkerApi(tasks,secret)).build();
    api.perform(post("/internal/worker/claim").contentType("application/json").content("{\"worker_id\":\"worker-one\"}"))
      .andExpect(status().isUnauthorized());
    api.perform(post("/internal/worker/claim").header("X-FactorPool-Internal-Token","incorrect")
      .contentType("application/json").content("{\"worker_id\":\"worker-one\"}"))
      .andExpect(status().isUnauthorized());
    verifyNoInteractions(tasks);
  }
  @Test void noWorkIs204RatherThanAnEmptyTask() throws Exception {
    when(tasks.claim("worker-one")).thenReturn(null);
    var api=MockMvcBuilders.standaloneSetup(new WorkerApi(tasks,secret)).build();
    api.perform(post("/internal/worker/claim").header("X-FactorPool-Internal-Token",secret)
      .contentType("application/json").content("{\"worker_id\":\"worker-one\"}"))
      .andExpect(status().isNoContent());
    verify(tasks).claim("worker-one");
  }
  @Test void staleHeartbeatIs409() throws Exception {
    when(tasks.heartbeat(any())).thenThrow(new IllegalStateException("Stale worker lease"));
    var api=MockMvcBuilders.standaloneSetup(new WorkerApi(tasks,secret)).build();
    api.perform(post("/internal/worker/heartbeat").header("X-FactorPool-Internal-Token",secret)
      .contentType("application/json").content("{}"))
      .andExpect(status().isConflict()).andExpect(jsonPath("$.error").value("Stale worker lease"));
  }
}
