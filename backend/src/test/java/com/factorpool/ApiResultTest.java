package com.factorpool;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class ApiResultTest {
  final UUID id = UUID.randomUUID();
  final JdbcTemplate db = mock(JdbcTemplate.class);
  final Store store = spy(new Store(db, new ObjectMapper(),
      mock(PlatformTransactionManager.class), mock(ScriptResolver.class), mock(DataClient.class)));

  @Test
  void completeResultIsReturnedAsJsonWithoutBuildingAnotherObjectTree() throws Exception {
    String report = "{ \"metadata\": {\"name\":\"成交额\"}, \"daily\":[-0.0,1.2345678901234567e-100], \"summary\":{} }\n";
    doReturn(Map.of("status", "SUCCEEDED")).when(store).task(id, false);
    when(db.queryForObject("SELECT result::text FROM research_results WHERE task_id=?", String.class, id))
        .thenReturn(report);
    var api = MockMvcBuilders.standaloneSetup(new Api(store, mock(Tasks.class))).build();
    api.perform(get("/api/tasks/" + id + "/result"))
        .andExpect(status().isOk())
        .andExpect(content().contentTypeCompatibleWith("application/json"))
        .andExpect(content().bytes(report.getBytes(StandardCharsets.UTF_8)))
        .andExpect(jsonPath("$.metadata.name").value("成交额"));
    verify(store, never()).parse(anyString());
  }

  @Test
  void unfinishedResultRemainsHidden() throws Exception {
    doReturn(Map.of("status", "RUNNING")).when(store).task(id, false);
    var api = MockMvcBuilders.standaloneSetup(new Api(store, mock(Tasks.class))).build();
    api.perform(get("/api/tasks/" + id + "/result")).andExpect(status().isConflict());
    verifyNoInteractions(db);
  }
}
