package com.factorpool;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.sql.Connection;
import java.sql.DriverManager;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import static org.junit.jupiter.api.Assertions.*;

/** Runs against an explicitly supplied disposable PostgreSQL; all fixtures are session-local TEMP tables. */
@EnabledIfEnvironmentVariable(named = "FACTORPOOL_TEST_POSTGRES_URL", matches = ".+")
class ReportSectionPostgresTest {
  final ObjectMapper json = new ObjectMapper();
  final UUID id = UUID.randomUUID();
  Connection db;
  JsonNode original;

  @BeforeEach
  void fixture() throws Exception {
    db = DriverManager.getConnection(System.getenv("FACTORPOOL_TEST_POSTGRES_URL"));
    try (var statement = db.createStatement()) {
      statement.execute("CREATE TEMP TABLE research_tasks(id uuid primary key,status text)");
      statement.execute("CREATE TEMP TABLE research_results(task_id uuid primary key,result jsonb)");
    }
    original = json.readTree("""
        {"metadata":{"report_version":3,"source_sha256":"source","snapshot_id":"snapshot",
        "synthetic":false,"value_count":0,"brain_settings":{"visualization":true},"source_config":{"code":"full source"}},
        "summary":{"ic_mean":0,"rank_ic_mean":null,"icir":0,"coverage":1,"unused":99},
        "daily":[{"date":"2020-01-01","unused":99}],
        "diagnostics":{"version":3,"test_period":{"start":"2020-01-02"},
        "conventions":{"brain_settings":{"visualization":true},"annual_days":252,"missing_datasets":[],"unused":99},
        "alpha_constraints":{"daily":[{"date":"2020-01-01","full_gross_feasible":false,"target_gross":1,"unallocated_gross":0,"capacity":0.5}]},
        "horizons":{"1":{"horizon":1,"dates":["2020-01-01"],"portfolio_dates":["2020-01-01"],
        "ic":{"mean":0},"rank_ic":null,"series":{"ic":[0]},"monthly":{},"yearly":{},"decay":[],
        "portfolios":{"alpha":{"brain_style":{"sharpe":0,"margin":null},"yearly_stats":[{"year":2020,"returns":0,"pnl":99}],
        "pnl":{"book_size":1,"unit":"BOOK","cumulative":[0],"daily":[99]},"nav":[1],"returns":[0],"sharpe_series":null,"positions":[99]},
        "G1":{"annual_return":0,"nav":[1],"positions":[99]}},
        "partitions":{"all":{"unused":99},"train":{"dates":["2020-01-01"],"alpha":{"brain_style":{"sharpe":0},"nav":[1],"positions":[99]}},
        "test":{"dates":["2020-01-02"],"alpha":{"brain_style":{"sharpe":null},"nav":[1]}}},
        "pnl_book":{"size":1},"alpha_trace":{"rows":[{"date":"2020-01-01","weight":0}]},"trace_status":{"available":true}},
        "5":{"horizon":5,"portfolios":{"unused":99}}}}}
        """);
    ((ObjectNode)original.path("metadata")).put("omitted_payload", "x".repeat(50000));
    try (var statement = db.prepareStatement("INSERT INTO research_tasks VALUES (?, 'SUCCEEDED')")) {
      statement.setObject(1,id);
      statement.executeUpdate();
    }
    replace(original);
  }

  void replace(JsonNode report) throws Exception {
    try (var statement = db.prepareStatement("INSERT INTO research_results VALUES (?,?::jsonb) ON CONFLICT(task_id) DO UPDATE SET result=excluded.result")) {
      statement.setObject(1,id);
      statement.setString(2,report.toString());
      statement.executeUpdate();
    }
  }

  String raw(ReportSection section) throws Exception {
    try (var statement = db.prepareStatement(section.sql())) {
      statement.setObject(1,id);
      try (var result = statement.executeQuery()) {
        return result.next() ? result.getString(1) : null;
      }
    }
  }

  JsonNode read(ReportSection section) throws Exception {
    return json.readTree(raw(section));
  }

