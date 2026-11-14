package com.factorpool;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import static org.mockito.Mockito.*;
import static org.hamcrest.Matchers.containsString;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class ApiScriptTest {
  final ObjectMapper json = new ObjectMapper();
  final ScriptResolver scripts = mock(ScriptResolver.class);
  final Store store = new Store(mock(JdbcTemplate.class), json,
      mock(PlatformTransactionManager.class), scripts,mock(DataClient.class));

  @Test
  void resolveEndpointAcceptsSourceWithoutExternalFactorClass() throws Exception {
    var normalized = json.createObjectNode().put("factor_class", "MyFactor");
    normalized.putObject("source_config");
    when(scripts.resolve("source only", null)).thenReturn(normalized);
    var api = MockMvcBuilders.standaloneSetup(new Api(store, mock(Tasks.class))).build();
    api.perform(post("/api/script/resolve").contentType("application/json")
        .content("{\"source_code\":\"source only\"}"))
        .andExpect(status().isOk()).andExpect(jsonPath("$.factor_class").value("MyFactor"));
    verify(scripts).resolve("source only", null);
    verifyNoInteractions(store.db);
  }

  @Test
  void badScriptIsA400WithItsLineNumber() throws Exception {
    when(scripts.resolve("bad", null)).thenThrow(new IllegalArgumentException("line 3: config must be literal"));
    var api = MockMvcBuilders.standaloneSetup(new Api(store, mock(Tasks.class))).build();
    api.perform(post("/api/script/resolve").contentType("application/json")
        .content("{\"source_code\":\"bad\"}"))
        .andExpect(status().isBadRequest()).andExpect(jsonPath("$.error").value("line 3: config must be literal"));
    verifyNoInteractions(store.db);
  }
  @Test
  void resolveEndpointPassesWorkspaceDefaultsWithoutInterpretingSource() throws Exception {
    var workspace = json.createObjectNode();
    workspace.putObject("settings").put("decay", 5);
    var normalized = json.createObjectNode().put("factor_class", "MyFactor");
    when(scripts.resolve("config = {}", workspace)).thenReturn(normalized);
    var body = json.createObjectNode().put("source_code", "config = {}");
    body.set("workspace_config", workspace);
    var api = MockMvcBuilders.standaloneSetup(new Api(store, mock(Tasks.class))).build();
    api.perform(post("/api/script/resolve").contentType("application/json").content(body.toString()))
        .andExpect(status().isOk()).andExpect(jsonPath("$.factor_class").value("MyFactor"));
    verify(scripts).resolve("config = {}", workspace);
  }

  @Test
  void resolveEndpointRejectsRemovedFieldsAndNonObjectDefaultsBeforeParsing() throws Exception {
    var api = MockMvcBuilders.standaloneSetup(new Api(store, mock(Tasks.class))).build();
    for (String field : java.util.List.of("frontend_only", "source_mode", "factor_class")) {
      var body = json.createObjectNode().put("source_code", "source only").put(field, "removed");
      api.perform(post("/api/script/resolve").contentType("application/json").content(body.toString()))
          .andExpect(status().isBadRequest()).andExpect(jsonPath("$.error", containsString(field)));
    }
    var nullDefaults = json.createObjectNode().put("source_code", "source only").putNull("workspace_config");
    api.perform(post("/api/script/resolve").contentType("application/json").content(nullDefaults.toString()))
        .andExpect(status().isBadRequest()).andExpect(jsonPath("$.error", containsString("workspace_config")));
    verifyNoInteractions(scripts, store.db);
  }

}
