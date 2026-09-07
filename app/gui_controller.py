import asyncio
import json
import logging
import os
import queue
import threading
from pathlib import Path
from collections.abc import Awaitable, Callable
from .config import Config
from .errors import UnsafeState
from .runtime import execute
from .state_manager import RunLock, unfinished, atomic_json, timestamp


class QueueLogHandler(logging.Handler):
    def __init__(self, events: queue.Queue):
        super().__init__()
        self.events = events

    def emit(self, record: logging.LogRecord) -> None:
        self.events.put(('log', self.format(record)))


class TaskController:
    """One worker, one event loop, all UI events transferred through a queue."""
    def __init__(self, executor: Callable[..., Awaitable[int]] = execute):
        self.events: queue.Queue = queue.Queue()
        self.executor = executor
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task | None = None
        self._guard = threading.Lock()
        self._stop_requested = False
        self._cancel_sent = False

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, config: Config, resume_path: Path | None = None, restart: bool = False) -> None:
        if self.running:
            raise UnsafeState('任务仍在运行或关闭浏览器，请等待完成')
        if 'REPLACE_WITH_' in config.chat_url:
            raise ValueError('请填写你的真实 ChatGPT 对话链接')
        self._stop_requested = self._cancel_sent = False
        self.thread = threading.Thread(target=self._worker, args=(config, resume_path, restart), daemon=False)
        self.thread.start()

    def stop(self) -> None:
        with self._guard:
            self._stop_requested = True
            if self.loop and self.task and not self._cancel_sent:
                self._cancel_sent = True
                self.loop.call_soon_threadsafe(self.task.cancel)

    def _worker(self, config: Config, resume_path: Path | None, restart: bool) -> None:
        code, error = 1, ''
        try:
            cache = Path(__file__).resolve().parents[1] / '.browser-cache'
            if cache.is_dir():
                os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(cache))
            with RunLock(config.profile_dir / '.task.lock'):
                # Recheck under the lock: a CLI or another window may have changed state.
                pending = unfinished(config)
                if pending != ([resume_path] if resume_path else []):
                    raise UnsafeState('未完成任务已变化，请重新点击启动以核对断点')
                if resume_path and restart:
                    data = json.loads(resume_path.read_text(encoding='utf-8'))
                    data.update(status='ABANDONED', abandoned_at=timestamp())
                    atomic_json(resume_path, data)
                    resume_path = None
                with asyncio.Runner() as runner:
                    with self._guard:
                        self.loop = runner.get_loop()
                        self.task = self.loop.create_task(self.executor(
                            config, resume_path, lambda kind, data: self.events.put((kind, data)),
                            QueueLogHandler(self.events)))
                        if self._stop_requested:
                            self._cancel_sent = True
                            self.task.cancel()
                    try:
                        code = runner.run(self._await_task())
                    except asyncio.CancelledError:
                        code = 130
                    finally:
                        with self._guard:
                            self.task, self.loop = None, None
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
            self.events.put(('log', error))
        finally:
            self.events.put(('finished', {'code': code, 'error': error}))

    async def _await_task(self) -> int:
        assert self.task is not None
        return await self.task
