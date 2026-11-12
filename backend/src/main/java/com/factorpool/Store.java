package com.factorpool;
import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

@Component
public class Store {
  final JdbcTemplate db;
  final ObjectMapper json;
  final TransactionTemplate tx;
  final ScriptResolver scripts;
  Store(JdbcTemplate db, ObjectMapper json,
        PlatformTransactionManager manager, ScriptResolver scripts) {
    this.db = db;
    this.json = json;
    this.tx = new TransactionTemplate(manager);
    this.scripts = scripts;
  }
  JsonNode parse(String source) {
    try {
      return json.readTree(source);
    } catch (Exception e) {
      throw new IllegalArgumentException("Invalid JSON", e);
    }
  }
  String hash(String source) {
    try {
      return HexFormat.of().formatHex(
          MessageDigest.getInstance("SHA-256").digest(
              source.getBytes(StandardCharsets.UTF_8)));
    } catch (Exception e) {
      throw new IllegalStateException(e);
    }
  }
  ObjectNode factor(JsonNode request) {
    ObjectNode definition = validateDefinition(request);
    return tx.execute(status -> {
      UUID id = definition.hasNonNull("id")
                    ? UUID.fromString(definition.get("id").asText())
                    : UUID.randomUUID();
      db.update("INSERT INTO factors(id,name,description) VALUES (?,?,?) ON "
                    + "CONFLICT (id) DO UPDATE SET "
                    + "name=excluded.name,description=excluded.description",
                id, definition.path("name").asText(),
                definition.path("description").asText(""));
      db.queryForObject("SELECT id FROM factors WHERE id=? FOR UPDATE",
                        UUID.class, id);
      int version =
          db.queryForObject("SELECT coalesce(max(version),0)+1 "
                                + "FROM factor_versions WHERE factor_id=?",
                            Integer.class, id);
      UUID vid = UUID.randomUUID();
      db.update(
          "INSERT INTO "
              + "factor_versions(id,factor_id,version,snapshot,source_hash) "
              + "VALUES (?,?,?,?::jsonb,?)",
          vid, id, version, definition.toString(),
          hash(definition.path("source_code").asText()));
      ObjectNode result = json.createObjectNode();
      result.put("id", id.toString());
      result.put("version_id", vid.toString());
      result.put("version", version);
      return result;
    });
  }
  List<Map<String, Object>> factors() {
    return db.queryForList(
        "SELECT f.*,v.id AS version_id,v.version,v.source_hash,v.created_at "
        + "AS version_at FROM factors f JOIN LATERAL (SELECT * FROM "
        + "factor_versions WHERE factor_id=f.id ORDER BY version DESC LIMIT 1) "
        + "v ON true ORDER BY f.created_at DESC");
  }
  ObjectNode factor(UUID id) {
    var versions = db.queryForList(
        "SELECT id,version,snapshot::text,source_hash,created_at FROM "
            + "factor_versions WHERE factor_id=? ORDER BY version DESC",
        id);
    if (versions.isEmpty())
      throw new NoSuchElementException("Factor not found");
    ObjectNode out = json.createObjectNode();
    out.put("id", id.toString());
    out.set("versions", json.valueToTree(versions));
    out.set("latest", parse((String)versions.get(0).get("snapshot")));
    return out;
  }
  UUID enqueue(JsonNode request, UUID batch) {
    ObjectNode normalized = validateDefinition(request);
    validate(normalized);
    return tx.execute(status -> insertTask(normalized, batch, null));
  }
  UUID insertTask(ObjectNode normalized, UUID batch, String defaultSnapshot) {
    ObjectNode pinned = normalized.deepCopy();
    pinned.set("data", pinMarketData(normalized.path("data"), defaultSnapshot));
    if (isScript(pinned)) {
      ObjectNode effective = pinned.path("source_config").deepCopy();
      var data = pinned.path("data");
      ObjectNode base = json.createObjectNode();
      base.set("start_date", data.path("factor_start").deepCopy());
      base.set("end_date", data.path("factor_end").deepCopy());
      base.set("frequency", data.path("frequency").deepCopy());
      effective.set("base", base);
      ObjectNode effectiveData = json.createObjectNode();
      for (String key : List.of("provider", "universe", "adjustment", "snapshot_id"))
        if (data.has(key))
          effectiveData.set(key, data.get(key).deepCopy());
      effective.set("data", effectiveData);
      effective.set("factor", pinned.path("factor_params").deepCopy());
      effective.set("analysis", pinned.path("analysis").deepCopy());
      if (pinned.has("brain_settings"))
        effective.set("settings", pinned.path("brain_settings").deepCopy());
      pinned.set("effective_config", effective);
    }
    UUID id = UUID.randomUUID();
    db.update("INSERT INTO research_tasks(id,batch_id,name,snapshot,status) "
                  + "VALUES (?,?,?,?::jsonb,'QUEUED')",
              id, batch, pinned.path("name").asText(), pinned.toString());
    return id;
  }
  ObjectNode pinMarketData(JsonNode data) {
    return pinMarketData(data, null);
  }
  ObjectNode pinMarketData(JsonNode data, String defaultSnapshot) {
    if (!data.isObject())
      throw new IllegalArgumentException("data is required");
    ObjectNode pinned = data.deepCopy();
    if (!data.path("provider").asText().equals("market_matrix"))
      return pinned;
    String requested = data.path("snapshot_id").asText("");
    if (data.has("snapshot_id") &&
        (!data.get("snapshot_id").isTextual() || requested.isBlank()))
      throw new IllegalArgumentException("snapshot_id must be a nonempty string");
    if (requested.isBlank() && defaultSnapshot != null) {
      pinned.put("snapshot_id", defaultSnapshot);
      return pinned;
    }
    var snapshots = requested.isBlank()
        ? db.queryForList("SELECT id FROM market_snapshots ORDER BY created_at DESC,id DESC LIMIT 1")
        : db.queryForList("SELECT id FROM market_snapshots WHERE id=?", requested);
    if (snapshots.isEmpty())
      throw new IllegalArgumentException(requested.isBlank()
          ? "No market matrix snapshot is available. Import a snapshot explicitly first; tasks never import or refresh market data."
          : "Market matrix snapshot does not exist: " + requested);
    pinned.put("snapshot_id", snapshots.get(0).get("id").toString());
    return pinned;
  }

