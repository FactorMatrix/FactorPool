package com.factorpool;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class ApiScriptTest {
  final ObjectMapper json = new ObjectMapper();
  final ScriptResolver scripts = mock(ScriptResolver.class);
  final Store store = new Store(mock(JdbcTemplate.class), json,
      mock(PlatformTransactionManager.class), scripts);

  @Test
  void resolveEndpointAcceptsSourceWithoutExternalFactorClass() throws Exception {
    var normalized = json.createObjectNode().put("source_mode", "script").put("factor_class", "MyFactor");
    normalized.putObject("source_config");
    when(scripts.resolve("source only")).thenReturn(normalized);
    var api = MockMvcBuilders.standaloneSetup(new Api(store, mock(Tasks.class))).build();
    api.perform(post("/api/script/resolve").contentType("application/json")
        .content("{\"source_code\":\"source only\"}"))
        .andExpect(status().isOk()).andExpect(jsonPath("$.factor_class").value("MyFactor"));
    verify(scripts).resolve("source only");
    verifyNoInteractions(store.db);
  }

  @Test
  void badScriptIsA400WithItsLineNumber() throws Exception {
    when(scripts.resolve("bad")).thenThrow(new IllegalArgumentException("line 3: config must be literal"));
    var api = MockMvcBuilders.standaloneSetup(new Api(store, mock(Tasks.class))).build();
    api.perform(post("/api/script/resolve").contentType("application/json")
        .content("{\"source_code\":\"bad\"}"))
        .andExpect(status().isBadRequest()).andExpect(jsonPath("$.error").value("line 3: config must be literal"));
    verifyNoInteractions(store.db);
  }
}
