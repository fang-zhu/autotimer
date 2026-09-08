# ChatGPT Web Automation（Windows）

使用 Python + Playwright 操作你本人登录的 ChatGPT 网页，在指定时间按顺序发送任意数量的提示词。**不用 OpenAI API，不保存或填写账号密码，不绕过验证码或安全验证。**

现在提供 Windows 桌面窗口，同时保留命令行入口。默认停止处理不确定状态，优先降低重复发送风险。

**桌面版：双击 `start_gui.bat`，或运行 `.venv\Scripts\python.exe gui.py`。** 窗口支持直接填写链接和时间、编辑/排序提示词、TXT 自动分步导入、实时日志与断点恢复。打开窗口不会自动发送。

新增的“浏览器与登录”页支持连接已经开启本机调试端口的 Chrome/Edge，停止后保留该浏览器。普通方式打开的标签不能直接接管；首次使用独立登录目录仍可能需要登录，程序不能绕过人机验证。

完整桌面操作和 TXT 格式见 **[桌面版使用说明](docs/gui.md)**。下文原有登录流程适用于“程序打开专用窗口”模式。

## 1. 安装和启动

需要 Windows、Python 3.11 或更新版本、正常网络连接。本项目实际测试环境为 Windows / Python 3.14.6；未逐个验证所有 Python 版本。

仓库提供源代码和 `config.example.yaml`，不包含个人配置、登录数据、日志、Python 虚拟环境或浏览器安装包。第一次下载后，先在项目目录执行以下命令生成自己的配置（已存在时不要覆盖）：

```powershell
if (!(Test-Path config.yaml)) { Copy-Item config.example.yaml config.yaml }
```

示例默认使用 Edge 连接模式，需先在“浏览器与登录”页打开可连接浏览器并手动登录。想使用程序自带的 Chromium 窗口，可在该页切换模式。

在 PowerShell 中执行：

```powershell
cd D:\autotimer
python -m venv .venv
.venv\Scripts\activate
pip install --no-cache-dir -r requirements.txt

# Chromium 浏览器体积超过 100 MB；把下载、缓存和临时文件放在 D 盘。
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) '.browser-cache'
$env:TEMP = Join-Path (Get-Location) '.download-temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
playwright install chromium

# 打开桌面界面，填写自己的对话链接和提示词。
python gui.py
# 也可以使用命令行：python main.py
```

如果 PowerShell 不允许激活虚拟环境，无需修改系统安全设置，用完整路径代替：

```powershell
.venv\Scripts\python.exe -m pip install --no-cache-dir -r requirements.txt
# 先设置上面的 PLAYWRIGHT_BROWSERS_PATH、TEMP、TMP。
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe main.py
```

虚拟环境就是项目自己的 Python 和依赖目录，不会替换系统 Python。GitHub 下载的项目需要按上述步骤安装，不能假定已经存在 `.venv` 和 `.browser-cache`。移动项目后建议重新创建虚拟环境；已有断点还需要迁移处理，见后文。

程序检测到项目内 `.browser-cache` 后会自动使用它；显式设置的 `PLAYWRIGHT_BROWSERS_PATH` 优先。浏览器关闭后登录目录仍保留。运行、日志、浏览器登录文件均默认写在项目内。

## 2. 第一次登录、选择对话

1. 在你平时的浏览器中手动登录 ChatGPT，新建一个普通文本对话，先发一句话，等回答结束。
2. 复制地址栏中类似 `https://chatgpt.com/c/真实ID` 的完整地址，填入 `config.yaml` 的 `chat_url`。不能使用首页、共享链接或示例占位 ID。
3. 根据需要修改提示词和开始时间，再运行程序。
4. 程序会打开**独立 Chromium 窗口**。首次使用时请在此窗口内手动登录同一个账号；如登录后停留在首页，手动粘贴配置的对话链接。
5. 程序每秒检查页面；登录完成且目标对话可识别后自动继续。默认给你 900 秒登录，可修改 `login_timeout_seconds`。