  @AfterEach
  void close() throws Exception {
    if (db != null) db.close();
  }

  @Test
  void coreContainsOnlyVisibleAlphaAndActualPartitionData() throws Exception {
    String body = raw(ReportSection.CORE);
    JsonNode core = json.readTree(body);
    JsonNode h1 = core.at("/diagnostics/horizons/1");
    assertTrue(body.length() < original.toString().length() / 10);
    assertFalse(core.has("daily"));
    assertFalse(core.has("summary"));
    assertFalse(core.path("metadata").path("synthetic").asBoolean(true));
    assertEquals(0, core.path("metadata").path("value_count").asInt(-1));
    assertEquals(1, h1.path("portfolios").size());
    assertFalse(h1.path("portfolios").path("alpha").has("positions"));
    assertFalse(h1.at("/portfolios/alpha/pnl").has("daily"));
    assertFalse(h1.at("/portfolios/alpha/yearly_stats/0").has("pnl"));
    assertTrue(h1.at("/portfolios/alpha/sharpe_series").isNull());
    assertFalse(h1.path("partitions").has("all"));
    assertEquals(1,h1.at("/partitions/train/alpha/nav/0").asInt());
    assertEquals(1,h1.at("/partitions/test/alpha").size());
    assertTrue(h1.at("/partitions/test/alpha/brain_style/sharpe").isNull());
    JsonNode daily = core.at("/diagnostics/alpha_constraints/daily/0");
    assertEquals(4,daily.size());
    assertFalse(daily.path("full_gross_feasible").asBoolean(true));
    assertEquals(0,daily.path("unallocated_gross").asInt(-1));
  }

  @Test
  void sectionsRemainIndependentAndExportRemainsComplete() throws Exception {
    JsonNode ic = read(ReportSection.IC);
    assertTrue(ic.at("/diagnostics/horizons/1/rank_ic").isNull());
    assertFalse(ic.at("/diagnostics/horizons/1").has("portfolios"));
    assertEquals(0,ic.at("/summary/ic_mean").asInt(-1));
    assertFalse(ic.path("summary").has("unused"));
    JsonNode groups = read(ReportSection.GROUPS);
    assertEquals(2,groups.at("/diagnostics/horizons/1/portfolios").size());
    assertFalse(groups.at("/diagnostics/horizons/1/portfolios/G1").has("positions"));
    assertFalse(groups.at("/diagnostics/horizons/1").has("ic"));
    JsonNode trace = read(ReportSection.TRACE);
    assertEquals(0.5,trace.at("/diagnostics/alpha_constraints/daily/0/capacity").asDouble());
    assertTrue(trace.at("/diagnostics/horizons/1/trace_status/available").asBoolean());
    assertEquals(original.path("metadata"),read(ReportSection.SOURCE).path("metadata"));
    assertEquals(original,read(ReportSection.EXPORT));
    for (ReportSection section : ReportSection.values())
      assertEquals(3,read(section).at("/metadata/report_version").asInt());
  }

  @Test
  void absentOneDayAndExplicitNullStayUnavailableWithoutFallback() throws Exception {
    ((ObjectNode)original.at("/diagnostics/horizons")).remove("1");
    ((ObjectNode)original.path("diagnostics")).putNull("alpha_constraints");
    replace(original);
    for (ReportSection section : new ReportSection[]{ReportSection.CORE,ReportSection.IC,ReportSection.GROUPS,ReportSection.TRACE})
      assertEquals(0,read(section).at("/diagnostics/horizons").size());
    assertTrue(read(ReportSection.CORE).at("/diagnostics/alpha_constraints").isNull());
  }

  @Test
  void sqlNeverExposesAResultFromAnUnsuccessfulTask() throws Exception {
    try (var statement = db.createStatement()) {
      statement.executeUpdate("UPDATE research_tasks SET status='FAILED'");
    }
    for (ReportSection section : ReportSection.values()) assertNull(raw(section));
  }
}
