"""Shared CLI/GUI lifecycle; Tk never calls Playwright from its UI thread."""
import asyncio
import logging
from pathlib import Path
from collections.abc import Callable
from .config import Config
from .state_manager import StateManager, timestamp
from .logger import create_run
from .scheduler import wait_for_start


async def execute(config: Config, resume_path: Path | None,
                  on_event: Callable[[str, object], None] | None = None,
                  log_handler: logging.Handler | None = None) -> int:
    from .browser import BrowserSession
    from .chatgpt import ChatGPTPage
    from .task_runner import TaskRunner
    from .notify import notify
    emit = on_event or (lambda *_: None)
    run_dir, logger = create_run(config.log_dir, resume_path.parent if resume_path else None, log_handler)
    state = StateManager(run_dir / 'status.json', config, bool(resume_path),
                         lambda data: emit('state', data))
    emit('run_dir', str(run_dir))
    logger.info('任务启动；任务=%s；对话=%s；步骤数量=%s；恢复=%s', config.task_name, config.chat_url, len(config.steps), bool(resume_path))
    try:
        emit('phase', '正在打开浏览器；请在浏览器中手动登录')
        async with BrowserSession(config, logger) as page:
            chat = ChatGPTPage(page, config.chat_url, logger, run_dir)
            try:
                logger.info('当前时间：%s；计划时间：%s', timestamp(), config.start_time.isoformat())
                await chat.wait_accessible(config.settings.login_timeout_seconds)
                emit('phase', '等待计划时间')
                await wait_for_start(config, logger.info)
                emit('phase', '任务执行中')
                success = await TaskRunner(config, chat, state, logger).run()
            except asyncio.CancelledError:
                # Cancellation must reach context.close promptly; do not add diagnostic waits.
                raise
            except BaseException:
                await chat.diagnostics('unexpected')
                raise
            if on_event is None:
                notify(success)
            return 0 if success else 1
    except BaseException as exc:
        if state.data['status'] not in {'FINISHED', 'FINISHED_WITH_ERRORS'}:
            state.data.update(status='PAUSED', last_error=f'{type(exc).__name__}: {exc}', paused_at=timestamp())
            state.save()
        logger.error('任务已停止，断点已保存：%s', '用户停止' if isinstance(exc, asyncio.CancelledError) else exc)
        raise
    finally:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
