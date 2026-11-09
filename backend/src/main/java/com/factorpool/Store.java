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
  Store(JdbcTemplate db, ObjectMapper json,
        PlatformTransactionManager manager) {
    this.db = db;
    this.json = json;
    this.tx = new TransactionTemplate(manager);
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
    if (request.path("name").asText().isBlank() ||
        request.path("source_code").asText().isBlank())
      throw new IllegalArgumentException("name and source_code are required");
    return tx.execute(status -> {
      UUID id = request.hasNonNull("id")
                    ? UUID.fromString(request.get("id").asText())
                    : UUID.randomUUID();
      db.update("INSERT INTO factors(id,name,description) VALUES (?,?,?) ON " +
                "CONFLICT (id) DO UPDATE SET " +
                "name=excluded.name,description=excluded.description",
                id, request.path("name").asText(),
                request.path("description").asText(""));
      db.queryForObject("SELECT id FROM factors WHERE id=? FOR UPDATE",
                        UUID.class, id);
      int version = db.queryForObject("SELECT coalesce(max(version),0)+1 " +
                                      "FROM factor_versions WHERE factor_id=?",
                                      Integer.class, id);
      UUID vid = UUID.randomUUID();
      db.update("INSERT INTO " +
                "factor_versions(id,factor_id,version,snapshot,source_hash) " +
                "VALUES (?,?,?,?::jsonb,?)",
                vid, id, version, request.toString(),
                hash(request.path("source_code").asText()));
      ObjectNode result = json.createObjectNode();
      result.put("id", id.toString());
      result.put("version_id", vid.toString());
      result.put("version", version);
      return result;
    });
  }
  List<Map<String, Object>> factors() {
    return db.queryForList(
        "SELECT f.*,v.id AS version_id,v.version,v.source_hash,v.created_at " +
        "AS version_at FROM factors f JOIN LATERAL (SELECT * FROM " +
        "factor_versions WHERE factor_id=f.id ORDER BY version DESC LIMIT 1) " +
        "v ON true ORDER BY f.created_at DESC");
  }
  ObjectNode factor(UUID id) {
    var versions = db.queryForList(
        "SELECT id,version,snapshot::text,source_hash,created_at FROM " +
        "factor_versions WHERE factor_id=? ORDER BY version DESC",
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
    validate(request);
    UUID id = UUID.randomUUID();
    db.update("INSERT INTO research_tasks(id,batch_id,name,snapshot,status) " +
              "VALUES (?,?,?,?::jsonb,'QUEUED')",
              id, batch, request.path("name").asText(), request.toString());
    return id;
  }
  static void validateDefinition(JsonNode r) {
    if (!r.isObject() || r.path("name").asText().isBlank() ||
        r.path("source_code").asText().isBlank() ||
        r.path("source_code").asText().length() > 131072)
      throw new IllegalArgumentException(
          "A name and source_code (max 128 KiB) are required");
    if (!r.path("factor_class").asText().matches("[A-Za-z_][A-Za-z0-9_]*"))
      throw new IllegalArgumentException("Invalid factor_class");
  }
  static void validate(JsonNode r) {
    validateDefinition(r);
    var d = r.path("data");
    var a = r.path("analysis");
    if (!d.isObject() || !a.isObject())
      throw new IllegalArgumentException("data and analysis are required");
    var start = java.time.LocalDate.parse(d.path("factor_start").asText());
    var end = java.time.LocalDate.parse(d.path("factor_end").asText());
    if (end.isBefore(start))
      throw new IllegalArgumentException("End precedes start");
    if (!Set.of("fixture", "ft_daily_mmap")
             .contains(d.path("provider").asText()))
      throw new IllegalArgumentException("Unknown provider");
    if (!d.path("frequency").asText("1d").equals("1d") ||
        !d.path("adjustment").asText("raw").equals("raw"))
      throw new IllegalArgumentException("Only daily/raw input is supported");
    for (var it = a.fieldNames(); it.hasNext();)
      if (!Set.of("entry", "exit", "groups", "group_weight", "min_ic_pairs",
                  "icir_ddof", "annualize_icir")
               .contains(it.next()))
        throw new IllegalArgumentException("Unknown analysis option");
    for (String key : List.of("groups", "min_ic_pairs", "icir_ddof"))
      if (a.has(key) && !a.get(key).isIntegralNumber())
        throw new IllegalArgumentException(key + " must be an integer");
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
