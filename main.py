import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from app.config import load_config
from app.errors import AutomationError
from app.state_manager import StateManager, RunLock, unfinished, timestamp
from app.runtime import execute


def recovery_choice(config, mode: str):
    pending = unfinished(config)
    if not pending:
        if mode == 'resume':
            raise ValueError('没有可恢复任务')
        return None
    if len(pending) > 1:
        raise ValueError('同一对话有多个未完成任务，请先人工核对：' + ', '.join(str(p) for p in pending))
    path = pending[0]
    data = json.loads(path.read_text(encoding='utf-8'))
    print(f'检测到未完成任务：{path}')
    for sid, record in data['steps'].items():
        print(f"  Step {sid}: {record['status']}")
    choice = {'resume': '1', 'restart': '2', 'exit': '3'}.get(mode)
    if choice is None:
        if not sys.stdin.isatty():
            raise ValueError('无人值守模式发现断点，停止；请指定 --recovery resume 或交互运行')
        choice = input('1. 从断点恢复（不重发）\n2. 从头重新执行（可能重复已有提示词）\n3. 退出\n请选择：').strip()
    if choice == '1':
        return path
    if choice == '2':
        if not sys.stdin.isatty() or input('从头执行会再次发送已完成提示词。确认请输入 RESTART：').strip() != 'RESTART':
            raise ValueError('未确认从头执行，未改变断点')
        data.update(status='ABANDONED', abandoned_at=timestamp())
        # Use the same durable writer without replacing existing step records.
        state = object.__new__(StateManager)
        state.path, state.data = path, data
        state.save()
        return None
    raise ValueError('已退出，未发送消息')


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description='ChatGPT 网页串行自动任务（手动登录）')
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parent / 'config.yaml')
    parser.add_argument('--check-config', action='store_true', help='只检查配置，不打开浏览器或发送')
    parser.add_argument('--recovery', choices=['ask', 'resume', 'restart', 'exit'], default='ask')
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        print('=' * 40 + '\n ChatGPT Web Automation\n' + '=' * 40)
        print(f'任务：{config.task_name}\n对话：{config.chat_url}\n步骤数量：{len(config.steps)}\n计划：{config.start_time}')
        if args.check_config:
            print(f'配置格式有效；登录目录：{config.profile_dir}；日志：{config.log_dir}\n此检查不验证对话是否存在或登录是否有效。')
            return 0
        if 'REPLACE_WITH_' in config.chat_url:
            raise ValueError('请先将 config.yaml 的 chat_url 改成自己的真实 ChatGPT 对话链接')
        # Project-local cache prevents a browser download into the default C: cache.
        cache = Path(__file__).resolve().parent / '.browser-cache'
        if cache.is_dir():
            os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(cache))
        with RunLock(config.profile_dir / '.task.lock'):
            resume_path = recovery_choice(config, args.recovery)
            return asyncio.run(execute(config, resume_path))
    except KeyboardInterrupt:
        print('已中断；再次运行可恢复。')
        return 130
    except (AutomationError, ValueError, OSError, ImportError) as exc:
        print(f'无法继续：{exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'运行失败（{type(exc).__name__}）：{exc}；请查看 run.log。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
