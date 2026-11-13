package com.factorpool;

import com.factorpool.data.*;
import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.grpc.*;
import io.grpc.stub.MetadataUtils;
import jakarta.annotation.PreDestroy;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/** The generated protobuf client is the only backend market/value reader. */
@Component
public class DataClient {
  final ManagedChannel channel;
  final DataServiceGrpc.DataServiceBlockingStub stub;
  final ObjectMapper json;
  final int timeout;
  DataClient(ObjectMapper json,@Value("${factorpool.data-service-address}") String address,
      @Value("${factorpool.internal-token}") String token,
      @Value("${factorpool.data-timeout-seconds:10}") int timeout) {
    if (timeout<1 || timeout>120) throw new IllegalArgumentException("Invalid data service timeout");
    this.json=json;
    this.timeout=timeout;
    this.channel=ManagedChannelBuilder.forTarget(address).usePlaintext().maxInboundMessageSize(16*1024*1024).build();
    var headers=new Metadata();
    headers.put(Metadata.Key.of("x-factorpool-internal-token",Metadata.ASCII_STRING_MARSHALLER),token);
    this.stub=DataServiceGrpc.newBlockingStub(channel).withInterceptors(MetadataUtils.newAttachHeadersInterceptor(headers));
  }
  JsonNode decode(JsonReply reply) {
    try {return json.readTree(reply.getJson().toByteArray());}
    catch(Exception error) {throw new IllegalStateException("Invalid data-service response",error);}
  }
  ObjectNode snapshot(String id) {
    try {
      JsonNode response=decode(stub.withDeadlineAfter(timeout,TimeUnit.SECONDS)
          .snapshot(SnapshotRequest.newBuilder().setSnapshotId(id==null?"":id).build()));
      if(!response.isObject()) throw new IllegalStateException("Invalid snapshot response");
      return (ObjectNode)response;
    } catch(StatusRuntimeException error) {
      if(error.getStatus().getCode()==Status.Code.NOT_FOUND) return null;
      throw unavailable(error);
    }
  }
  JsonNode values(UUID id,long offset,int limit) {
    try {return decode(stub.withDeadlineAfter(timeout,TimeUnit.SECONDS).values(ValuesRequest.newBuilder()
        .setTaskId(id.toString()).setOffset(offset).setLimit(limit).build()));}
    catch(StatusRuntimeException error) {throw unavailable(error);}
  }
  void verifyPublication(JsonNode body,long expected) {
    try {
      var receipt=stub.withDeadlineAfter(timeout,TimeUnit.SECONDS).verifyPublication(ReceiptRequest.newBuilder()
          .setTaskId(body.path("task_id").asText()).setAttempt(body.path("attempt").asLong())
          .setLeaseToken(body.path("lease_token").asText()).setReceipt(body.path("receipt").asText())
          .setExpectedCount(expected).setSha256(body.path("sha256").asText()).build());
      if(!receipt.getValid() || receipt.getCount()!=expected || !receipt.getReceipt().equals(body.path("receipt").asText()))
        throw new IllegalStateException("Factor values are not completely published for this execution");
    } catch(StatusRuntimeException error) {
      if(error.getStatus().getCode()==Status.Code.FAILED_PRECONDITION || error.getStatus().getCode()==Status.Code.PERMISSION_DENIED)
        throw new IllegalStateException("Invalid or stale result publication");
      throw unavailable(error);
    }
  }
  RuntimeException unavailable(StatusRuntimeException error) {
    return new org.springframework.web.server.ResponseStatusException(org.springframework.http.HttpStatus.SERVICE_UNAVAILABLE,"Data service unavailable",error);
  }
  @PreDestroy void close(){channel.shutdownNow();}
}
