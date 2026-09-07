import unittest
from app.prompt_import import split_prompts


class PromptImportTests(unittest.TestCase):
    def test_chinese_step_headings_keep_headings_and_content(self):
        text = '第一步：分析\n请分析问题。\n\n第二步：继续\n请细化。'
        parts = split_prompts(text)
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0].prompt, '第一步：分析\n请分析问题。')
        self.assertEqual(parts[1].prompt, '第二步：继续\n请细化。')

    def test_first_second_on_separate_lines(self):
        parts = split_prompts('第一、请分析这个问题\n第二、请给出建议')
        self.assertEqual(len(parts), 2)

    def test_first_time_in_prose_does_not_split(self):
        text = '第一次使用这个程序。\n第二天再来。'
        self.assertEqual([part.prompt for part in split_prompts(text)], [text])

    def test_code_fence_heading_does_not_split(self):
        text = '第一步\n代码：\n```text\n第二步\n```\n第二步\n真正的第二步'
        self.assertEqual(len(split_prompts(text)), 2)
        self.assertIn('```text\n第二步\n```', split_prompts(text)[0].prompt)

    def test_preamble_is_preserved(self):
        parts = split_prompts('请用中文回答。\n第一\n分析\n第二\n总结')
        self.assertTrue(parts[0].prompt.startswith('请用中文回答。'))

    def test_duplicate_or_missing_number_is_rejected(self):
        for text in ('第一\na\n第三\nb', '第二步\na', '第一步\na\n第一步\nb'):
            with self.assertRaises(ValueError):
                split_prompts(text)

    def test_bom_crlf_and_markdown_titles(self):
        parts = split_prompts('\ufeff## 第1步：分析\r\nhello\r\n## 第2步：总结\r\nworld')
        self.assertEqual(len(parts), 2)
        self.assertIn('hello', parts[0].prompt)

    def test_empty_file_is_rejected(self):
        with self.assertRaises(ValueError):
            split_prompts(' \n ')
