import json
import logging
from collections.abc import Mapping
from typing import Any

from sglang_ops_stack.config import Settings
from sglang_ops_stack.utils.masking import mask_secret, mask_sensitive_data


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_secret(str(record.msg))
        if isinstance(record.args, Mapping):
            record.args = mask_sensitive_data(record.args)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": mask_secret(record.getMessage()),
        }
        if isinstance(record.args, Mapping):
            payload.update(mask_sensitive_data(record.args))
        if record.exc_info:
            payload["exc_info"] = mask_secret(self.formatException(record.exc_info))
        return json.dumps(mask_sensitive_data(payload), ensure_ascii=False, default=str)


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level.upper())
