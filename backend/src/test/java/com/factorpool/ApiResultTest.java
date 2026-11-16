package com.factorpool;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.junit.jupiter.params.provider.ValueSource;
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

  String path(ReportSection section) {
    return "/api/tasks/" + id + "/result" + (section == ReportSection.CORE ? "" : "/" + section.name().toLowerCase());
  }

  @ParameterizedTest
  @EnumSource(ReportSection.class)
  void completedSectionUsesItsBoundProjectionWithoutJavaJsonParsing(ReportSection section) throws Exception {
    String report = "{ \"metadata\": {\"name\":\"成交额\"}, \"daily\":[-0.0,1.2345678901234567e-100], \"summary\":{} }\n";
    doReturn(Map.of("status", "SUCCEEDED")).when(store).task(id, false);
    when(db.queryForObject(section.sql(), String.class, id)).thenReturn(report);
    var api = MockMvcBuilders.standaloneSetup(new Api(store, mock(Tasks.class))).build();
    api.perform(get(path(section)))
        .andExpect(status().isOk())
        .andExpect(content().contentTypeCompatibleWith("application/json"))
        .andExpect(content().bytes(report.getBytes(StandardCharsets.UTF_8)));
    verify(db).queryForObject(section.sql(), String.class, id);
    verifyNoMoreInteractions(db);
    verify(store, never()).parse(anyString());
  }

  @ParameterizedTest
  @EnumSource(ReportSection.class)
  void unfinishedResultRemainsHiddenForEverySection(ReportSection section) throws Exception {
    doReturn(Map.of("status", "RUNNING")).when(store).task(id, false);
    var api = MockMvcBuilders.standaloneSetup(new Api(store, mock(Tasks.class))).build();
    api.perform(get(path(section))).andExpect(status().isConflict());
    verifyNoInteractions(db);
  }

  @ParameterizedTest
  @ValueSource(strings = {"FAILED", "CANCELLED", "QUEUED"})
  void nonSuccessfulTasksCannotExportAReport(String status) throws Exception {
    doReturn(Map.of("status", status)).when(store).task(id, false);
    var api = MockMvcBuilders.standaloneSetup(new Api(store, mock(Tasks.class))).build();
    api.perform(get(path(ReportSection.EXPORT))).andExpect(status().isConflict());
    verifyNoInteractions(db);
  }

  @Test
  void unknownSectionIsRejectedBeforeAnyDatabaseAccess() throws Exception {
    var api = MockMvcBuilders.standaloneSetup(new Api(store, mock(Tasks.class))).build();
    api.perform(get("/api/tasks/" + id + "/result/full")).andExpect(status().isBadRequest());
    verifyNoInteractions(db);
    verify(store, never()).task(any(), anyBoolean());
  }

  @Test
  void missingTaskKeepsNotFoundBoundary() throws Exception {
    doThrow(new NoSuchElementException("Task not found")).when(store).task(id, false);
    var api = MockMvcBuilders.standaloneSetup(new Api(store, mock(Tasks.class))).build();
    api.perform(get(path(ReportSection.IC))).andExpect(status().isNotFound());
    verifyNoInteractions(db);
  }
}