Persistent Context 的意思是“保留登录状态的浏览器窗口”，数据位于 `browser_data/`。它保存网站 Cookie 等会话数据，不保存你的账号密码表。**它不能直接接管你平时 Chrome/Edge 中已经打开的普通标签页**；以后可复用程序自己的登录状态。

网页要求验证码、登录或安全确认时，请本人完成。不要把 `browser_data/` 发给别人或提交 Git，其中的登录状态可能允许访问你的账号。程序执行期间不要在同一对话的任何窗口里手动发送、编辑消息、切换分支或模型。

## 3. 修改时间和提示词

打开 [config.yaml](config.yaml)：

```yaml
task_name: "论文资料整理"
chat_url: "https://chatgpt.com/c/你的真实ID"
start_time: "2026-09-08 08:30:00"
start_immediately: false
```

没有时区的时间按 Windows 本地时区解释；也支持 `"2026-09-08T08:30:00+08:00"`。倒计时按完整日期计算，可跨天。已经超过开始时间时会记录提示并立即执行。`start_immediately: true` 忽略计划时间。

配置在每次启动时读取。修改运行中的配置不会热更新；要改变排队中的任务，请按 `Ctrl+C` 停止，修改后重新运行并选择恢复。只调整时间不会失去断点；修改提示词、步骤顺序/ID、任务名称、对话或登录目录会使旧断点不匹配，程序拒绝悄悄继续。

默认提示词在 `prompts/01.txt`、`02.txt`、`03.txt` 中，直接编辑 UTF-8 文本即可。`steps` 列表从上到下执行，增加或删除列表项即可改变数量：

```yaml
steps:
  - id: 1
    name: "第一轮分析"
    prompt_file: "prompts/01.txt"
  - id: 2
    name: "继续分析"
    prompt: |
      根据上一轮回答，继续完成以下任务：
      请补充三个具体例子。
    timeout_seconds: 3600
    stable_wait_seconds: 8
    delay_after_response_seconds: 5
```

`prompt` 和 `prompt_file` 每步只能选一个。ID 必须唯一，省略时按位置生成。路径相对于配置文件所在目录解析。Windows 绝对路径建议用 `D:/我的提示词/01.txt`，或 YAML 单引号 `'D:\我的提示词\01.txt'`，避免双引号中的反斜杠被当作转义符。

## 4. 等待和错误参数

| 配置字段 | 作用 |
|---|---|
| `response_timeout_seconds` | 每轮回答最长等待，默认 1800 秒；从发送意图时间计算，断线时间也计入 |
| `stable_wait_seconds` | 满足全部完成条件后，内容连续保持不变的秒数，默认 5 |
| `retry_count` | 发送前准备失败的额外重试次数，以及网页错误观察恢复次数，默认 3 |
| `retry_interval_seconds` | 重试准备或下一次错误恢复观察的间隔，默认 10 秒 |
| `send_verification_timeout_seconds` | 点击后核对新增用户消息及输入框清空的期限，默认 30 秒 |
| `login_timeout_seconds` | 等待手动登录、页面可用和发送前上一轮结束的期限，默认 900 秒 |
| `continue_on_error` | 是否在安全情况下跳过失败步骤，默认 false |
| `delay_after_response_seconds` | 确认回答完成后再等几秒，默认 0；示例设为 5 |

每步可覆盖 `timeout_seconds`、`stable_wait_seconds`、`delay_after_response_seconds`，其余使用全局规则。

**`retry_count` 不表示反复点击发送。** 只有尚未记录点击意图的准备阶段能重试；记录意图后，无论点击超时还是网络延迟，都只核对已发送消息。程序不自动点击网页的 Retry/重新生成，因为这可能创建新的回答分支。识别出错误后会有限次数观察恢复并保存截图，仍然异常就暂停。

