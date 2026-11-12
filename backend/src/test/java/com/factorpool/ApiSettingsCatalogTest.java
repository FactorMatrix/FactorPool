package com.factorpool;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class ApiSettingsCatalogTest {
  @TempDir Path temporary;

  @Test
  void publicCatalogReadsTheRuntimeResourceWithoutDatabaseOrParser() throws Exception {
    var json = new ObjectMapper();
    var db = mock(JdbcTemplate.class);
    var scripts = mock(ScriptResolver.class);
    var store = new Store(db, json, mock(PlatformTransactionManager.class), scripts);
    Path resource = temporary.resolve("settings_catalog.json");
    Files.writeString(resource, """
        {"schema_version":1,"official_schema_complete":false,"fields":[
          {"field":"delay","official_default":null,"project_default":1,
           "options":[{"value":0,"status":"missing_data"},{"value":1,"status":"implemented_pending_parity"}]}]}
        """);
    var api = MockMvcBuilders.standaloneSetup(new Api(store, mock(Tasks.class), resource)).build();
    api.perform(get("/api/simulation-settings"))
        .andExpect(status().isOk()).andExpect(jsonPath("$.schema_version").value(1))
        .andExpect(jsonPath("$.official_schema_complete").value(false))
        .andExpect(jsonPath("$.fields[0].official_default").value(org.hamcrest.Matchers.nullValue()))
        .andExpect(jsonPath("$.fields[0].project_default").value(1))
        .andExpect(jsonPath("$.fields[0].options[0].status").value("missing_data"));
    verifyNoInteractions(db, scripts);
  }
}
