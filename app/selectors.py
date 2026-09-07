"""All site-specific selectors. Keep fallbacks scoped; never click a generic Retry."""
COMPOSER = ('#prompt-textarea', '[data-testid="prompt-textarea"]',
            'main [contenteditable="true"][role="textbox"]', 'main textarea[placeholder]')
SEND = ('button[data-testid="send-button"]', 'button[aria-label="Send prompt"]',
        'button[aria-label="Send message"]', 'button[aria-label="发送提示"]', 'button[aria-label="发送消息"]')
STOP = ('button[data-testid="stop-button"]', 'button[aria-label*="Stop generating"]',
        'button[aria-label*="停止生成"]', 'button:has-text("Stop generating")', 'button:has-text("停止生成")')
USER = ('[data-message-author-role="user"]', '[data-testid="user-message"]')
ASSISTANT = ('[data-message-author-role="assistant"]', '[data-testid="assistant-message"]')
TURN = '[data-testid^="conversation-turn-"], article'
DONE = ('button[data-testid="copy-turn-action-button"]', 'button[aria-label="Copy response"]',
        'button[aria-label="复制回复"]', 'button[aria-label="Good response"]',
        'button[aria-label="Bad response"]', 'button[aria-label="朗读"]', 'button[aria-label="Read aloud"]')
BUSY = ('[aria-busy="true"]', '[data-is-streaming="true"]', '[data-testid="thinking-indicator"]')
ERROR = ('[role="alert"]', '[data-testid="conversation-error"]', '[data-testid="error-message"]')
ERROR_TEXT = ('something went wrong', 'network error', '发生错误', '网络错误', 'unable to load', '达到上限', 'limit reached')
RETRY = ('main button:text-is("Retry")', 'main button:text-is("Try again")',
         'main button:text-is("重试")', 'main button:text-is("重试回答")')
LOGIN = ('button:has-text("Log in")', 'a:has-text("Log in")', 'button:has-text("登录")',
         'iframe[title*="challenge"]', 'input[type="password"]')
MODEL = ('button[data-testid="model-switcher-dropdown-button"]', '[data-testid="model-selector"]')
