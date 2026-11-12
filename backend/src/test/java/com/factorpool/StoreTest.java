package com.factorpool;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class StoreTest {
  final JdbcTemplate db = mock(JdbcTemplate.class);
  final ObjectMapper json = new ObjectMapper();
  final Store store = new Store(db, json, mock(PlatformTransactionManager.class), mock(ScriptResolver.class));

  ObjectNode reportingRequest() {
    ObjectNode out = json.createObjectNode();
    out.putObject("data").put("provider", "fixture").put("factor_start", "2024-01-02").put("factor_end", "2024-01-04");
    out.putObject("analysis").put("book_size", 20000000).put("pnl_unit", "USD")
        .putArray("trace_dates").add("2024-01-02");
    return out;
  }

  @Test
  void reportingScaleAndBoundedSignalTraceAreAcceptedWithoutAnyDatabaseAccess() {
    assertDoesNotThrow(() -> Store.validate(reportingRequest()));
    verifyNoInteractions(db);
  }

  @Test
  void reportingOptionsRejectInvalidAmountsUnitsAndOutOfWindowOrDuplicateSignals() {
    for (String bad : List.of("{\"book_size\":0}", "{\"book_size\":true}",
        "{\"pnl_unit\":\"usd\"}", "{\"min_ic_pairs\":4294967299}", "{\"trace_dates\":[\"2024-01-05\"]}",
        "{\"trace_dates\":[\"2024-01-02\",\"2024-01-02\"]}")) {
      ObjectNode request = reportingRequest();
      try { request.set("analysis", json.readTree(bad)); }
      catch (Exception e) { throw new AssertionError(e); }
      assertThrows(IllegalArgumentException.class, () -> Store.validate(request));
    }
    verifyNoInteractions(db);
  }

  @Test
  void latestSnapshotIsPinnedWithoutMutatingCaller() {
    when(db.queryForList("SELECT id FROM market_snapshots ORDER BY created_at DESC,id DESC LIMIT 1"))
        .thenReturn(List.of(Map.of("id", "snapshot-a")));
    ObjectNode original = json.createObjectNode().put("provider", "market_matrix");
    var pinned = store.pinMarketData(original);
    assertEquals("snapshot-a", pinned.path("snapshot_id").asText());
    assertFalse(original.has("snapshot_id"));
    assertNotSame(original, pinned);
  }

  @Test
  void explicitSnapshotRemainsPinned() {
    when(db.queryForList("SELECT id FROM market_snapshots WHERE id=?", "snapshot-old"))
        .thenReturn(List.of(Map.of("id", "snapshot-old")));
    var data = json.createObjectNode().put("provider", "market_matrix")
        .put("snapshot_id", "snapshot-old");
    assertEquals("snapshot-old", store.pinMarketData(data).path("snapshot_id").asText());
    verify(db, never()).queryForList("SELECT id FROM market_snapshots ORDER BY created_at DESC,id DESC LIMIT 1");
  }

  @Test
  void missingSnapshotFailsWithoutImportingAnything() {
    when(db.queryForList("SELECT id FROM market_snapshots ORDER BY created_at DESC,id DESC LIMIT 1"))
        .thenReturn(List.of());
    var error = assertThrows(IllegalArgumentException.class,
        () -> store.pinMarketData(json.createObjectNode().put("provider", "market_matrix")));
    assertTrue(error.getMessage().contains("never import or refresh"));
    verify(db).queryForList("SELECT id FROM market_snapshots ORDER BY created_at DESC,id DESC LIMIT 1");
    verifyNoMoreInteractions(db);
  }

  @Test
  void fixtureKeepsItsOriginalDataAndNeverLooksUpMarketSnapshots() {
    var data = json.createObjectNode().put("provider", "fixture").put("universe", "fixture");
    assertEquals(data, store.pinMarketData(data));
    verifyNoInteractions(db);
  }

  @Test
  void sourceExposesTheSnapshotAxes() {
    when(db.queryForList("SELECT id,manifest::text,dates::text,symbols::text,fields::text,created_at FROM market_snapshots ORDER BY created_at DESC,id DESC LIMIT 1"))
        .thenReturn(List.of(Map.of("id", "snapshot-a", "dates", "[\"2024-01-02\",\"2024-01-03\"]",
            "symbols", "[\"A\",\"B\"]", "fields", "[\"close\",\"return\"]", "manifest", "{\"format\":\"factorpool-market-matrix-v1\",\"return_formula\":\"close/prev_close-1\"}", "created_at", "2026-11-12")));
    var source = store.marketSource();
    assertTrue(source.path("available").asBoolean());
    assertEquals("market_matrix", source.path("id").asText());
    assertEquals("2024-01-02", source.path("snapshot").path("date_start").asText());
    assertEquals("2024-01-03", source.path("snapshot").path("date_end").asText());
    assertEquals(2, source.path("snapshot").path("symbols").size());
    assertEquals("close/prev_close-1", source.path("snapshot").path("manifest").path("return_formula").asText());
  }
}
