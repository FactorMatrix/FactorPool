"""Fenced factor-value staging and atomic publication; summaries belong to Java."""
import hashlib
import uuid

from factorpool.data_client.generated import data_pb2 as pb
from factorpool.result_matrix import AXES_MAGIC, MAX_AXES_BYTES, decode_axes, decode_chunk, rows_from_chunk
from . import connect, transaction


def _lease(conn, task, attempt, token, *, completed=False):
    row = conn.execute("WITH locked AS MATERIALIZED (SELECT status,execution_attempt,lease_token,lease_expires_at,result_attempt "
                       "FROM research_tasks WHERE id=%s FOR UPDATE) SELECT status,execution_attempt,lease_token,"
                       "lease_expires_at > clock_timestamp(),result_attempt FROM locked", (task,)).fetchone()
    if (row is None or row[1] != attempt or str(row[2]) != str(token)
            or not (row[0] == "RUNNING" and row[3] or completed and row[0] == "SUCCEEDED" and row[4] == attempt)):
        raise ValueError("Execution lease is stale, cancelled, expired or unavailable")
    return row


class ValueStore:
    def stage(self, chunks, max_bytes):
        # One TCP session per stream; each chunk still commits separately and
        # checks the live lease under its own row lock. Context exit closes the
        # session on success, cancellation, malformed data or iterator failure.
        with connect() as connection:
            return self._stage(chunks,max_bytes,connection)

    def _stage(self, chunks, max_bytes, connection):
        digest, total, identity, axes = hashlib.sha256(), 0, None, None
        for chunk in chunks:
            lease = chunk.lease
            current = (str(uuid.UUID(lease.task_id)), lease.attempt, str(uuid.UUID(lease.lease_token)))
            if identity is None:
                identity = current
            if current != identity or lease.attempt < 1 or chunk.ordinal != total:
                raise ValueError("Invalid factor-value upload sequence")
            payload = bytes(chunk.payload)
            metadata = payload.startswith(AXES_MAGIC)
            allowed = MAX_AXES_BYTES if metadata else max_bytes
            if len(payload) > allowed or hashlib.sha256(payload).hexdigest() != chunk.sha256:
                raise ValueError("Factor-value chunk integrity or size failure")
            task, attempt, token = identity
            if metadata:
                if axes is not None or total or chunk.row_count:
                    raise ValueError("Result matrix axes must be first and unique")
                axes = decode_axes(payload)
                with transaction(connection) as conn:
                    _lease(conn,task,attempt,token)
                    existing = conn.execute("SELECT row_count,sha256 FROM factor_value_matrix_axes WHERE task_id=%s AND attempt=%s",(task,attempt)).fetchone()
                    if existing is not None:
                        if existing != (axes['row_count'],chunk.sha256):
                            raise ValueError("Conflicting retry of result matrix axes")
                    else:
                        conn.execute("INSERT INTO factor_value_matrix_axes(task_id,attempt,row_count,payload,sha256) VALUES(%s,%s,%s,%s,%s)",
                                     (task,attempt,axes['row_count'],payload,chunk.sha256))
                digest.update(bytes.fromhex(chunk.sha256))
                continue
            if axes is None:
                raise ValueError("A binary result matrix axes chunk is required first")
            block = decode_chunk(payload,axes,max_raw_bytes=max_bytes)
            count = block['row_count']
            if count < 1 or chunk.row_count != count or block['ordinal'] != total:
                raise ValueError("Result matrix chunk row count or sequence differs")
            with transaction(connection) as conn:
                _lease(conn,task,attempt,token)
                existing = conn.execute("SELECT row_count,sha256 FROM factor_value_upload_chunks WHERE task_id=%s AND attempt=%s AND ordinal=%s",(task,attempt,total)).fetchone()
                if existing is not None:
                    if existing != (count,chunk.sha256):
                        raise ValueError("Conflicting retry of factor-value chunk")
                else:
                    conn.execute("INSERT INTO factor_value_matrix_chunks(task_id,attempt,ordinal,row_count,payload,sha256) VALUES(%s,%s,%s,%s,%s,%s)",
                                 (task,attempt,total,count,payload,chunk.sha256))
                    conn.execute("INSERT INTO factor_value_upload_chunks(task_id,attempt,ordinal,row_count,sha256) VALUES(%s,%s,%s,%s,%s)",
                                 (task,attempt,total,count,chunk.sha256))
            digest.update(bytes.fromhex(chunk.sha256))
            total += count
        if identity is None:
            raise ValueError("Empty factor-value upload stream")
        if total != axes['row_count']:
            raise ValueError("Incomplete result matrix stream")
        return pb.ReceiptReply(valid=True,count=total,sha256=digest.hexdigest())

    def finalize(self, request):
        task = str(uuid.UUID(request.task_id))
        token = str(uuid.UUID(request.lease_token))
        if request.attempt < 1 or request.expected_count < 0:
            raise ValueError("Invalid publication attempt/count")
        with transaction() as conn:
            _lease(conn, task, request.attempt, token, completed=True)
            existing = conn.execute("SELECT attempt,row_count,sha256,receipt,storage_format FROM factor_value_publications WHERE task_id=%s", (task,)).fetchone()
            if existing and existing[0] == request.attempt:
                if existing[4] != 'matrix-v1':
                    raise ValueError("Unsupported result publication format")
                if existing[1:3] != (request.expected_count, request.sha256):
                    raise ValueError("Conflicting publication retry")
                return pb.ReceiptReply(valid=True, count=existing[1], sha256=existing[2], receipt=str(existing[3]))
            chunks = conn.execute("SELECT ordinal,row_count,sha256 FROM factor_value_upload_chunks "
                "WHERE task_id=%s AND attempt=%s ORDER BY ordinal", (task, request.attempt)).fetchall()
            axis_row = conn.execute("SELECT row_count,payload,sha256 FROM factor_value_matrix_axes WHERE task_id=%s AND attempt=%s",(task,request.attempt)).fetchone()
            if axis_row is None:
                raise ValueError("A binary result matrix axes chunk is required")
            total, digest = 0, hashlib.sha256()
            if hashlib.sha256(bytes(axis_row[1])).hexdigest() != axis_row[2]:
                raise ValueError("Result matrix axes integrity failure")
            axes = decode_axes(axis_row[1])
            if axes['row_count'] != axis_row[0] or axis_row[0] != request.expected_count:
                raise ValueError("Incomplete result matrix axes")
            digest.update(bytes.fromhex(axis_row[2]))
            for ordinal,count,sha in chunks:
                if ordinal != total:
                    raise ValueError("Incomplete factor-value upload chunks")
                total += count
                digest.update(bytes.fromhex(sha))
            actual_chunks = conn.execute("SELECT ordinal,row_count,sha256 FROM factor_value_matrix_chunks WHERE task_id=%s AND attempt=%s ORDER BY ordinal",(task,request.attempt)).fetchall()
            actual = sum(item[1] for item in actual_chunks)
            if chunks != actual_chunks:
                raise ValueError("Incomplete result matrix chunks")
            if ((not chunks and request.expected_count != 0)
                    or total != request.expected_count or actual != total or digest.hexdigest() != request.sha256):
                raise ValueError("Incomplete factor-value publication")
            receipt = uuid.uuid4()
            conn.execute("INSERT INTO factor_value_publications(task_id,attempt,row_count,sha256,receipt,storage_format) VALUES(%s,%s,%s,%s,%s,%s) "
                         "ON CONFLICT(task_id) DO UPDATE SET attempt=excluded.attempt,row_count=excluded.row_count,"
                         "sha256=excluded.sha256,receipt=excluded.receipt,storage_format=excluded.storage_format,created_at=now()",
                         (task,request.attempt,total,request.sha256,receipt,'matrix-v1'))
            conn.execute("DELETE FROM factor_value_matrix_chunks WHERE task_id=%s AND attempt<>%s",(task,request.attempt))
            conn.execute("DELETE FROM factor_value_matrix_axes WHERE task_id=%s AND attempt<>%s",(task,request.attempt))
        return pb.ReceiptReply(valid=True, count=total, sha256=request.sha256, receipt=str(receipt))

    def verify(self, request):
        with transaction() as conn:
            _lease(conn, request.task_id, request.attempt, request.lease_token, completed=True)
            row = conn.execute("SELECT attempt,row_count,sha256,receipt,storage_format FROM factor_value_publications WHERE task_id=%s", (request.task_id,)).fetchone()
            valid = (row is not None and row[4] == 'matrix-v1' and row[0] == request.attempt and row[1] == request.expected_count
                     and str(row[3]) == request.receipt and row[2] == request.sha256)
            return pb.ReceiptReply(valid=valid, count=row[1] if row else 0, sha256=row[2] if row else "",
                                   receipt=str(row[3]) if row else "")

    def visible_version(self, task):
        with transaction(readonly=True) as conn:
            row = conn.execute("SELECT status,result_attempt FROM research_tasks WHERE id=%s", (task,)).fetchone()
            if row is None or row[0] != "SUCCEEDED":
                raise ValueError("Task has no complete factor values")
            receipt = conn.execute("SELECT attempt,storage_format FROM factor_value_publications WHERE task_id=%s", (task,)).fetchone()
            if row[1] is None or receipt is None or receipt != (row[1], 'matrix-v1'):
                raise ValueError("Task has no current binary result matrix publication")
            return row[1]

    def page(self, task, offset, limit):
        if offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("Invalid pagination")
        with transaction(readonly=True) as conn:
            publication = conn.execute("SELECT attempt,storage_format FROM factor_value_publications WHERE task_id=%s",(task,)).fetchone()
            if publication is None or publication[1] != 'matrix-v1':
                raise ValueError("Task has no current binary result matrix publication")
            attempt = publication[0]
            axis = conn.execute("SELECT payload,sha256 FROM factor_value_matrix_axes WHERE task_id=%s AND attempt=%s",(task,attempt)).fetchone()
            if axis is None or hashlib.sha256(bytes(axis[0])).hexdigest() != axis[1]:
                raise ValueError("Result matrix axes integrity failure")
            # A lower budget for new tasks must not hide already published
            # histories: this reader only allocates the requested chunks.
            axes = decode_axes(axis[0],enforce_budget=False)
            stop = min(axes['row_count'],offset+limit)
            if offset >= stop:
                return []
            chunks = conn.execute("SELECT ordinal,row_count,payload,sha256 FROM factor_value_matrix_chunks "
                "WHERE task_id=%s AND attempt=%s AND ordinal<%s AND ordinal+row_count>%s ORDER BY ordinal",(task,attempt,stop,offset)).fetchall()
            result,covered = [],offset
            for ordinal,count,payload,sha in chunks:
                if hashlib.sha256(bytes(payload)).hexdigest() != sha:
                    raise ValueError("Result matrix chunk integrity failure")
                block = decode_chunk(payload,axes,max_raw_bytes=16*1024*1024)
                left,right = max(offset,ordinal),min(stop,ordinal+count)
                if left != covered or block['ordinal'] != ordinal or block['row_count'] != count:
                    raise ValueError("Result matrix has missing or overlapping chunks")
                result.extend(rows_from_chunk(axes,block,offset=left-ordinal,limit=right-left,api=True))
                covered=right
            if covered != stop:
                raise ValueError("Result matrix page is incomplete")
            return result
