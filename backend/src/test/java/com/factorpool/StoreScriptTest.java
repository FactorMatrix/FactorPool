package com.factorpool;

import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class StoreScriptTest {
  final JdbcTemplate db = mock(JdbcTemplate.class);
  final ObjectMapper json = new ObjectMapper();
  final ScriptResolver scripts = mock(ScriptResolver.class);
  final DataClient data=mock(DataClient.class);
  final Store store = new Store(db, json, mock(PlatformTransactionManager.class), scripts,data);
  static final String LATEST = "SELECT id FROM market_snapshots ORDER BY created_at DESC,id DESC LIMIT 1";
  static final String TASK_INSERT = "INSERT INTO research_tasks(id,batch_id,name,snapshot,status) VALUES (?,?,?,?::jsonb,'QUEUED')";

  ObjectNode request(String source) {
    return json.createObjectNode().put("name", "Script factor")
        .put("source_mode", "script").put("source_code", source);
  }

  ObjectNode normalized(String snapshot) {
    ObjectNode out = json.createObjectNode().put("source_mode", "script").put("factor_class", "MyFactor");
    out.putObject("factor_params").put("period", 20);
    ObjectNode data = out.putObject("data").put("provider", "market_matrix")
        .put("frequency", "1d").put("adjustment", "raw")
        .put("factor_start", "2024-01-29").put("factor_end", "2024-02-02");
    data.putArray("universe").add("A").add("B");
    if (snapshot != null) data.put("snapshot_id", snapshot);
    ObjectNode analysis = out.putObject("analysis").put("groups", 3).put("annual_days", 252)
        .put("decay_steps", 10).put("entry", "signal_close").put("exit", "next_close")
        .put("min_ic_pairs", 3).put("icir_ddof", 1).put("group_weight", "equal")
        .put("annualize_icir", false);
    analysis.putArray("horizons").add(1).add(5).add(20);
    ObjectNode config = out.putObject("source_config");
    config.putObject("base").put("start_date", "2024-01-29").put("end_date", "2024-02-02");
    ObjectNode originalData = config.putObject("data").put("provider", "market_matrix");
    originalData.set("universe", data.path("universe").deepCopy());
    if (snapshot != null) originalData.put("snapshot_id", snapshot);
    config.putObject("factor").put("period", 20);
    config.putObject("analysis").put("groups", 3);
    return out;
  }

  void version(UUID id, ObjectNode definition) {
    when(db.queryForList("SELECT id,version,snapshot::text,source_hash,created_at FROM factor_versions WHERE factor_id=? ORDER BY version DESC", id))
        .thenReturn(List.of(Map.of("snapshot", definition.toString(), "version", 1)));
  }

  ObjectNode savedDefinition(String source, String snapshot) {
    ObjectNode resolved = normalized(snapshot);
    when(scripts.resolve(source)).thenReturn(resolved);
    ObjectNode definition = request(source);
    for (String key : List.of("factor_class", "factor_params", "data", "analysis", "source_config"))
      definition.set(key, resolved.path(key).deepCopy());
    return definition;
  }

  @Test
  void scriptSaveParsesBeforeWritingAndKeepsExactSourceAndLiteralConfig() {
    String source = "# complete script\nconfig = {}\nclass MyFactor: pass\nMyFactor.run(config)\n";
    ObjectNode resolved = normalized(null);
    when(scripts.resolve(source)).thenReturn(resolved);
    when(db.queryForObject(eq("SELECT coalesce(max(version),0)+1 FROM factor_versions WHERE factor_id=?"), eq(Integer.class), any()))
        .thenReturn(1);
    store.factor(request(source));
    ArgumentCaptor<String> snapshot = ArgumentCaptor.forClass(String.class);
    verify(db).update(eq("INSERT INTO factor_versions(id,factor_id,version,snapshot,source_hash) VALUES (?,?,?,?::jsonb,?)"),
        any(), any(), eq(1), snapshot.capture(), eq(store.hash(source)));
    JsonNode saved = store.parse(snapshot.getValue());
    assertEquals(source, saved.path("source_code").asText());
    assertEquals(resolved.path("source_config"), saved.path("source_config"));
    assertEquals("MyFactor", saved.path("factor_class").asText());
    assertFalse(saved.has("effective_config"));
    verify(data,never()).snapshot("");
  }

  @Test
  void invalidScriptNeverCreatesVersionOrTask() {
    when(scripts.resolve("invalid source")).thenThrow(new IllegalArgumentException("line 4: config must be literal"));
    assertThrows(IllegalArgumentException.class, () -> store.factor(request("invalid source")));
    assertThrows(IllegalArgumentException.class, () -> store.enqueue(request("invalid source"), null));
    verifyNoInteractions(db);
  }

  @Test
  void conflictingExternalControlsAreRejectedRatherThanOverridingSource() {
    when(scripts.resolve("script")).thenReturn(normalized(null));
    ObjectNode input = request("script");
    input.putObject("factor_params").put("period", 999);
    var error = assertThrows(IllegalArgumentException.class, () -> store.enqueue(input, null));
    assertTrue(error.getMessage().contains("conflicts with source config"));
    verifyNoInteractions(db);
  }

  @Test
  void brainSettingsComeFromParserAndAreSavedAsEffectiveSettings() {
    ObjectNode resolved = normalized(null);
    resolved.putObject("brain_settings").put("delay", 1).put("decay", 4)
        .put("pasteurization", "ON");
    ((ObjectNode)resolved.path("source_config")).putObject("settings").put("decay", 4);
    when(scripts.resolve("script")).thenReturn(resolved);
    when(data.snapshot("")).thenReturn(json.createObjectNode().put("snapshot_id","snapshot-new"));
    store.enqueue(request("script"), null);
    ArgumentCaptor<String> capture = ArgumentCaptor.forClass(String.class);
    verify(db).update(eq(TASK_INSERT), any(), isNull(), eq("Script factor"), capture.capture());
    JsonNode saved = store.parse(capture.getValue());
    assertEquals(resolved.path("brain_settings"), saved.path("brain_settings"));
    assertEquals(resolved.path("brain_settings"), saved.path("effective_config").path("settings"));
    assertEquals(resolved.path("source_config").path("settings"), saved.path("source_config").path("settings"));
    assertFalse(saved.path("source_config").path("settings").has("pasteurization"));
  }

  @Test
  void externalBrainSettingsCannotOverrideSourceSettings() {
    ObjectNode resolved = normalized(null);
    resolved.putObject("brain_settings").put("decay", 4);
    when(scripts.resolve("script")).thenReturn(resolved);
    ObjectNode input = request("script");
    input.putObject("brain_settings").put("decay", 99);
    assertThrows(IllegalArgumentException.class, () -> store.enqueue(input, null));
    verifyNoInteractions(db);
  }

  @Test
  void taskPinsDataAndKeepsOriginalAndEffectiveConfigsSeparate() {
    ObjectNode resolved = normalized(null);
    when(scripts.resolve("script")).thenReturn(resolved);
    when(data.snapshot("")).thenReturn(json.createObjectNode().put("snapshot_id","snapshot-new"));
    ObjectNode input = request("script");
    store.enqueue(input, null);
    ArgumentCaptor<String> capture = ArgumentCaptor.forClass(String.class);
    verify(db).update(eq(TASK_INSERT), any(), isNull(), eq("Script factor"), capture.capture());
    JsonNode snapshot = store.parse(capture.getValue());
    assertEquals("snapshot-new", snapshot.path("data").path("snapshot_id").asText());
    assertEquals("snapshot-new", snapshot.path("effective_config").path("data").path("snapshot_id").asText());
    assertFalse(snapshot.path("source_config").path("data").has("snapshot_id"));
    assertEquals(resolved.path("source_config"), snapshot.path("source_config"));
    assertEquals(snapshot.path("factor_params"), snapshot.path("effective_config").path("factor"));
    assertEquals(snapshot.path("analysis"), snapshot.path("effective_config").path("analysis"));
    assertEquals("2024-01-29", snapshot.path("effective_config").path("base").path("start_date").asText());
    assertFalse(snapshot.path("effective_config").path("data").has("factor_start"));
    assertFalse(snapshot.path("effective_config").has("factor_class"));
    assertFalse(snapshot.path("effective_config").has("settings"));
    assertFalse(input.has("data"));
    assertFalse(resolved.path("data").has("snapshot_id"));
  }

  @Test
  void scriptBatchUsesIndividualConfigsAndOneDefaultSnapshot() {
    UUID first = UUID.randomUUID(), second = UUID.randomUUID(), explicit = UUID.randomUUID();
    ObjectNode one = savedDefinition("script-one", null);
    ObjectNode two = savedDefinition("script-two", null);
    ObjectNode old = savedDefinition("script-old", "snapshot-old");
    version(first, one); version(second, two); version(explicit, old);
    when(data.snapshot("")).thenReturn(json.createObjectNode().put("snapshot_id","snapshot-new"));
    when(data.snapshot("snapshot-old")).thenReturn(json.createObjectNode().put("snapshot_id","snapshot-old"));
    ObjectNode input = json.createObjectNode();
    input.putArray("factors").add(first.toString()).add(second.toString()).add(explicit.toString());
    var result = store.batch(input);
    assertEquals(3, ((List<?>)result.get("tasks")).size());
    verify(data,times(1)).snapshot("");
    ArgumentCaptor<String> capture = ArgumentCaptor.forClass(String.class);
    verify(db, times(3)).update(eq(TASK_INSERT), any(), eq(result.get("id")), eq("Script factor"), capture.capture());
    List<JsonNode> saved = capture.getAllValues().stream().map(store::parse).toList();
    assertEquals(List.of("snapshot-new", "snapshot-new", "snapshot-old"),
        saved.stream().map(value -> value.path("data").path("snapshot_id").asText()).toList());
    assertEquals(List.of("script-one", "script-two", "script-old"),
        saved.stream().map(value -> value.path("source_code").asText()).toList());
  }

  @Test
  void scriptBatchRejectsSharedOverridesAndMixedModesBeforeWrites() {
    UUID script = UUID.randomUUID(), legacy = UUID.randomUUID();
    version(script, savedDefinition("script", null));
    ObjectNode legacyDefinition = json.createObjectNode().put("name", "Legacy")
        .put("source_code", "class OldFactor: pass").put("factor_class", "OldFactor");
    version(legacy, legacyDefinition);
    ObjectNode override = json.createObjectNode();
    override.putArray("factors").add(script.toString());
    override.putObject("analysis").put("groups", 99);
    assertThrows(IllegalArgumentException.class, () -> store.batch(override));
    ObjectNode mixed = json.createObjectNode();
    mixed.putArray("factors").add(script.toString()).add(legacy.toString());
    assertThrows(IllegalArgumentException.class, () -> store.batch(mixed));
    verify(db, never()).update(anyString(), any(Object[].class));
    verify(data,never()).snapshot("");
  }

  @Test
  void legacyBatchRetainsSharedDataAndAnalysis() {
    UUID id = UUID.randomUUID();
    ObjectNode legacy = json.createObjectNode().put("name", "Legacy")
        .put("source_code", "class OldFactor: pass").put("factor_class", "OldFactor");
    version(id, legacy);
    ObjectNode input = json.createObjectNode();
    input.putArray("factors").add(id.toString());
    input.putObject("data").put("provider", "fixture").put("universe", "fixture")
        .put("factor_start", "2024-01-29").put("factor_end", "2024-01-29");
    input.putObject("analysis").put("groups", 5);
    var result = store.batch(input);
    ArgumentCaptor<String> capture = ArgumentCaptor.forClass(String.class);
    verify(db).update(eq(TASK_INSERT), any(), eq(result.get("id")), eq("Legacy"), capture.capture());
    JsonNode saved = store.parse(capture.getValue());
    ObjectNode expectedData = input.path("data").deepCopy();
    expectedData.put("factor_price", "quote_adjusted");
    assertEquals(expectedData, saved.path("data"));
    assertFalse(input.path("data").has("factor_price"));
    assertEquals(input.path("analysis"), saved.path("analysis"));
    assertFalse(saved.has("effective_config"));
    verify(scripts).validateFrontend("class OldFactor: pass");
  }
}
