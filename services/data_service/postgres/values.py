"""Fenced factor-value staging and atomic publication; summaries belong to Java."""
from datetime import date
import hashlib
import math
import uuid

from factorpool.data_client.generated import data_pb2 as pb
from . import transaction


def _lease(conn, task, attempt, token, *, completed=False):
    row = conn.execute("WITH locked AS MATERIALIZED (SELECT status,execution_attempt,lease_token,lease_expires_at,result_attempt "
                       "FROM research_tasks WHERE id=%s FOR UPDATE) SELECT status,execution_attempt,lease_token,"
                       "lease_expires_at > clock_timestamp(),result_attempt FROM locked", (task,)).fetchone()
    if (row is None or row[1] != attempt or str(row[2]) != str(token)
            or not (row[0] == "RUNNING" and row[3] or completed and row[0] == "SUCCEEDED" and row[4] == attempt)):
        raise ValueError("Execution lease is stale, cancelled, expired or unavailable")
    return row


def _row(task, attempt, ordinal, row):
    date.fromisoformat(row.date)
    if not row.symbol or len(row.symbol) > 128 or len(row.reason) > 4096:
        raise ValueError("Invalid factor-value identity or reason")
    for present, value in ((row.has_value, row.value), (row.has_forward_return, row.forward_return)):
        if present and not math.isfinite(value):
            raise ValueError("Factor-value numeric fields must be finite or null")
    return (task, attempt, ordinal, row.date, row.symbol, row.value if row.has_value else None,
            row.valid, row.ready, row.reason, row.forward_return if row.has_forward_return else None,
            row.group if row.has_group else None)


