"""Publish in-memory test quotes using the current four-table field contract."""
import hashlib
import numpy as np

from factorpool.data_client.contract import FIELDS, SERVED_FIELDS, market_field
from factorpool.market_adjustment import adjustment_policy, derive_adjustment
from factorpool.market_returns import return_policy
from services.data_service.market_store import STORAGE_FORMAT, block_digest, field_catalog


def field_snapshot(metadata, matrix):
    columns = {field: np.ascontiguousarray(market_field(matrix, field),
               dtype='|u1' if field.startswith('__') else '<f8') for field in SERVED_FIELDS}
    adjusted = derive_adjustment(*(columns[field] for field in
        ('close', 'prev_close', 'volume', 'total_turnover', 'member', 'is_suspended')),
        dates=np.array([int(day.replace('-', '')) for day in metadata['dates']], dtype='<i8'))
    columns.update(__q__=adjusted.q, __segment__=adjusted.segment,
                   __adj_factor__=adjusted.factor, __adj_known__=adjusted.known)
    catalog = field_catalog()
    columns = {field: np.ascontiguousarray(value, dtype=catalog[field]['dtype'])
               for field, value in columns.items()}
    indexed = []
    for block in metadata['blocks']:
        start, count = block['start_index'], block['row_count']
        descriptors = {field: dict(dtype=value.dtype.str,
            sha256=hashlib.sha256(value[start:start+count].tobytes()).hexdigest())
            for field, value in columns.items()}
        indexed.append(dict(start_index=start, row_count=count, fields=descriptors,
                            sha256=block_digest(start, count, descriptors)))
    metadata['manifest'].update(storage_format=STORAGE_FORMAT, field_catalog=catalog,
        field_blocks=indexed, return_policy=return_policy(), adjustment_policy=adjustment_policy())
    metadata.update(field_catalog=catalog, field_blocks=indexed,
        blocks=[{key: block[key] for key in ('start_index', 'row_count', 'sha256')} for block in indexed])
    return columns


def matrix_blocks(source, metadata, initial, stop, columns, *, fields=FIELDS):
    """Stack typed columns only for tests comparing the independent matrix oracle."""
    for start, count, values in source.selected_columns(metadata, initial, stop, columns, fields=fields):
        yield start, count, np.stack([values[field] for field in fields])