`continue_on_error: true` 也不会强行打断正在生成的回答。回答超时后，最多再观察一个本步骤超时周期，确认当前轮结束才允许跳过该失败步骤继续；否则暂停。没有点击的准备失败可以跳过，但下一步仍须通过输入框和页面检查，存在草稿时不会覆盖。

断点恢复不会重新赠送完整回答超时；若旧预算已过，仅给一次 `stable_wait_seconds + 2` 秒的完成核对机会。确认消息发送的阶段单独受发送验证超时控制。

## 5. 如何判断回答完成

程序约每秒重新读取网页，要求同时满足：

1. 用户消息列表是发送前历史加上当前提示词，没有额外人工消息。
2. 出现新 assistant 消息，旧回复历史没有变化，最后回复不为空。
3. 没有 Stop generating / 停止生成或已知忙碌标记。
4. 输入框重新可编辑且为空，浏览器在线，没有已识别页面错误。
5. **本轮**回复出现复制、评价等结束操作按钮。
6. 回复内容和消息指纹连续 `stable_wait_seconds` 秒不变。

指纹是文本的摘要，用于比较前后内容是否一致。断线、页面刷新、消息缺失或状态异常会重置稳定计时。轮询间隔和步骤间延迟使用定时等待；**不会固定等待 60 秒就假定回答完成**。

网页不是稳定接口，站点改版、工具调用、自定义 GPT、深度研究中途询问、未加载完整的超长对话、附件输出等可能无法满足这些规则。第一版优先支持普通文本对话；不确定时暂停。这里的多信号检测仍不是服务器提供的完成确认，不能保证所有未来页面状态都正确识别。

## 6. 日志和断点恢复

每次新任务生成单独目录，同一任务恢复时追加到原目录：

```text
logs/2026-09-08_083000_随机后缀/
  run.log
  status.json
  screenshots/
```

`run.log` 记录启动、计划时间、实际开始、每步发送前后、提示词前 100 字、生成开始/结束和异常。`status.json` 记录每步状态、时间、完成 ID、历史指纹和统计。截图可能包含对话内容，请按个人文件保管。截图失败会明确记录，不会伪称已保存。

步骤状态：`WAITING`（等待）、`SENDING`（发送准备/待核对）、`WAITING_RESPONSE`（等待回答）、`COMPLETED`（完成）、`FAILED`（失败）。任务还会标记 `PAUSED`、`FINISHED`、`FINISHED_WITH_ERRORS`。

异常退出后再次运行 `python main.py`，会显示：

```text
1. 从断点恢复（不重发）
2. 从头重新执行（可能重复已有提示词）
3. 退出
```

选 **1** 时，已完成步骤不再执行；有发送意图的步骤仅核对页面，确认发送后接着等回答。选 **2** 后再确认一次，旧任务保留并标为放弃，新任务会重新发送所有步骤。桌面版直接点击确认，不再要求输入 `RESTART`；命令行输入 `y` 确认，默认不重跑。只有你明确要重跑时才这样选。

如果程序在“写入发送意图”与“实际点击”之间崩溃，可能出现其实未发送但程序坚持不重发的情况。请查看网页和日志人工核对；不要随意把 `click_intent` 改为 false。需要重做时建议新建对话，只保留尚需执行的提示词作为新任务，避免重跑已完成工作。

断点索引在 `browser_data/.task-states/`，修改日志目录后仍可找到旧断点。不要删除/搬走未完成任务的状态文件和登录目录，程序遇到缺失或损坏的索引会停止。系统文件锁阻止两个本程序进程同时使用同一登录目录；崩溃后锁自动释放。不要通过换登录目录来同时跑同一对话。

无人值守恢复可使用：

```powershell
python main.py --recovery resume
```

没有可恢复任务会报错；默认非交互运行发现断点会停止，不会替你选择重跑。

## 7. 检查配置、测试和排错

```powershell
python main.py --check-config
python main.py --config "D:/autotimer/config.yaml"
python -m unittest discover -s tests -v
python -m unittest discover -s tests_browser -v
python -m compileall -q main.py app tests tests_browser
```

