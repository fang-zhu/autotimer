"""Loss-conscious form serialization; external prompt files are never overwritten."""
import os
import shutil
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import yaml
from .config import Config, load_config, parse_config


@dataclass
class PromptDraft:
    id: str
    name: str
    prompt: str
    timeout: str = ''
    stable: str = ''
    delay: str = ''
    source_file: str | None = None
    source_prompt: str = ''

    def to_mapping(self) -> dict:
        result = {'id': self.id, 'name': self.name}
        if self.source_file and self.prompt.strip() == self.source_prompt.strip():
            result['prompt_file'] = self.source_file
        else:
            result['prompt'] = self.prompt.strip()
        for key, value in (('timeout_seconds', self.timeout), ('stable_wait_seconds', self.stable),
                           ('delay_after_response_seconds', self.delay)):
            if value.strip():
                try:
                    result[key] = float(value)
                except ValueError as exc:
                    raise ValueError(f'步骤「{self.name}」的 {key} 必须是数字') from exc
        return result


def read_draft(path: Path) -> tuple[dict, Config, list[PromptDraft]]:
    config = load_config(path)
    raw = yaml.safe_load(path.read_text(encoding='utf-8-sig'))
    steps = []
    for item, step in zip(raw['steps'], config.steps):
        steps.append(PromptDraft(step.id, step.name, step.prompt,
                                 str(item.get('timeout_seconds', '')), str(item.get('stable_wait_seconds', '')),
                                 str(item.get('delay_after_response_seconds', '')), item.get('prompt_file'), step.prompt))
    return deepcopy(raw), config, steps


def save_draft(path: Path, data: dict) -> Config:
    config = parse_config(data, path)
    # Validate entirely before writing either the original config or a prompt file.
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + '.bak'))
    temp = path.with_suffix(path.suffix + '.tmp')
    try:
        with temp.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return config
