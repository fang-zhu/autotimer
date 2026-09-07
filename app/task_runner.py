import asyncio
import logging
from datetime import datetime
from time import monotonic
from playwright.async_api import Error as PlaywrightError
from .chatgpt import ChatGPTPage
from .config import Config, Step
from .errors import AutomationError, PageUnavailable, ResponseTimeout, UnsafeState
from .response_monitor import wait_response
from .state_manager import StateManager, timestamp


class TaskRunner:
    def __init__(self, config: Config, chat: ChatGPTPage, state: StateManager, logger: logging.Logger):
        self.config, self.chat, self.state, self.logger = config, chat, state, logger

    async def send(self, step: Step) -> dict:
        record = self.state.data['steps'][step.id]
        if record['click_intent']:
            baseline = record['baseline']
            self.logger.info('步骤 %s 已存在发送意图；仅核对，不重发', step.id)
            await self.chat.verify_sent(baseline, step.prompt, self.config.settings.send_verification_timeout_seconds)
            self.state.update(step.id, 'WAITING_RESPONSE', verified_at=record.get('verified_at') or timestamp())
            return baseline
        settings = self.config.settings
        while record.get('preparation_attempts', 0) < settings.retry_count + 1:
            attempt = record.get('preparation_attempts', 0) + 1
            self.state.update(step.id, 'SENDING', preparation_attempts=attempt)
            try:
                before = await self.chat.wait_ready(settings.login_timeout_seconds, step.stable_wait_seconds)
                baseline = {'users': list(before.users), 'assistants': list(before.assistants)}
                await self.chat.prepare(step.prompt)
                check = await self.chat.snapshot()
                if check.users != before.users or check.assistants != before.assistants or check.busy:
                    raise UnsafeState('准备发送时对话发生变化，暂停核对')
            except (PageUnavailable, PlaywrightError) as exc:
                self.logger.warning('发送前准备失败 %s/%s：%s', attempt, settings.retry_count + 1, exc)
                await self.chat.diagnostics('prepare')
                if attempt > settings.retry_count:
                    raise PageUnavailable('发送前准备重试次数耗尽') from exc
                await asyncio.sleep(settings.retry_interval_seconds)
                continue
            # Persist before the external side effect, never after it.
            self.state.update(step.id, 'SENDING', baseline=baseline, click_intent=True, sent_at=timestamp())
            self.logger.info('发送前 Step %s：%s；摘要=%r', step.id, step.name, step.prompt[:100])
            try:
                await self.chat.click_send()
            except (PageUnavailable, PlaywrightError) as exc:
                self.logger.warning('点击返回异常，核对是否已发送，不再次点击：%s', exc)
            await self.chat.verify_sent(baseline, step.prompt, settings.send_verification_timeout_seconds)
            self.state.update(step.id, 'WAITING_RESPONSE', verified_at=timestamp())
            self.logger.info('发送后 Step %s：已确认输入框清空且新增 user 消息完全匹配', step.id)
            return baseline
        raise PageUnavailable('此前发送前准备次数已耗尽，请检查日志和配置')

    async def monitor(self, step: Step, baseline: dict, timeout: float) -> None:
        record = self.state.data['steps'][step.id]
        def started():
            if not record.get('response_started_at'):
                self.state.update(step.id, 'WAITING_RESPONSE', response_started_at=timestamp())
        def recovery(count):
            record['recovery_attempts'] = count
            self.state.save()
        await wait_response(self.chat, baseline, step.prompt, timeout, step.stable_wait_seconds,
                            self.config.settings.retry_count, self.config.settings.retry_interval_seconds,
                            started, recovery, record.get('recovery_attempts', 0))

    async def run(self) -> bool:
        settings = self.config.settings
        self.state.data.setdefault('actual_start', timestamp())
        self.state.save()
        self.logger.info('Task started；当前模型：%s', await self.chat.model_name())
        for index, step in enumerate(self.config.steps, 1):
            record = self.state.data['steps'][step.id]
            if record['status'] == 'COMPLETED' or (record['status'] == 'FAILED' and record.get('safe_to_continue')):
                self.logger.info('跳过已处理步骤 %s', step.id)
                continue
            self.logger.info('[STEP %s/%s] %s', index, len(self.config.steps), step.name)
            start = monotonic()
            try:
                recovering_send = bool(record['click_intent'])
                baseline = await self.send(step)
                # Resume checks a completed response even after timeout, but does not silently
                # grant another full timeout window to a still-running response.
                elapsed = max(0, (datetime.now().astimezone() - datetime.fromisoformat(record['sent_at'])).total_seconds())
                budget = max(step.stable_wait_seconds + 2 if recovering_send else .001, step.timeout_seconds - elapsed)
                await self.monitor(step, baseline, budget)
                self.state.update(step.id, 'COMPLETED', response_ended_at=timestamp(), duration_seconds=round(monotonic()-start, 3),
                                  total_elapsed_seconds=round((datetime.now().astimezone()-datetime.fromisoformat(record['sent_at'])).total_seconds(), 3))
                self.logger.info('Step %s 回答完成；本次处理耗时 %.1f 秒', step.id, monotonic()-start)
                await asyncio.sleep(step.delay_after_response_seconds)
            except (AutomationError, PlaywrightError) as exc:
                self.logger.exception('Step %s 失败：%s', step.id, exc)
                self.state.update(step.id, 'FAILED', error=str(exc), failed_at=timestamp())
                await self.chat.diagnostics(f'step_{index}_failed')
                safe = not record['click_intent'] and not isinstance(exc, UnsafeState)
                if settings.continue_on_error and isinstance(exc, ResponseTimeout):
                    self.logger.warning('continue_on_error=true：必须先确认当前回答结束，最多额外观察一个步骤超时周期')
                    try:
                        await self.monitor(step, record['baseline'], step.timeout_seconds)
                        safe = True
                    except (AutomationError, PlaywrightError) as drain_error:
                        self.logger.error('当前轮仍未确认结束，停止后续发送：%s', drain_error)
                if settings.continue_on_error and safe:
                    self.state.update(step.id, 'FAILED', safe_to_continue=True)
                    continue
                self.state.data['status'] = 'PAUSED'
                self.state.save()
                self.summary()
                return False
        failures = sum(r['status'] == 'FAILED' for r in self.state.data['steps'].values())
        self.state.data.update(status='FINISHED_WITH_ERRORS' if failures else 'FINISHED', finished_at=timestamp())
        self.state.save()
        self.summary()
        return not failures

    def summary(self) -> None:
        records = list(self.state.data['steps'].values())
        counts = {status: sum(r['status'] == status for r in records) for status in ('COMPLETED', 'FAILED', 'WAITING')}
        counts['PENDING'] = len(records) - counts['COMPLETED'] - counts['FAILED']
        self.state.data['summary'] = counts
        self.state.save()
        self.logger.info('任务结果：%s；成功 %s；失败 %s；未执行 %s；日志 %s',
                         self.state.data['status'], counts['COMPLETED'], counts['FAILED'], counts['PENDING'], self.state.path.parent)