`--check-config` 只校验字段、提示词文件和格式，不打开网页、不发送，也不证明对话链接真实存在。

单元测试验证逻辑；浏览器测试在真正 Chromium 中运行本地模拟网页，所有请求由测试拦截，不登录或访问真实 ChatGPT。测试临时浏览器文件在项目 `.download-temp/` 中。测试结果和人工验收表见 [docs/testing.md](docs/testing.md)。

selector 是“在网页中找到输入框和按钮的规则”。页面改版导致找不到时，查看日志和截图，集中修改 [app/selectors.py](app/selectors.py)，不要在其他模块写死页面选择器。复杂的消息结构变化也可能需要调整 `app/chatgpt.py` 的消息归属逻辑。

常见情况：

- 找不到浏览器：重新按安装步骤设置 D 盘缓存路径并运行 `playwright install chromium`。
- 找不到输入框：确认已登录、URL 正确、安全验证已完成；仍有问题则检查选择器。
- 一直等回答：可能没有识别到本轮结束按钮或页面处于工具交互中；查看截图，不能以缩短等待时间代替修复。
- 发送状态不确定：不要再次点击或从头运行，先核对网页，再选择恢复。
- 网络恢复后页面仍显示错误：人工处理页面或重新登录后恢复；程序不会规避站点限制。
- 没在计划时间执行：电脑须开机、不睡眠、程序保持运行、网络和登录有效。本程序不是 Windows 后台服务，也不负责唤醒电脑。

完成时会输出结果并播放 Windows 提示音；**未实现系统通知横幅**。专用窗口模式会尝试关闭浏览器会话；连接模式只断开程序连接，保留已有浏览器和标签。退出码 0 表示全部成功，1 表示未全部成功，130 表示手动中断。

## 8. 项目结构与可靠性边界

```text
gui.py                     桌面入口
start_gui.bat              Windows 双击启动
main.py                    命令行入口、恢复选择
config.yaml                用户配置
config.example.yaml        可提交的示例配置（复制后再填写个人信息）
requirements.txt           Python 依赖
app/config.py              配置验证及路径解析
app/gui.py                 窗口、提示词编辑、实时日志
app/gui_config.py          表单配置保存与备份
app/gui_controller.py      后台任务与安全停止
app/runtime.py             桌面/命令行共用执行生命周期
app/prompt_import.py       TXT 中文编号自动分步
app/browser_launcher.py    打开供本人手动登录的可连接浏览器
app/scheduler.py           日期和倒计时
app/browser.py             持久化浏览器
app/selectors.py            页面定位规则
app/chatgpt.py              网页读取、输入、发送验证、截图
app/response_monitor.py     多信号完成检测
app/task_runner.py          串行执行及错误策略
app/state_manager.py        原子断点保存、索引、进程锁
app/logger.py               独立运行日志
app/notify.py               控制台和 Windows 提示音
prompts/                   示例提示词
tests/                     单元测试
tests_browser/             本地网页集成测试
docs/design.md             设计与实施阶段
docs/testing.md            验证结果和真实网页验收方法
```

网页操作没有服务器端“同一请求只处理一次”的机制，因此不能承诺任何崩溃场景下既绝不重复又绝不停顿。本程序选择保守暂停：点击意图先写盘，点击后验证，不确定就停止。删除断点、主动从头运行、人工同时操作或站点 DOM 行为变化仍可能破坏保护。

交付时未使用你的登录状态完成真实 ChatGPT 对话验收。首次请先用一个新测试对话、两段简短提示词、`start_immediately: true` 人工观察，再配置正式任务。

## 9. 移动目录与本次发布

此版本尚未实现完整的目录迁移功能。启动脚本和默认相对路径能跟随目录变化，但 `.venv` 不保证可搬移；断点索引保存状态文件的完整地址，任务指纹包含登录目录的完整地址，外置 TXT 也可能保存完整地址。因此，即使任务已结束，直接移动原目录也可能造成旧状态文件找不到。