class ValueStore:
    def stage(self, chunks, max_bytes):
        digest, total, identity = hashlib.sha256(), 0, None
        for chunk in chunks:
            lease = chunk.lease
            current = (str(uuid.UUID(lease.task_id)), lease.attempt, str(uuid.UUID(lease.lease_token)))
            if identity is None:
                identity = current
            if current != identity or lease.attempt < 1 or chunk.ordinal != total:
                raise ValueError("Invalid factor-value upload sequence")
            if len(chunk.payload) > max_bytes or hashlib.sha256(chunk.payload).hexdigest() != chunk.sha256:
                raise ValueError("Factor-value chunk integrity or size failure")
            rows = pb.ValuesPayload.FromString(chunk.payload).rows
            if chunk.row_count != len(rows) or len(rows) > 1000 or total + len(rows) > 2_000_000:
                raise ValueError("Factor-value chunk row count differs")
            task, attempt, token = identity
            with transaction() as conn:
                _lease(conn, task, attempt, token)
                existing = conn.execute("SELECT row_count,sha256 FROM factor_value_upload_chunks "
                    "WHERE task_id=%s AND attempt=%s AND ordinal=%s", (task, attempt, total)).fetchone()
                if existing is not None:
                    if existing != (len(rows), chunk.sha256):
                        raise ValueError("Conflicting retry of factor-value chunk")
                else:
                    if rows:
                        with conn.cursor() as cursor:
                            cursor.executemany("INSERT INTO factor_value_staging "
                                "(task_id,attempt,ordinal,trading_date,symbol,value,valid,ready,reason,forward_return,factor_group) "
                                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                [_row(task, attempt, total+n, row) for n, row in enumerate(rows)])
                    conn.execute("INSERT INTO factor_value_upload_chunks(task_id,attempt,ordinal,row_count,sha256) "
                                 "VALUES(%s,%s,%s,%s,%s)", (task, attempt, total, len(rows), chunk.sha256))
            digest.update(bytes.fromhex(chunk.sha256))
            total += len(rows)
        if identity is None:
            raise ValueError("Empty factor-value upload stream")
        return pb.ReceiptReply(valid=True, count=total, sha256=digest.hexdigest())

    def finalize(self, request):
        task = str(uuid.UUID(request.task_id))
        token = str(uuid.UUID(request.lease_token))
        if request.attempt < 1 or request.expected_count < 0:
            raise ValueError("Invalid publication attempt/count")
        with transaction() as conn:
            _lease(conn, task, request.attempt, token, completed=True)
            existing = conn.execute("SELECT attempt,row_count,sha256,receipt FROM factor_value_publications WHERE task_id=%s", (task,)).fetchone()
            if existing and existing[0] == request.attempt:
                if existing[1:3] != (request.expected_count, request.sha256):
                    raise ValueError("Conflicting publication retry")
                return pb.ReceiptReply(valid=True, count=existing[1], sha256=existing[2], receipt=str(existing[3]))
            chunks = conn.execute("SELECT ordinal,row_count,sha256 FROM factor_value_upload_chunks "
                "WHERE task_id=%s AND attempt=%s ORDER BY ordinal", (task, request.attempt)).fetchall()
            total, digest = 0, hashlib.sha256()
            for ordinal, count, sha in chunks:
                if ordinal != total:
                    raise ValueError("Incomplete factor-value upload chunks")
                total += count
                digest.update(bytes.fromhex(sha))
            actual = conn.execute("SELECT count(*) FROM factor_value_staging WHERE task_id=%s AND attempt=%s",
                                  (task, request.attempt)).fetchone()[0]
            if not chunks or total != request.expected_count or actual != total or digest.hexdigest() != request.sha256:
                raise ValueError("Incomplete factor-value publication")
            receipt = uuid.uuid4()
            conn.execute("DELETE FROM factor_values WHERE task_id=%s", (task,))
            conn.execute("INSERT INTO factor_values(task_id,ordinal,trading_date,symbol,value,valid,ready,reason,forward_return,factor_group) "
                         "SELECT task_id,ordinal,trading_date,symbol,value,valid,ready,reason,forward_return,factor_group "
                         "FROM factor_value_staging WHERE task_id=%s AND attempt=%s ORDER BY ordinal", (task, request.attempt))
            conn.execute("INSERT INTO factor_value_publications(task_id,attempt,row_count,sha256,receipt) VALUES(%s,%s,%s,%s,%s) "
                         "ON CONFLICT(task_id) DO UPDATE SET attempt=excluded.attempt,row_count=excluded.row_count,"
                         "sha256=excluded.sha256,receipt=excluded.receipt,created_at=now()",
                         (task, request.attempt, total, request.sha256, receipt))
            conn.execute("DELETE FROM factor_value_staging WHERE task_id=%s", (task,))
        return pb.ReceiptReply(valid=True, count=total, sha256=request.sha256, receipt=str(receipt))

    def verify(self, request):
        with transaction() as conn:
            _lease(conn, request.task_id, request.attempt, request.lease_token, completed=True)
            row = conn.execute("SELECT attempt,row_count,sha256,receipt FROM factor_value_publications WHERE task_id=%s", (request.task_id,)).fetchone()
            valid = (row is not None and row[0] == request.attempt and row[1] == request.expected_count
                     and str(row[3]) == request.receipt and (not request.sha256 or row[2] == request.sha256))
            return pb.ReceiptReply(valid=valid, count=row[1] if row else 0, sha256=row[2] if row else "",
                                   receipt=str(row[3]) if row else "")

    def visible_version(self, task):
        with transaction(readonly=True) as conn:
            row = conn.execute("SELECT status,result_attempt FROM research_tasks WHERE id=%s", (task,)).fetchone()
            if row is None or row[0] != "SUCCEEDED":
                raise ValueError("Task has no complete factor values")
            if row[1] is not None:
                receipt = conn.execute("SELECT attempt FROM factor_value_publications WHERE task_id=%s", (task,)).fetchone()
                if receipt is None or receipt[0] != row[1]:
                    raise ValueError("Task publication does not match completed attempt")
            return row[1] or 0

    def page(self, task, offset, limit):
        with transaction(readonly=True) as conn:
            rows = conn.execute("SELECT trading_date,symbol,value,valid,ready,reason,forward_return,factor_group FROM factor_values "
                                "WHERE task_id=%s ORDER BY ordinal LIMIT %s OFFSET %s", (task, limit, offset)).fetchall()
        return [dict(trading_date=str(day), symbol=symbol, value=value, valid=valid, ready=ready,
                     reason=reason, forward_return=forward, factor_group=group)
                for day, symbol, value, valid, ready, reason, forward, group in rows]
