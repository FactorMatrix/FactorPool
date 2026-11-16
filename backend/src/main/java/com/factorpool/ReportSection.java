package com.factorpool;

import java.util.Map;
import java.util.Arrays;
import java.util.stream.Collectors;

/** Fixed JSONB projections of an immutable completed report, without a Java JSON tree. */
enum ReportSection {
  CORE, IC, GROUPS, TRACE, SOURCE, EXPORT;

  static ReportSection named(String name) {
    return switch (name) {
      case "ic" -> IC;
      case "groups" -> GROUPS;
      case "trace" -> TRACE;
      case "source" -> SOURCE;
      case "export" -> EXPORT;
      default -> throw new IllegalArgumentException("Unsupported report section: " + name);
    };
  }

  String sql() {
    return Queries.SQL[ordinal()];
  }

  private static final class Queries {
    private static final String[] ALPHA = {
      "brain_style", "yearly_stats", "pnl", "nav", "drawdown_series", "returns",
      "turnover_series", "monthly", "yearly", "sharpe_series"
    };
    private static final String[] GROUP = {
      "annual_return", "cagr", "annual_volatility", "sharpe", "max_drawdown",
      "win_rate", "payoff_ratio", "n", "nav", "drawdown_series", "returns",
      "turnover_series", "monthly", "yearly"
    };

    // All expressions and field names below are source constants. Request input is only a bound UUID.
    private static String pick(String object, String... fields) {
      object = "(" + object + ")";
      if (fields.length == 0)
        return "CASE WHEN jsonb_typeof(" + object + ")='object' THEN '{}'::jsonb ELSE " + object + " END";
      String names = Arrays.stream(fields).map(x -> "'" + x + "'")
          .collect(Collectors.joining(","));
      return "CASE WHEN jsonb_typeof(" + object + ")='object' THEN "
          + "(SELECT coalesce(jsonb_object_agg(f.key," + object + "->f.key),'{}'::jsonb) "
          + "FROM unnest(ARRAY[" + names + "]::text[]) f(key) WHERE jsonb_exists("
          + object + ",f.key)) ELSE " + object + " END";
    }

    private static String project(String object, String[] fields, Map<String, String> nested) {
      object = "(" + object + ")";
      String expression = "(" + pick(object, fields) + ")";
      for (var field : nested.entrySet()) {
        expression += " || CASE WHEN jsonb_exists(" + object + ",'" + field.getKey()
            + "') THEN jsonb_build_object('" + field.getKey() + "'," + field.getValue()
            + ") ELSE '{}'::jsonb END";
      }
      return "CASE WHEN jsonb_typeof(" + object + ")='object' THEN (" + expression + ") ELSE " + object + " END";
    }

    private static String rows(String array, String row) {
      array = "(" + array + ")";
      return "CASE WHEN jsonb_typeof(" + array + ")='array' THEN "
          + "(SELECT coalesce(jsonb_agg(" + row + " ORDER BY e.ordinal),'[]'::jsonb) "
          + "FROM jsonb_array_elements(" + array + ") WITH ORDINALITY e(value,ordinal)) ELSE "
          + array + " END";
    }

    private static String alpha(String object) {
      return project(object, ALPHA, Map.of(
          "pnl", pick(object + "->'pnl'", "book_size", "unit", "cumulative"),
          "monthly", rows(object + "->'monthly'", pick("e.value", "period", "value")),
          "yearly", rows(object + "->'yearly'", pick("e.value", "period", "value")),
          "yearly_stats", rows(object + "->'yearly_stats'", pick("e.value", "year", "start",
              "end", "days", "sharpe", "turnover", "fitness", "returns", "drawdown",
              "margin", "long_count", "short_count"))));
    }

    private static String group(String object) {
      return project(object, GROUP, Map.of(
          "monthly", rows(object + "->'monthly'", pick("e.value", "period", "value")),
          "yearly", rows(object + "->'yearly'", pick("e.value", "period", "value"))));
    }

    private static String icCalendar(String object) {
      return project(object, new String[]{}, Map.of(
          "ic", rows(object + "->'ic'", pick("e.value", "period", "mean")),
          "rank_ic", rows(object + "->'rank_ic'", pick("e.value", "period", "mean"))));
    }

    private static String constraints(String object) {
      object = "(" + object + ")";
      // Capacity details are loaded with Trace; first paint needs feasibility only.
      return "CASE WHEN jsonb_typeof(" + object + ")='object' THEN (" + object + " || CASE WHEN jsonb_exists(" + object + ",'daily') THEN jsonb_build_object('daily',"
          + rows(object + "->'daily'", pick("e.value", "date", "full_gross_feasible",
              "target_gross", "unallocated_gross")) + ") ELSE '{}'::jsonb END) ELSE " + object + " END";
    }

