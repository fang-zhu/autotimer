"""Split explicit Chinese step headings; preserve every line of the input."""
import re
from dataclasses import dataclass

HEADING = re.compile(r'^\s{0,3}(?:#{1,6}\s+)?第(?P<number>[一二三四五六七八九十百千零〇两0-9]+)(?:步骤|部分|步|段|条|轮)?(?:[、.．:：)）\s]+.*)?$')


@dataclass(frozen=True)
class ImportedPrompt:
    name: str
    prompt: str


def chinese_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
    units = {'十': 10, '百': 100, '千': 1000}
    total, digit = 0, 0
    for char in value:
        if char in digits:
            digit = digits[char]
        else:
            total += (digit or 1) * units[char]
            digit = 0
    return total + digit


def split_prompts(text: str) -> list[ImportedPrompt]:
    text = text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text:
        raise ValueError('TXT 文件为空，请先填写提示词。')
    lines = text.splitlines()
    positions: list[tuple[int, int]] = []
    fence = None
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(('```', '~~~')):
            token = stripped[:3]
            if fence is None:
                fence = token
            elif token == fence:
                fence = None
            continue
        if fence:
            continue
        match = HEADING.fullmatch(line)
        if match:
            positions.append((index, chinese_number(match['number'])))
    if not positions:
        return [ImportedPrompt(lines[0][:60], text)]
    numbers = [number for _, number in positions]
    if numbers != list(range(1, len(numbers)+1)):
        raise ValueError('分步编号应从第一开始，依次为第二、第三……，不能重复或跳号。请检查 TXT 中单独成行的标题。')
    prompts = []
    for index, (position, _) in enumerate(positions):
        start = 0 if index == 0 else position  # Keep introductory instructions in step one.
        end = positions[index+1][0] if index+1 < len(positions) else len(lines)
        prompts.append(ImportedPrompt(lines[position].strip().lstrip('#').strip()[:60], '\n'.join(lines[start:end]).strip()))
    return prompts
