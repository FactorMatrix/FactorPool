package com.factorpool;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class ScriptResolverTest {
  final ObjectMapper json = new ObjectMapper();

  ScriptResolver resolver(String mode, long timeout) {
    return new ScriptResolver(json, List.of(
        Path.of(System.getProperty("java.home"), "bin", "java").toString(),
        "-cp", System.getProperty("java.class.path"), ParserProcess.class.getName(), mode), timeout);
  }

  @Test
  void sourceIsSentOnlyOnStdinAndNormalizedJsonIsAccepted() {
    String source = "# Unicode配置\nconfig = {}\n";
    var result = resolver("success", 5000).resolve(source);
    assertEquals(source, result.path("source_config").path("received_source").asText());
    assertEquals("script", result.path("source_mode").asText());
  }

  @Test
  void parserErrorsKeepTheLineWithoutDumpingOtherStderr() {
    var error = assertThrows(IllegalArgumentException.class,
        () -> resolver("invalid", 5000).resolve("not executable"));
    assertEquals("line 7: config must be a literal dictionary", error.getMessage());
  }

  @Test
  void classDefinitionsUseTheSameBoundedFrontendValidator() {
    assertDoesNotThrow(() -> resolver("success", 5000).validateFrontend("class Factor: pass"));
    var error = assertThrows(IllegalArgumentException.class,
        () -> resolver("invalid", 5000).validateFrontend("old operator"));
    assertEquals("line 7: config must be a literal dictionary", error.getMessage());
  }

  @Test
  void blockedInputAndSleepingParserHaveAFiniteDeadline() {
    var error = assertThrows(IllegalArgumentException.class,
        () -> resolver("sleep", 500).resolve("x".repeat(131072)));
    assertTrue(error.getMessage().contains("timed out"));
  }

  @Test
  void excessiveOutputIsBoundedAndRejected() {
    var error = assertThrows(IllegalArgumentException.class,
        () -> resolver("flood", 5000).resolve("config = {}"));
    assertTrue(error.getMessage().contains("limit"));
  }

  @Test
  void invalidOrMultipleJsonDocumentsAreRejected() {
    assertThrows(IllegalArgumentException.class, () -> resolver("bad-json", 5000).resolve("config = {}"));
    assertThrows(IllegalArgumentException.class, () -> resolver("multiple-json", 5000).resolve("config = {}"));
  }

  public static class ParserProcess {
    public static void main(String[] args) throws Exception {
      String mode = args[0];
      if (mode.equals("sleep")) {
        Thread.sleep(30000);
        return;
      }
      if (mode.equals("flood")) {
        for (int i=0; i<256; ++i) System.out.write(new byte[8192]);
        return;
      }
      if (mode.equals("invalid")) {
        System.err.println("line 7: config must be a literal dictionary\nsource context must not escape");
        System.exit(2);
      }
      if (mode.equals("bad-json")) {
        System.out.println("invalid JSON");
        return;
      }
      var json = new ObjectMapper();
      var input = json.readTree(new String(System.in.readAllBytes(), StandardCharsets.UTF_8));
      if (input.path("frontend_only").asBoolean(false)) {
        System.out.println(json.createObjectNode().put("valid", true));
        return;
      }
      var out = json.createObjectNode().put("source_mode", "script").put("factor_class", "MyFactor");
      out.putObject("factor_params"); out.putObject("data"); out.putObject("analysis");
      out.putObject("source_config").put("received_source", input.path("source_code").asText());
      System.out.println(out);
      if (mode.equals("multiple-json")) System.out.println("{}");
    }
  }
}