    private static String horizons(String h1) {
      return "CASE WHEN jsonb_typeof(d->'horizons')='object' AND jsonb_exists(d->'horizons','1') "
          + "THEN jsonb_build_object('1'," + h1 + ") ELSE '{}'::jsonb END";
    }

    private static String diagnostic(String[] fields, String h1, String[] conventions) {
      return project("d", fields, Map.of("horizons", horizons(h1),
          "conventions", pick("d->'conventions'", conventions)));
    }

    private static String core() {
      String train = project("h#>'{partitions,train}'", new String[]{"dates"},
          Map.of("alpha", alpha("h#>'{partitions,train,alpha}'")));
      String test = project("h#>'{partitions,test}'", new String[]{"dates"},
          Map.of("alpha", pick("h#>'{partitions,test,alpha}'", "brain_style")));
      String h1 = project("h", new String[]{"horizon", "portfolio_dates"}, Map.of(
          "portfolios", project("h->'portfolios'", new String[]{},
              Map.of("alpha", alpha("h#>'{portfolios,alpha}'"))),
          "partitions", project("h->'partitions'", new String[]{}, Map.of("train", train, "test", test))));
      String diagnostics = project("d", new String[]{"version", "test_period"}, Map.of(
          "conventions", pick("d->'conventions'", "brain_settings", "missing_datasets"),
          "horizons", horizons(h1), "alpha_constraints", constraints("d->'alpha_constraints'")));
      return "jsonb_build_object('metadata'," + pick("m", "report_version", "brain_settings", "source_sha256",
          "snapshot_id", "synthetic", "value_count") + ",'diagnostics'," + diagnostics + ")";
    }

    private static String ic() {
      String[] moments = {"mean", "std", "ir", "positive_ratio", "skewness", "excess_kurtosis",
          "t_iid", "p_iid", "t_hac", "p_hac", "n"};
      String decayRow = project("e.value", new String[]{"entry_lag"}, Map.of(
          "ic", pick("e.value->'ic'", "mean"), "rank_ic", pick("e.value->'rank_ic'", "mean")));
      String h1 = project("h", new String[]{"horizon", "dates"}, Map.of(
          "ic", pick("h->'ic'", moments), "rank_ic", pick("h->'rank_ic'", moments),
          "series", pick("h->'series'", "ic", "rank_ic", "ic_rolling30", "rank_ic_rolling30", "ic_cumulative", "rank_ic_cumulative"),
          "monthly", icCalendar("h->'monthly'"), "yearly", icCalendar("h->'yearly'"),
          "decay", rows("h->'decay'", decayRow)));
      return "jsonb_build_object('metadata'," + pick("m", "report_version") + ",'summary',"
          + pick("s", "ic_mean", "rank_ic_mean", "icir", "coverage") + ",'diagnostics',"
          + diagnostic(new String[]{"version"}, h1, new String[]{"annual_days", "brain_settings"}) + ")";
    }

    private static String groups() {
      String portfolios = "CASE WHEN jsonb_typeof(h->'portfolios')='object' THEN "
          + "(SELECT coalesce(jsonb_object_agg(p.key," + group("p.value")
          + "),'{}'::jsonb) FROM jsonb_each(h->'portfolios') p) ELSE h->'portfolios' END";
      String h1 = project("h", new String[]{"horizon", "portfolio_dates"}, Map.of("portfolios", portfolios));
      return "jsonb_build_object('metadata'," + pick("m", "report_version") + ",'diagnostics',"
          + diagnostic(new String[]{"version"}, h1, new String[]{"annual_days", "brain_settings"}) + ")";
    }

    private static String trace() {
      return "jsonb_build_object('metadata'," + pick("m", "report_version") + ",'diagnostics',"
          + project("d", new String[]{"version", "alpha_constraints"}, Map.of(
              "horizons", horizons(pick("h", "horizon", "pnl_book", "alpha_trace", "trace_status", "alpha_constraints")))) + ")";
    }

    private static final String FROM = " FROM research_results "
          + "JOIN research_tasks ON research_tasks.id=research_results.task_id "
          + "WHERE task_id=? AND research_tasks.status='SUCCEEDED'";

    private static String query(String projection, boolean summary) {
      // Materialize only the required subtrees once; PostgreSQL must not repeatedly detoast the full result.
      return "WITH report AS MATERIALIZED (SELECT result->'metadata' AS m, result->'diagnostics' AS d"
          + (summary ? ",result->'summary' AS s" : "") + FROM
          + "), components AS MATERIALIZED (SELECT *,d#>'{horizons,1}' AS h FROM report) SELECT ("
          + projection + ")::text FROM components";
    }

    static final String[] SQL = {query(core(), false), query(ic(), true), query(groups(), false), query(trace(), false),
        "SELECT jsonb_build_object('metadata',result->'metadata')::text" + FROM,
        "SELECT result::text" + FROM};
  }
}
