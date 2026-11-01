# WebCollector · 跨网站信息与原始资源采集

当前版本 **0.2**：中英工作台、资源库、严格代理、本地 OCR / 中英翻译 / 语音转写及双语字幕。完整升级说明、API 示例、验收与恢复步骤见 [docs/UPGRADE.md](docs/UPGRADE.md)。新增功能有明确验证边界，尤其不是操作系统级防泄漏工具。

原生 Windows 本地服务。输入一个或多个网址，通过统一 API 获取网页正文、图片、原始附件和受支持的音视频。首批实测使用中文绘本、二年级课件和一年级课堂视频，核心不绑定教育网站。

## 已部署的位置

- 项目：`D:\WebCollector\app`
- 数据及 SQLite：`D:\WebCollector\data`
- Python 环境及 Chromium：`D:\WebCollector\runtime`
- 本地登录配置：`D:\WebCollector\profiles`（不进入 Git）
- 上游研究副本及原始验证材料：`D:\WebCollector\research`
- 工作台：<http://127.0.0.1:8765>
- OpenAPI 交互文档：<http://127.0.0.1:8765/docs>

双击 `start.cmd` 启动；或运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\WebCollector\app\start.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File D:\WebCollector\app\stop.ps1
```

本机已注册名为 `WebCollectorLocal` 的按需任务，启动器用它独立运行后台进程；没有自动定时或开机触发。服务仅监听本机，不发布公网。`register-task.ps1` 可以重新注册；没有该任务时启动器使用隐藏后台进程。诊断日志在 `logs`。

系统模式兼容本机代理 DNS 的 `198.18.0.0/15` Fake-IP，通过仓库外 `local-config.json` 适配；Fake-IP 不代表匿名出口。0.2 新任务默认使用已配置的严格代理，配置保存在仓库外 `network-settings.json`。严格模式不使用系统环境代理覆盖，也不设回环直连例外。服务供可信本机用户使用，不是多租户或公网沙箱。

## API 示例

```powershell
$body = @{
  urls = @('https://www.storybookscanada.ca/stories/zh/0112/')
  max_depth = 0
  max_pages = 10
  max_resources = 150
  max_file_mb = 100
  max_total_mb = 500
  browser = $false
} | ConvertTo-Json
$job = Invoke-RestMethod http://127.0.0.1:8765/api/jobs -Method Post -ContentType application/json -Body $body
Invoke-RestMethod "http://127.0.0.1:8765/api/jobs/$($job.id)"
$result = Invoke-RestMethod "http://127.0.0.1:8765/api/jobs/$($job.id)/results"
$file = $result.items | Where-Object downloaded | Select-Object -First 1
Invoke-WebRequest "http://127.0.0.1:8765/api/files/$($file.id)" -OutFile $file.filename
```

| 接口 | 用途 |
|---|---|
| `POST /api/jobs` | 提交一个或多个网址，返回任务 ID |
| `GET /api/jobs`、`GET /api/jobs/{id}` | 列表、进度、统计和错误事件 |
| `GET /api/jobs/{id}/results` | 资源清单、来源、顺序、大小、SHA-256、状态 |
| `GET /api/files/{item_id}` | 原件附件下载 |
| `GET /api/files/{item_id}?text=true` | 提取后的 Markdown |
| `POST /api/jobs/{id}/cancel` | 取消并清理未完成文件，保留已完成文件 |
| `POST /api/jobs/{id}/retry` | 用相同参数新建任务，原任务证据不覆盖 |

每条结果的 `discovered`、`downloaded`、`extracted`、`classified` 独立记录。添加用户标签后 `classified` 为 true，方法记录为 user_tags；不冒充自动教学分类。独立内容处理任务及译文状态通过 processes API 查询。`source_pages` 为来源页，`url` 为发现地址，`final_url` 为重定向终点。媒体由提取器管理分段时，记录来源页和 `resource_url_note`。

动态页面设 `browser=true`；渲染后发现资源并捕获图片、媒体及附件响应。可设置 `download_selectors: ['#download']` 点击明确的下载按钮，支持普通、POST、blob 下载。不会自动遍历所有按钮或提交任意表单。默认抓取最多 3 次滚动，并非无限滚动到底。

`media=true` 调用 yt-dlp；默认 `media_quality='low'` 控制带宽，需要更高清时设 `'best'` 并提高容量限制。FFmpeg 需要可在 PATH 中找到；本机已经安装。每个页面最多提取一个视频，播放列表不整批下载；字幕仅在来源提供时保存。HLS/DASH 原始清单本身与完整视频分别记录。

`gallery=true` 调用 gallery-dl，按其站点支持情况下载；未对所有支持站点逐一验证。普通网页图片由通用下载器处理。

## 范围、限额与登录

- 默认递归深度为 0；增加 `max_depth` 后按 `allowed_domains` 和 `path_prefix` 限定页面。资源文件允许来自 CDN。未填域名时使用输入网址的域名，子域名需明确加入。
- `max_pages`、`max_resources`、`max_requests`、`max_links_per_page` 控制任务范围。链接超出单页限制会记录 `discovery_truncated`。页面、未知类型链接通过响应类型识别，不依赖扩展名白名单。
- `concurrency` 为单任务资源并发，最多同时运行两个任务；`interval_seconds` 控制该任务 HTTP 请求间隔。浏览器页面自己的网络请求、媒体提取器内部请求不能保证相同频率。
- `max_file_mb` 和 `max_total_mb` 限制文件及单任务保存量。HTTP 按块限制；浏览器/媒体进程按间隔监控，临时文件可能短暂超出上限。额外保留至少 512 MB 磁盘余量用于 HTTP 下载；请定期清理历史任务。
- URL 在任务内去重；相同内容 SHA-256 在 HTTP 结果中关联并复用文件。任务之间保留独立证据，不做全库去重。
- 失败自动按 `retries` 重试；服务器重启后，排队任务继续，运行中的任务标为 interrupted，用户可重试。HTTP 下载目前不做跨重启的字节级续传；媒体提取器可能使用自身续传机制，但取消会清理半成品。

需要登录时运行以下命令，在独立浏览器中手动登录，回到终端回车保存；任务传 `profile='school'`。不要将凭据放在 URL 中。

```powershell
D:\WebCollector\runtime\venv\Scripts\python.exe D:\WebCollector\app\scripts\profile.py school https://example.com/login
```

保存的 `storage-state.json` 用于浏览器与 HTTP cookies，`cookies.txt` 用于媒体下载器。会话失效时重新登录。仅支持现有权限内访问，不能保证验证码、付费下载或 DRM 内容可取得。

## 已知限制

- 不承诺覆盖所有网站；支付、登录、网站变动、反自动化响应和缺失下载链接会失败，需要授权登录或适配器。
- 支持任意 HTTP 文件字节保存，不代表每种文件都能解析。当前解析 HTML、文本、PDF、DOCX、PPTX、XLSX；0.2 提供独立 OCR、语音转写及中英翻译任务。旧版 DOC/PPT 与 EPUB 深入解析未覆盖，原件仍保留。模型准确性、截断范围与代理限制见升级文档。
- 浏览器渲染失败会保留原 HTML 并写 `browser_error`；附件返回 HTML 时明确标为失败。页面快照和原文档不同，本项目不将截图或网页转 PDF 充当教材原件。
- 本机服务不执行下载的附件。页面脚本运行在 Chromium；不要把它暴露给不受信任的公网用户。
- 采集到的素材不随项目上传 GitHub。测试中文绘本标注 CC BY-NC 3.0，不代表获得商业产品使用授权。

## 安装、维护与扩展

当前环境使用 Python 3.12.14，虚拟环境在 D 盘，基础解释器来自现有 Codex runtime。另机部署需安装 Python 3.12，然后运行 `install.ps1 -PythonExe <python.exe路径>`。锁定版本见 `requirements.lock.txt`。升级前停服务、备份 `data` 和 `profiles`，更新依赖后运行测试和实际来源验收，再启动。

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH='D:\WebCollector\runtime\browsers'
Set-Location D:\WebCollector\app
..\runtime\venv\Scripts\python.exe -m pytest -q
```

新网站可提供 `webcollector.discovery` entry point。函数 `(html, url) -> list[dict]` 返回 `url/role/label/order`，参考 `collector/plugins.py`。仅安装可信插件，插件是本机 Python 代码。登录状态和签名 URL 只能保留在本地数据目录。

[选型与源码验证](docs/SELECTION.md) · [真实测试记录](docs/VERIFICATION.md) · [依赖与许可证](third_party/dependencies.json)
