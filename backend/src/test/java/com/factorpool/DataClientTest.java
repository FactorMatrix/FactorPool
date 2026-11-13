package com.factorpool;

import com.factorpool.data.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.google.protobuf.ByteString;
import io.grpc.*;
import io.grpc.stub.StreamObserver;
import java.util.UUID;
import org.junit.jupiter.api.*;
import static org.junit.jupiter.api.Assertions.*;

class DataClientTest {
  Server server;
  DataClient client;
  final String secret="isolated-test-service-secret-32";
  final ObjectMapper json=new ObjectMapper();
  @BeforeEach void start() throws Exception {
    var service=new DataServiceGrpc.DataServiceImplBase() {
      @Override public void snapshot(SnapshotRequest request,StreamObserver<JsonReply> observer) {
        if(request.getSnapshotId().equals("missing")) {observer.onError(Status.NOT_FOUND.asRuntimeException());return;}
        if(request.getSnapshotId().equals("unavailable")) {observer.onError(Status.UNAVAILABLE.asRuntimeException());return;}
        observer.onNext(JsonReply.newBuilder().setJson(ByteString.copyFromUtf8("{\"snapshot_id\":\"snapshot-a\",\"symbols\":[\"A\"]}")).build());observer.onCompleted();
      }
      @Override public void values(ValuesRequest request,StreamObserver<JsonReply> observer) {
        assertEquals(2,request.getOffset());assertEquals(1,request.getLimit());
        observer.onNext(JsonReply.newBuilder().setJson(ByteString.copyFromUtf8("[{\"symbol\":\"A\",\"valid\":true,\"value\":null}]")).build());observer.onCompleted();
      }
      @Override public void verifyPublication(ReceiptRequest request,StreamObserver<ReceiptReply> observer) {
        boolean valid=request.getReceipt().equals("committed-receipt") && request.getAttempt()==3 && request.getExpectedCount()==2;
        observer.onNext(ReceiptReply.newBuilder().setValid(valid).setCount(valid?2:0).setReceipt(request.getReceipt()).build());observer.onCompleted();
      }
    };
    ServerInterceptor auth=new ServerInterceptor() {
      public <ReqT,RespT> ServerCall.Listener<ReqT> interceptCall(ServerCall<ReqT,RespT> call,Metadata headers,ServerCallHandler<ReqT,RespT> next) {
        assertEquals(secret,headers.get(Metadata.Key.of("x-factorpool-internal-token",Metadata.ASCII_STRING_MARSHALLER)));
        return next.startCall(call,headers);
      }
    };
    server=ServerBuilder.forPort(0).addService(ServerInterceptors.intercept(service,auth)).build().start();
    client=new DataClient(json,"127.0.0.1:"+server.getPort(),secret,2);
  }
  @AfterEach void close(){client.close();server.shutdownNow();}
  @Test void generatedClientUsesAuthenticatedProtobufTransportAndPreservesNullValues() {
    assertEquals("snapshot-a",client.snapshot("").path("snapshot_id").asText());
    var values=client.values(UUID.randomUUID(),2,1);
    assertEquals("A",values.path(0).path("symbol").asText());
    assertTrue(values.path(0).path("value").isNull());
  }
  @Test void missingSnapshotAndUnavailableServiceAreDifferentOutcomes() {
    assertNull(client.snapshot("missing"));
    var error=assertThrows(org.springframework.web.server.ResponseStatusException.class,()->client.snapshot("unavailable"));
    assertEquals(503,error.getStatusCode().value());
  }
  @Test void mismatchedPublicationNeverPassesCompletionGate() {
    var body=json.createObjectNode().put("task_id",UUID.randomUUID().toString()).put("attempt",3)
      .put("lease_token",UUID.randomUUID().toString()).put("receipt","committed-receipt");
    assertDoesNotThrow(()->client.verifyPublication(body,2));
    assertThrows(IllegalStateException.class,()->client.verifyPublication(body,3));
  }
}
