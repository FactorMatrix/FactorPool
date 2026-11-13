"""Independent internal data service; startup never imports market data."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import signal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    import grpc
    from factorpool.data_client.generated import data_pb2 as pb, data_pb2_grpc
    token = os.environ.get("FACTORPOOL_INTERNAL_TOKEN", "")
    if not token:
        raise RuntimeError("FACTORPOOL_INTERNAL_TOKEN must be provided")
    if args.health:
        address = os.environ.get("FACTORPOOL_HEALTH_ADDR", "127.0.0.1:50051")
        with grpc.insecure_channel(address) as channel:
            reply = data_pb2_grpc.DataServiceStub(channel).Health(pb.Empty(), timeout=3,
                metadata=(("x-factorpool-internal-token", token),))
            value = json.loads(reply.json)
            print(json.dumps(value))
            if value["status"] != "UP":
                raise SystemExit(1)
        return
    from .api import DataService
    workers = int(os.environ.get("FACTORPOOL_DATA_THREADS", "4"))
    server = grpc.server(ThreadPoolExecutor(max_workers=workers), maximum_concurrent_rpcs=workers*4,
        options=[("grpc.max_receive_message_length", 20*1024*1024), ("grpc.max_send_message_length", 20*1024*1024)])
    data_pb2_grpc.add_DataServiceServicer_to_server(DataService(), server)
    server.add_insecure_port(os.environ.get("FACTORPOOL_DATA_BIND", "[::]:50051"))
    server.start()
    def stop(*_):
        server.stop(10)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
