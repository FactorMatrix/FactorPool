package com.factorpool;

import com.fasterxml.jackson.databind.JsonNode;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;
import java.util.NoSuchElementException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/internal/worker")
public class WorkerApi {
  final Tasks tasks;
  final byte[] secret;
  WorkerApi(Tasks tasks,@Value("${factorpool.internal-token}") String token) {
    if (token == null || token.length()<24) throw new IllegalArgumentException("Internal service token must contain at least 24 characters");
    this.tasks=tasks;
    this.secret=token.getBytes(StandardCharsets.UTF_8);
  }
  void authorize(String token) {
    if (token==null || !MessageDigest.isEqual(secret,token.getBytes(StandardCharsets.UTF_8)))
      throw new org.springframework.web.server.ResponseStatusException(org.springframework.http.HttpStatus.UNAUTHORIZED,"Invalid internal service token");
  }
  @PostMapping("/claim")
  ResponseEntity<?> claim(@RequestHeader(value="X-FactorPool-Internal-Token",required=false) String token,@RequestBody JsonNode body) {
    authorize(token);
    var task=tasks.claim(body.path("worker_id").asText());
    return task==null?ResponseEntity.noContent().build():ResponseEntity.ok(task);
  }
  @PostMapping("/heartbeat")
  Object heartbeat(@RequestHeader(value="X-FactorPool-Internal-Token",required=false) String token,@RequestBody JsonNode body) {
    authorize(token);return tasks.heartbeat(body);
  }
  @PostMapping("/complete")
  Object complete(@RequestHeader(value="X-FactorPool-Internal-Token",required=false) String token,@RequestBody JsonNode body) {
    authorize(token);return tasks.complete(body);
  }
  @PostMapping("/fail")
  Object fail(@RequestHeader(value="X-FactorPool-Internal-Token",required=false) String token,@RequestBody JsonNode body) {
    authorize(token);return tasks.fail(body);
  }
  @PostMapping("/logs")
  Object logs(@RequestHeader(value="X-FactorPool-Internal-Token",required=false) String token,@RequestBody JsonNode body) {
    authorize(token);return tasks.log(body);
  }
  @ExceptionHandler(IllegalArgumentException.class)
  ResponseEntity<?> bad(Exception error) {return ResponseEntity.badRequest().body(Map.of("error",error.getMessage()));}
  @ExceptionHandler(IllegalStateException.class)
  ResponseEntity<?> stale(Exception error) {return ResponseEntity.status(409).body(Map.of("error",error.getMessage()));}
  @ExceptionHandler(NoSuchElementException.class)
  ResponseEntity<?> missing(Exception error) {return ResponseEntity.status(404).body(Map.of("error",error.getMessage()));}
}
