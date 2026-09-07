import logging
from pathlib import Path
from datetime import datetime
from uuid import uuid4


def create_run(log_root: Path, existing: Path | None = None,
               extra_handler: logging.Handler | None = None) -> tuple[Path, logging.Logger]:
    directory = existing or log_root / (datetime.now().strftime('%Y-%m-%d_%H%M%S') + '_' + uuid4().hex[:6])
    (directory / 'screenshots').mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('chatgpt_automation')
    logger.setLevel(logging.INFO)
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s %(message)s', '%Y-%m-%d %H:%M:%S')
    import sys
    handlers = [logging.FileHandler(directory / 'run.log', encoding='utf-8')]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    if extra_handler:
        handlers.append(extra_handler)
    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return directory, logger
