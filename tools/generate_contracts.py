"""Generate the Python gRPC contract using pinned grpcio-tools 1.76.0."""
import argparse
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if version('grpcio-tools') != '1.76.0':
        raise RuntimeError('Contract generation requires grpcio-tools==1.76.0')
    root = Path(__file__).resolve().parent.parent
    target = root / 'src/factorpool/data_client/generated'
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary)
        subprocess.run([sys.executable, '-m', 'grpc_tools.protoc', '-I', str(root/'shared/contracts'),
                        '--python_out='+str(output), '--grpc_python_out='+str(output),
                        str(root/'shared/contracts/data.proto')], check=True)
        grpc = output/'data_pb2_grpc.py'
        grpc.write_text(grpc.read_text().replace('import data_pb2 as data__pb2', 'from . import data_pb2 as data__pb2'))
        stale = []
        for filename in ('data_pb2.py', 'data_pb2_grpc.py'):
            generated = (output/filename).read_bytes()
            if args.check:
                if not (target/filename).is_file() or (target/filename).read_bytes() != generated:
                    stale.append(filename)
            else:
                target.mkdir(parents=True, exist_ok=True)
                (target/filename).write_bytes(generated)
        if stale:
            raise SystemExit('Generated contract differs: '+', '.join(stale))
    print('Python gRPC contract is current')


if __name__ == '__main__':
    main()
