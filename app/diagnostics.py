"""Low-cardinality operational events, without bodies, URLs or visitor identity."""
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import uuid


def operational_logger(directory: Path | None = None, *, max_bytes: int = 5 * 1024 * 1024, backup_count: int = 2):
    if not 100 <= max_bytes <= 50 * 1024 * 1024 or not 0 <= backup_count <= 5:
        raise ValueError('operational log limits are out of range')
    if directory is None:return logging.getLogger('bunkerkartet.operations')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    logger=logging.getLogger('bunkerkartet.operations.'+uuid.uuid4().hex)
    handler=RotatingFileHandler(directory/'operations.jsonl',maxBytes=max_bytes,backupCount=backup_count,encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler);logger.setLevel(logging.WARNING)
    return logger


def emit_operation(logger, *, request_id: str, code: str, elapsed_ms: float):
    if code not in {'DATABASE_UNAVAILABLE','PROVIDER_UNAVAILABLE','OPERATION_UNAVAILABLE','STARTUP_FAILED'}:
        raise ValueError('unknown operational event category')
    logger.warning(json.dumps({'event':'operation_failure','code':code,'request_id':request_id,'elapsed_ms':round(elapsed_ms,1),'at':datetime.now(timezone.utc).isoformat()},separators=(',',':')))