  Map<String, Object> batch(JsonNode request) {
    if (!request.path("factors").isArray() || request.path("factors").isEmpty() ||
        request.path("factors").size() > 100)
      throw new IllegalArgumentException("Select 1..100 factors");
    return tx.execute(status -> {
      List<ObjectNode> definitions = new ArrayList<>();
      for (JsonNode fid : request.path("factors"))
        definitions.add(validateDefinition(factor(UUID.fromString(fid.asText())).path("latest")));
      boolean script = isScript(definitions.get(0));
      if (definitions.stream().anyMatch(value -> isScript(value) != script))
        throw new IllegalArgumentException("Script and legacy factors cannot be mixed in one batch");
      ObjectNode config = request.deepCopy();
      String defaultSnapshot = null;
      if (script) {
        if (request.has("data") || request.has("analysis"))
          throw new IllegalArgumentException("Script batches use each source config; omit shared data and analysis");
        for (ObjectNode definition : definitions)
          validate(definition);
        if (definitions.stream().anyMatch(value -> value.path("data").path("provider").asText().equals("market_matrix")
                                                  && !value.path("data").has("snapshot_id"))) {
          defaultSnapshot = pinMarketData(json.createObjectNode().put("provider", "market_matrix"))
                                .path("snapshot_id").asText();
          config.put("default_snapshot_id", defaultSnapshot);
        }
        config.put("source_mode", "script");
      } else {
        ObjectNode shared = pinMarketData(request.path("data"));
        config.set("data", shared);
        for (ObjectNode definition : definitions) {
          definition.set("data", shared.deepCopy());
          definition.set("analysis", request.path("analysis").deepCopy());
          validate(definition);
        }
      }
      UUID id = UUID.randomUUID();
      db.update("INSERT INTO research_batches(id,config) VALUES (?,?::jsonb)", id, config.toString());
      List<UUID> tasks = new ArrayList<>();
      for (ObjectNode definition : definitions)
        tasks.add(insertTask(definition, id, defaultSnapshot));
      return Map.of("id", id, "tasks", tasks);
    });
  }
  ObjectNode marketSource() {
    ObjectNode source = json.createObjectNode();
    source.put("id", "market_matrix");
    source.put("name", "PostgreSQL 行情矩阵 · 只读快照");
    source.put("default", true);
    source.put("synthetic", false);
    source.set("frequency", json.valueToTree(List.of("1d")));
    source.set("adjustment", json.valueToTree(List.of("raw")));
    var snapshots = db.queryForList(
        "SELECT id,manifest::text,dates::text,symbols::text,fields::text,created_at FROM market_snapshots ORDER BY created_at DESC,id DESC LIMIT 1");
    source.put("available", !snapshots.isEmpty());
    if (snapshots.isEmpty()) {
      source.put("message", "暂无行情矩阵快照，请先显式导入；任务不会自动导入或刷新行情");
      return source;
    }
    var row = snapshots.get(0);
    var dates = parse((String)row.get("dates"));
    var symbols = parse((String)row.get("symbols"));
    ObjectNode snapshot = json.createObjectNode();
    snapshot.put("id", row.get("id").toString());
    snapshot.put("date_count", dates.size());
    snapshot.put("symbol_count", symbols.size());
    if (!dates.isEmpty()) {
      snapshot.set("date_start", dates.get(0));
      snapshot.set("date_end", dates.get(dates.size()-1));
    }
    snapshot.set("symbols", symbols);
    snapshot.set("fields", parse((String)row.get("fields")));
    snapshot.set("manifest", parse((String)row.get("manifest")));
    snapshot.put("created_at", row.get("created_at").toString());
    source.set("snapshot", snapshot);
    return source;
  }
  static boolean isScript(JsonNode r) {
    return r.path("source_mode").asText().equals("script");
  }
  ObjectNode validateDefinition(JsonNode r) {
    if (!r.isObject() || r.path("name").asText().isBlank() ||
        r.path("source_code").asText().isBlank() ||
        r.path("source_code").asText().length() > 131072)
      throw new IllegalArgumentException(
          "A name and source_code (max 128 KiB) are required");
    if (r.has("source_mode") &&
        (!r.path("source_mode").isTextual() || !Set.of("script", "class").contains(r.path("source_mode").asText())))
      throw new IllegalArgumentException("source_mode must be script or class");
    ObjectNode normalized = r.deepCopy();
    normalized.remove("effective_config");
    if (isScript(r)) {
      ObjectNode parsed = scripts.resolve(r.path("source_code").asText());
      for (String key : List.of("factor_class", "factor_params", "data", "analysis", "source_config", "brain_settings")) {
        if (r.has(key) && !r.get(key).equals(parsed.get(key)))
          throw new IllegalArgumentException("Script " + key + " conflicts with source config; edit the source config instead");
        if (parsed.has(key))
          normalized.set(key, parsed.get(key).deepCopy());
        else
          normalized.remove(key);
      }
      normalized.put("source_mode", "script");
      validate(normalized);
    }
    if (!normalized.path("factor_class").asText().matches("[A-Za-z_][A-Za-z0-9_]*"))
      throw new IllegalArgumentException("Invalid factor_class");
    return normalized;
  }
  static void validate(JsonNode r) {
    var d = r.path("data");
    var a = r.path("analysis");
    if (!d.isObject() || !a.isObject())
      throw new IllegalArgumentException("data and analysis are required");
    var start = java.time.LocalDate.parse(d.path("factor_start").asText());
    var end = java.time.LocalDate.parse(d.path("factor_end").asText());
    if (end.isBefore(start))
      throw new IllegalArgumentException("End precedes start");
    if (d.path("provider").asText().equals("ft_daily_mmap"))
      throw new IllegalArgumentException(
          "ft_daily_mmap has migrated to market_matrix; new tasks must read a PostgreSQL market snapshot");
    if (!Set.of("fixture", "market_matrix")
             .contains(d.path("provider").asText()))
      throw new IllegalArgumentException("Unknown provider");
    if (!d.path("frequency").asText("1d").equals("1d") ||
        !d.path("adjustment").asText("raw").equals("raw"))
      throw new IllegalArgumentException("Only daily/raw input is supported");
    if (!a.path("entry").asText("signal_close").equals("signal_close") ||
        !a.path("exit").asText("next_close").equals("next_close"))
      throw new IllegalArgumentException(
          "Only signal_close / next_close is supported, using close / prev_close - 1; legacy open-based requests are unsupported");
    for (var it = a.fieldNames(); it.hasNext();)
      if (!Set.of("entry", "exit", "groups", "group_weight", "min_ic_pairs",
                  "icir_ddof", "annualize_icir", "horizons", "annual_days",
                  "decay_steps", "book_size", "pnl_unit", "trace_dates")
               .contains(it.next()))
        throw new IllegalArgumentException("Unknown analysis option");
    if (a.has("book_size") && (!a.get("book_size").isNumber() ||
        !Double.isFinite(a.get("book_size").asDouble()) || a.get("book_size").asDouble() <= 0 ||
        a.get("book_size").asDouble() > 1e15))
      throw new IllegalArgumentException("book_size must be finite and in (0, 1e15]");
    if (a.has("pnl_unit") && (!a.get("pnl_unit").isTextual() ||
        !a.get("pnl_unit").asText().matches("[A-Z][A-Z0-9_]{0,11}")))
      throw new IllegalArgumentException("pnl_unit must be a 1..12 character uppercase unit label");
    if (a.has("trace_dates")) {
      var values = a.get("trace_dates");
      if (!values.isArray() || values.size() > 5)
        throw new IllegalArgumentException("trace_dates must contain at most five signal dates");
      Set<String> unique = new HashSet<>();
      for (JsonNode value : values) {
        if (!value.isTextual())
          throw new IllegalArgumentException("trace_dates must contain ISO signal dates");
        var day = java.time.LocalDate.parse(value.asText());
        if (day.isBefore(start) || day.isAfter(end) || !unique.add(day.toString()))
          throw new IllegalArgumentException("trace_dates must be unique and within the research dates");
      }
    }
    if (a.has("horizons")) {
      if (!a.get("horizons").isArray() || a.get("horizons").isEmpty() ||
          a.get("horizons").size() > 6)
        throw new IllegalArgumentException(
            "horizons must contain 1..6 periods");
      Set<Integer> unique = new HashSet<>();
      for (JsonNode h : a.get("horizons"))
        if (!h.isIntegralNumber() || h.asLong() < 1 || h.asLong() > 252 ||
            !unique.add(h.asInt()))
          throw new IllegalArgumentException(
              "horizons must be unique integers in 1..252");
    }
    if (a.has("annual_days") && (!a.get("annual_days").isIntegralNumber() ||
                                 a.get("annual_days").asLong() < 1 ||
                                 a.get("annual_days").asLong() > 366))
      throw new IllegalArgumentException(
          "annual_days must be an integer in 1..366");
    if (a.has("decay_steps") && (!a.get("decay_steps").isIntegralNumber() ||
                                 a.get("decay_steps").asLong() < 1 ||
                                 a.get("decay_steps").asLong() > 10))
      throw new IllegalArgumentException(
          "decay_steps must be an integer in 1..10");
    for (String key : List.of("groups", "min_ic_pairs", "icir_ddof"))
      if (a.has(key) && (!a.get(key).isIntegralNumber() || !a.get(key).canConvertToInt()))
        throw new IllegalArgumentException(key + " must be a signed 32-bit integer");
    int groups = a.path("groups").asInt(3),
        pairs = a.path("min_ic_pairs").asInt(3),
        ddof = a.path("icir_ddof").asInt(1);
    if (groups < 1 || groups > 100 || pairs < 2 || ddof < 0 || ddof > 1)
      throw new IllegalArgumentException("Invalid analysis limits");
  }
  Map<String, Object> task(UUID id, boolean snapshot) {
    String columns =
        "id,batch_id,name,status,error,created_at,started_at,finished_at" +
        (snapshot ? ",snapshot::text" : "");
    var rows = db.queryForList(
        "SELECT " + columns + " FROM research_tasks WHERE id=?", id);
    if (rows.isEmpty())
      throw new NoSuchElementException("Task not found");
    var out = rows.get(0);
    if (snapshot)
      out.put("snapshot", parse((String)out.get("snapshot")));
    return out;
  }
}
