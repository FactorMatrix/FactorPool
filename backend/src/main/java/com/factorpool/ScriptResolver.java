package com.factorpool;

import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

/** The same bounded AST parser is used for previews, versions and submissions. */
@Component
public class ScriptResolver {
  static final int SOURCE_LIMIT = 131072, OUTPUT_LIMIT = 1048576, ERROR_LIMIT = 8192;
  final ObjectMapper json;
  final List<String> command;
  final long timeoutMillis;

  @Autowired
  ScriptResolver(ObjectMapper json) {
    this(json, List.of("/usr/local/bin/python3", "-m", "factorpool.script"), 10000);
  }

  ScriptResolver(ObjectMapper json, List<String> command, long timeoutMillis) {
    this.json = json;
    this.command = List.copyOf(command);
    this.timeoutMillis = timeoutMillis;
  }

  ObjectNode resolve(String source) {
    return resolve(source, null);
  }

  ObjectNode resolve(String source, JsonNode workspaceConfig) {
    if (workspaceConfig != null && !workspaceConfig.isObject())
      throw new IllegalArgumentException("workspace_config must be an object");
    if (source == null || source.isBlank() || source.length() > SOURCE_LIMIT)
      throw new IllegalArgumentException("source_code (max 128 KiB) is required");
    Process child = null;
    ExecutorService io = Executors.newFixedThreadPool(3, task -> {
      Thread thread = new Thread(task, "factorpool-script-parser-io");
      thread.setDaemon(true);
      return thread;
    });
    try {
      child = new ProcessBuilder(command).start();
      final Process process = child;
      long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMillis);
      Future<byte[]> output = io.submit(() -> read(process.getInputStream(), OUTPUT_LIMIT, process));
      Future<byte[]> errors = io.submit(() -> read(process.getErrorStream(), ERROR_LIMIT, process));
      Future<?> input = io.submit(() -> {
        try (var writer = process.getOutputStream()) {
          ObjectNode body = json.createObjectNode().put("source_code", source);
          if (workspaceConfig != null) body.set("workspace_config", workspaceConfig);
          json.writeValue(writer, body);
        }
        return null;
      });
      if (!process.waitFor(timeoutMillis, TimeUnit.MILLISECONDS))
        throw new IllegalArgumentException("Script parser timed out");
      byte[] stdout = result(output, deadline);
      byte[] stderr = result(errors, deadline);
      if (process.exitValue() != 0)
        throw new IllegalArgumentException(error(stderr));
      result(input, deadline);
      JsonNode resolved = json.reader().with(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
                              .readTree(stdout);
      if (resolved == null || !resolved.isObject() ||
          !resolved.path("source_mode").asText().equals("script") ||
          !resolved.path("factor_class").asText().matches("[A-Za-z_][A-Za-z0-9_]*") ||
          !resolved.path("factor_params").isObject() ||
          !resolved.path("data").isObject() || !resolved.path("analysis").isObject() ||
          !resolved.path("source_config").isObject() ||
          (resolved.has("brain_settings") && !resolved.path("brain_settings").isObject()))
        throw new IllegalArgumentException("Script parser returned an invalid configuration");
      return (ObjectNode) resolved;
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      throw new IllegalArgumentException("Script parsing interrupted");
    } catch (IOException e) {
      throw new IllegalArgumentException("Script parser unavailable or returned invalid JSON");
    } finally {
      if (child != null) {
        child.descendants().forEach(ProcessHandle::destroyForcibly);
        if (child.isAlive())
          child.destroyForcibly();
        try { child.getOutputStream().close(); } catch (IOException ignored) {}
        try { child.getInputStream().close(); } catch (IOException ignored) {}
        try { child.getErrorStream().close(); } catch (IOException ignored) {}
      }
      io.shutdownNow();
    }
  }

  private static byte[] read(InputStream input, int limit, Process process) throws IOException {
    try (input; var output = new ByteArrayOutputStream()) {
      byte[] buffer = new byte[8192];
      int count;
      while ((count = input.read(buffer)) != -1) {
        if (output.size() + count > limit) {
          process.destroyForcibly();
          throw new IOException("Script parser output exceeded its limit");
        }
        output.write(buffer, 0, count);
      }
      return output.toByteArray();
    }
  }

  private static <T> T result(Future<T> future, long deadline) throws InterruptedException {
    try {
      return future.get(Math.max(1, deadline - System.nanoTime()), TimeUnit.NANOSECONDS);
    } catch (TimeoutException e) {
      throw new IllegalArgumentException("Script parser timed out");
    } catch (ExecutionException e) {
      throw new IllegalArgumentException("Script parser input/output failed or exceeded its limit");
    }
  }

  private static String error(byte[] bytes) {
    String text = new String(bytes, StandardCharsets.UTF_8).strip();
    if (text.isBlank())
      return "Invalid script configuration";
    String line = text.lines().findFirst().orElse("Invalid script configuration");
    return line.substring(0, Math.min(line.length(), 1000));
  }
}
