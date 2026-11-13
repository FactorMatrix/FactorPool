"""Lossless, bounded columnar research values; no per-security Python objects."""
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import zlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import numpy as np


REASONS = ('ok', 'missing_bar', 'source_missing', 'not_ready', 'native_unavailable',
           'unready_or_invalid', 'outside_universe')
FORMAT = 'factorpool-result-matrix-v1'
AXES_MAGIC = b'FPRA'
CHUNK_MAGIC = b'FPRC'
MAX_AXES_BYTES = 4 * 1024 * 1024
_FILE_RECORD = struct.Struct('!QQI32s')


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def raw_size(count):
    return count * 25 + ((count + 7) // 8) * 2


def encode_axes(dates, symbols, reason_labels=REASONS):
    value = dict(format=FORMAT, dates=list(dates), symbols=list(symbols),
                 reasons=list(reason_labels), row_count=len(dates) * len(symbols))
    payload = AXES_MAGIC + _json(value)
    decode_axes(payload)
    return payload


def decode_axes(payload, *, enforce_budget=True):
    from datetime import date
    payload = bytes(payload)
    if not payload.startswith(AXES_MAGIC) or len(payload) > MAX_AXES_BYTES:
        raise ValueError('Invalid result matrix axes size or format')
    value = json.loads(payload[4:])
    if not isinstance(value, dict) or value.get('format') != FORMAT:
        raise ValueError('Invalid result matrix axes object')
    for name, allow_empty in [('dates', True), ('symbols', False), ('reasons', False)]:
        axis = value.get(name)
        maximum = 4096 if name == 'reasons' else 128
        if (not isinstance(axis, list) or not axis and not allow_empty
                or any(not isinstance(item, str) or len(item) > maximum for item in axis)
                or len(set(axis)) != len(axis)):
            raise ValueError('Invalid result matrix ' + name)
        if name != 'reasons' and (any(not item for item in axis) or any(a >= b for a, b in zip(axis, axis[1:]))):
            raise ValueError('Result matrix axes must be ordered and unique')
    for item in value['dates']:
        if date.fromisoformat(item).isoformat() != item:
            raise ValueError('Result matrix dates must be ISO dates')
    if len(value['reasons']) > 256:
        raise ValueError('Result reason dictionary exceeds uint8 codes')
    count = len(value['dates']) * len(value['symbols'])
    if type(value.get('row_count')) is not int or value['row_count'] != count:
        raise ValueError('Result matrix row count differs from axes')
    max_bytes = int(os.environ.get('FACTORPOOL_RESULT_MAX_BYTES', '536870912'))
    if enforce_budget and (max_bytes < 1 or raw_size(count) > max_bytes):
        raise ValueError('Result matrix exceeds configured byte budget')
    value['_sha256'] = hashlib.sha256(payload).hexdigest()
    return value


def encode_chunk(axes, ordinal, values, valid, ready, reasons, forward_returns, groups, *, codec='zlib'):
    values = np.ascontiguousarray(values, dtype='<f8').reshape(-1)
    count = len(values)
    arrays = [np.ascontiguousarray(item, dtype=dtype).reshape(-1) for item, dtype in
              [(forward_returns, '<f8'), (groups, '<f8'), (valid, np.bool_),
               (ready, np.bool_), (reasons, np.uint8)]]
    forward, group, good, warmed, codes = arrays
    if (any(len(item) != count for item in arrays) or type(ordinal) is not int
            or ordinal < 0 or ordinal + count > axes['row_count']):
        raise ValueError('Result matrix chunk axes differ')
    if (np.isinf(values).any() or np.isinf(forward).any() or np.isinf(group).any()
            or np.any(good & (~np.isfinite(values) | ~warmed))
            or count and np.max(codes) >= len(axes['reasons'])):
        raise ValueError('Invalid result matrix numeric values or status')
    finite_groups = group[np.isfinite(group)]
    if (np.any(finite_groups != np.trunc(finite_groups)) or np.any(finite_groups < -(2**31))
            or np.any(finite_groups > 2**31 - 1)):
        raise ValueError('Factor group must be a finite integral int32 or null')
    raw = b''.join((values.tobytes(), forward.tobytes(), group.tobytes(),
                    np.packbits(good, bitorder='little').tobytes(),
                    np.packbits(warmed, bitorder='little').tobytes(), codes.tobytes()))
    if codec not in ('raw', 'zlib', 'lz4'):
        raise ValueError('Unknown result matrix codec')
    header = _json(dict(format=FORMAT, axes_sha256=axes['_sha256'], ordinal=ordinal,
                        row_count=count, raw_bytes=len(raw), codec=codec,
                        sha256=hashlib.sha256(raw).hexdigest()))
    if codec == 'lz4':
        import lz4.frame
        encoded = lz4.frame.compress(raw, compression_level=0, content_checksum=True, store_size=True)
    else:
        encoded = zlib.compress(raw, 1) if codec == 'zlib' else raw
    return CHUNK_MAGIC + struct.pack('!I', len(header)) + header + encoded


def decode_chunk(payload, axes, *, max_raw_bytes=4 * 1024 * 1024):
    payload = bytes(payload)
    if len(payload) < 8 or payload[:4] != CHUNK_MAGIC:
        raise ValueError('Invalid result matrix chunk format')
    length = struct.unpack('!I', payload[4:8])[0]
    if not 1 <= length <= 4096 or len(payload) < 8 + length:
        raise ValueError('Invalid result matrix chunk header')
    header = json.loads(payload[8:8+length])
    if not isinstance(header, dict):
        raise ValueError('Invalid result matrix chunk header object')
    count, ordinal = header.get('row_count'), header.get('ordinal')
    if (header.get('format') != FORMAT or header.get('axes_sha256') != axes['_sha256']
            or type(count) is not int or count < 0 or type(ordinal) is not int or ordinal < 0
            or ordinal + count > axes['row_count'] or raw_size(count) > max_raw_bytes
            or header.get('raw_bytes') != raw_size(count)):
        raise ValueError('Result matrix chunk axes or size mismatch')
    raw = payload[8+length:]
    if header.get('codec') == 'lz4':
        import lz4.frame
        try:
            if lz4.frame.get_frame_info(raw)['content_size'] != raw_size(count):
                raise ValueError('Result LZ4 frame size differs from bounded chunk')
            decoder = lz4.frame.LZ4FrameDecompressor()
            raw = decoder.decompress(raw, max_length=raw_size(count)+1)
            if not decoder.eof or decoder.unused_data:
                raise ValueError('Invalid compressed result matrix chunk')
        except RuntimeError as error:
            raise ValueError('Invalid compressed result matrix chunk') from error
    elif header.get('codec') == 'zlib':
        decoder = zlib.decompressobj()
        raw = decoder.decompress(raw, raw_size(count)+1)
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('Invalid compressed result matrix chunk')
    elif header.get('codec') != 'raw':
        raise ValueError('Unknown result matrix codec')
    if len(raw) != raw_size(count) or hashlib.sha256(raw).hexdigest() != header.get('sha256'):
        raise ValueError('Result matrix chunk integrity failure')
    values = np.frombuffer(raw, dtype='<f8', count=count)
    forward = np.frombuffer(raw, dtype='<f8', count=count, offset=count*8)
    groups = np.frombuffer(raw, dtype='<f8', count=count, offset=count*16)
    packed = (count+7)//8
    valid = np.unpackbits(np.frombuffer(raw, dtype=np.uint8, count=packed, offset=count*24), bitorder='little')[:count].view(np.bool_)
    ready = np.unpackbits(np.frombuffer(raw, dtype=np.uint8, count=packed, offset=count*24+packed), bitorder='little')[:count].view(np.bool_)
    reasons = np.frombuffer(raw, dtype=np.uint8, count=count, offset=count*24+packed*2)
    if (np.isinf(values).any() or np.isinf(forward).any() or np.isinf(groups).any()
            or np.any(valid & (~np.isfinite(values) | ~ready))
            or count and np.max(reasons) >= len(axes['reasons'])):
        raise ValueError('Invalid result matrix numeric values or status')
    finite_groups = groups[np.isfinite(groups)]
    if (np.any(finite_groups != np.trunc(finite_groups)) or np.any(finite_groups < -(2**31))
            or np.any(finite_groups > 2**31-1)):
        raise ValueError('Factor group must be a finite integral int32 or null')
    return dict(ordinal=ordinal, row_count=count, value=values, forward_return=forward,
                group=groups, valid=valid, ready=ready, reason=reasons)


def write_result_matrix(path, dates, symbols, values, valid, ready, reasons, forward_returns, groups,
                        *, signal_indices=None, reason_labels=REASONS, chunk_bytes=None,
                        codec=None, threads=1):
    """Write all requested date/security cells using bounded array chunks."""
    shape = (len(dates), len(symbols))
    arrays = [np.asarray(item) for item in (values, valid, ready, reasons, forward_returns, groups)]
    if any(item.shape != shape for item in arrays):
        raise ValueError('Result matrix arrays must match their date/security axes')
    selected = np.arange(len(dates), dtype=np.intp) if signal_indices is None else np.asarray(sorted(signal_indices), dtype=np.intp)
    if (selected.ndim != 1 or len(selected) and (selected[0] < 0 or selected[-1] >= len(dates))
            or len(np.unique(selected)) != len(selected)):
        raise ValueError('Invalid result matrix signal indices')
    metadata = encode_axes([dates[int(index)] for index in selected], symbols, reason_labels)
    axes = decode_axes(metadata)
    chunk_bytes = int(os.environ.get('FACTORPOOL_CACHE_CHUNK_MAX_BYTES', '4194304')) if chunk_bytes is None else chunk_bytes
    if not 8192 <= chunk_bytes <= 16 * 1024 * 1024:
        raise ValueError('Invalid result matrix chunk byte budget')
    codec = os.environ.get('FACTORPOOL_RESULT_CODEC', 'lz4') if codec is None else codec
    # Leave room for the envelope and the worst-case LZ4 frame expansion
    # (n + n/255 + framing), including incompressible finite doubles.
    raw_budget = (chunk_bytes-512)*255//256-64
    rows_per_chunk = max(1, raw_budget*8//202)
    if type(threads) is not int or not 1 <= threads <= 64:
        raise ValueError('Result encoding threads must be an integer in 1..64')
    batch_bytes = int(os.environ.get('FACTORPOOL_CACHE_BATCH_MAX_BYTES', '33554432'))
    if batch_bytes < chunk_bytes:
        raise ValueError('Result encoding batch bytes must cover one chunk')
    consecutive = len(selected) < 2 or bool(np.all(np.diff(selected) == 1))
    direct = [item.reshape(-1) if consecutive and item.flags.c_contiguous else None for item in arrays]
    path = Path(path)
    temporary = path.with_name(path.name+'.tmp')
    count, digest, chunks = axes['row_count'], hashlib.sha256(), 0
    workers = min(threads, max(1,batch_bytes//(2*chunk_bytes)), max(1,(count+rows_per_chunk-1)//rows_per_chunk))
    digest.update(hashlib.sha256(metadata).digest())
    def encode_at(ordinal):
        stop = min(count, ordinal+rows_per_chunk)
        width = len(symbols)
        first_day, first_column = divmod(ordinal,width)
        last_day = (stop-1)//width
        sliced = []
        for item, flat in zip(arrays,direct):
            if flat is not None:
                base = int(selected[0])*width+ordinal
                sliced.append(flat[base:base+stop-ordinal])
            elif first_day == last_day:
                sliced.append(item[int(selected[first_day]),first_column:first_column+stop-ordinal])
            else:
                # Gather only this bounded date slab for sparse signal
                # calendars or non-contiguous input views.
                slab = item[selected[first_day:last_day+1],:]
                sliced.append(slab.reshape(-1)[first_column:first_column+stop-ordinal])
        payload = encode_chunk(axes, ordinal, sliced[0], sliced[1], sliced[2], sliced[3],
                               sliced[4], sliced[5], codec=codec)
        if len(payload) > chunk_bytes:
            raise ValueError('Encoded result matrix chunk exceeds byte budget')
        return ordinal,stop-ordinal,payload,hashlib.sha256(payload).digest()
    with temporary.open('wb') as output:
        output.write(AXES_MAGIC + struct.pack('!I',len(metadata)) + metadata)
        def write(encoded):
            nonlocal chunks
            ordinal,rows,payload,sha = encoded
            digest.update(sha)
            output.write(_FILE_RECORD.pack(ordinal,rows,len(payload),sha))
            output.write(payload)
            chunks += 1
        if workers == 1:
            for ordinal in range(0,count,rows_per_chunk):
                write(encode_at(ordinal))
        else:
            pending = deque()
            with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='result-encode') as executor:
                try:
                    for ordinal in range(0,count,rows_per_chunk):
                        if len(pending) >= workers:
                            write(pending.popleft().result())
                        pending.append(executor.submit(encode_at,ordinal))
                    while pending:
                        write(pending.popleft().result())
                finally:
                    for future in pending:
                        future.cancel()
    temporary.replace(path)
    return dict(format=FORMAT, count=count, chunks=chunks, sha256=digest.hexdigest(),
                stored_bytes=path.stat().st_size, raw_bytes=raw_size(count), codec=codec,
                encoding_threads=workers)


def iter_result_chunks(path, *, max_bytes=4 * 1024 * 1024, validate_values=True):
    """Yield metadata then validated bounded file chunks for remote upload."""
    with Path(path).open('rb') as source:
        prefix = source.read(8)
        if len(prefix) != 8 or prefix[:4] != AXES_MAGIC:
            raise ValueError('Invalid result matrix file')
        size = struct.unpack('!I',prefix[4:])[0]
        if size > MAX_AXES_BYTES:
            raise ValueError('Result matrix axes exceed byte budget')
        metadata = source.read(size)
        if len(metadata) != size:
            raise ValueError('Truncated result matrix axes')
        axes = decode_axes(metadata)
        yield 0, 0, metadata, hashlib.sha256(metadata).hexdigest()
        total = 0
        while True:
            record = source.read(_FILE_RECORD.size)
            if not record:
                break
            if len(record) != _FILE_RECORD.size:
                raise ValueError('Truncated result matrix file record')
            ordinal,count,size,sha = _FILE_RECORD.unpack(record)
            if ordinal != total or count < 1 or ordinal+count > axes['row_count'] or size > max_bytes:
                raise ValueError('Result matrix file chunk sequence or size invalid')
            payload = source.read(size)
            if len(payload) != size or hashlib.sha256(payload).digest() != sha:
                raise ValueError('Result matrix file chunk integrity failure')
            if validate_values:
                block = decode_chunk(payload,axes,max_raw_bytes=max_bytes)
                if block['ordinal'] != ordinal or block['row_count'] != count:
                    raise ValueError('Result matrix file chunk axes mismatch')
            yield ordinal,count,payload,sha.hex()
            total += count
        if total != axes['row_count']:
            raise ValueError('Incomplete result matrix file')


def rows_from_chunk(axes, chunk, *, offset=0, limit=None, api=False):
    """Materialize only a page or explicit legacy export, never the whole matrix."""
    count = chunk['row_count']
    stop = count if limit is None else min(count,offset+limit)
    width = len(axes['symbols'])
    def number(value):
        return float(value) if math.isfinite(value) else None
    for index in range(offset,stop):
        ordinal = chunk['ordinal']+index
        group = number(chunk['group'][index])
        row = dict(date=axes['dates'][ordinal//width],symbol=axes['symbols'][ordinal%width],
                   value=number(chunk['value'][index]),valid=bool(chunk['valid'][index]),
                   ready=bool(chunk['ready'][index]),reason=axes['reasons'][int(chunk['reason'][index])],
                   forward_return=number(chunk['forward_return'][index]),group=group)
        if api:
            row['trading_date'] = row.pop('date')
            row['factor_group'] = int(row.pop('group')) if group is not None else None
        yield row


def write_jsonl(path, matrix_path):
    axes = None
    with Path(path).open('w') as output:
        for ordinal,count,payload,_ in iter_result_chunks(matrix_path,validate_values=False):
            if axes is None:
                axes = decode_axes(payload)
                continue
            for row in rows_from_chunk(axes,decode_chunk(payload,axes)):
                output.write(json.dumps(row,allow_nan=False)+'\n')