移动前请备份、停止任务并关闭相关浏览器；不要删除断点来绕过报错。仅希望换个方便打开的位置时，可给 `start_gui.bat` 创建快捷方式。迁移已有任务应单独处理路径和断点，不属于本次发布已完成的功能。

本次 GitHub 发布包括：Windows 桌面界面、CLI、定时串行发送、多信号回答结束检测、保守防重发、日志与断点恢复、TXT 中文编号分步预览导入、Chrome/Edge 本机连接模式，以及自动测试与使用说明。测试结果与真实网页验证边界见 `docs/testing.md`。本仓库不包含绕过人机验证的实现，也不保证网站改版后仍无需调整选择器。

## 10. 鼠标录制与回放（功能分支）

本次修改前已创建 GitHub 备份标签 `backup/pre-mouse-recorder-20260907`，基于提交 `0bde3ce`；新功能位于 `feature/mouse-recorder-and-restart-dialog`，不直接覆盖 main。

先更新依赖：`.venv\Scripts\python.exe -m pip install -r requirements.txt`，关闭旧程序窗口后重新打开 `start_gui.bat`，进入 **“鼠标录制”** 页。

1. 点击“开始录制”，3 秒内切换到目标窗口。
2. 操作鼠标：支持移动、左右/中键点击、拖动和滚轮，不录制键盘文字。
3. 按 **F8 或 Esc** 停止，点击“保存录制”保存为 JSON 文件。
4. 回放前恢复目标窗口的位置和内容，在“回放次数”和“每次间隔（秒）”中填写参数，然后点击“开始回放”或按 **F9**。
5. 3 秒后按设置的次数回放。每一轮完整结束后才开始计算下一轮间隔，最后一轮结束后不会额外等待；按 **Esc** 可立即停止当前回放并取消后续轮次。停止时会尝试释放回放按住的鼠标按钮。

回放次数可填写 `1~1000`，间隔可填写 `0~86400` 秒。设置会随配置保存到 `config.yaml`，例如：

```yaml
mouse:
  replay_count: 3
  replay_interval_seconds: 5
```

上例会回放 3 轮，每轮结束后等待 5 秒再开始下一轮。回放过程中界面会显示当前轮次和间隔倒计时；Esc 停止后不会自动继续剩余轮次。

开始鼠标任务时自动启用全局快捷键，可在空闲时取消勾选。快捷键不阻止目标软件收到相应按键，不记录普通键盘输入。建议使用快捷键停止录制，避免把点击“停止”按钮本身录进去。

这是按屏幕坐标回放，不会等待网页回答完成，也不会判断按钮是否变化；它独立于 ChatGPT 自动任务，两者不能同时运行。窗口移动、滚动位置或页面内容变化可能造成点错。屏幕整体尺寸或系统缩放变化会拒绝回放；多显示器布局和各屏缩放也应保持不变。先在空白测试窗口试用，不应录制登录、安全验证或具有不可撤销后果的操作。

录制上限为 30 分钟、100000 个事件，鼠标移动最多每秒采样约 30 次。文件只保存事件和显示范围信息，不是屏幕录像，也不包含键盘文字。默认保存到 `recordings/`，该目录已排除 Git 上传。录制含有鼠标位置和操作时间，请按个人文件保管。

本轮自动测试使用模拟输入控制器，不会在实际桌面执行点击；真实目标软件的鼠标回放仍需本人先做小范围验收。

停止录制后若监听器报错，程序会保留已捕获且校验有效的事件，并允许保存；屏幕变化等不安全的数据仍会拒绝回放。界面会区分本次录制、上次保留的录制以及空录制，不再把空录制提示成“可保存”。鼠标停止结果和错误原因记录在 `logs/mouse_recorder.log`，不写入完整鼠标轨迹。
