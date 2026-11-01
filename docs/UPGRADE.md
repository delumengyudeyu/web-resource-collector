# WebCollector 0.2 升级交付

交付复核日期：2026-11-01（Asia/Tokyo）。首次真实采集与回归记录发生在 2026-10-29/30，证据保存在本机 research 目录。

## 使用

启动 `D:\WebCollector\app\start.cmd`，访问 http://127.0.0.1:8765 。停止：PowerShell 执行 `D:\WebCollector\app\stop.ps1`。服务沿用原来的按需计划任务，没有新增开机启动或定时采集。

四个工作区：新建采集、任务管理、资源库、设置。右上角切换中英文。资源库支持搜索、类型/成功状态筛选、原件预览、手动标签；查看资源后可提取文字、OCR、转写及翻译。扫描件先 OCR，视频先转写，再在翻译来源中选择对应结果。输出保留页码或时间轴，支持 JSON、Markdown、原文/译文/双语 SRT。不会修改原文件。

本机原有系统代理端口经实际请求验证后保存为 `local-system`，默认新任务严格代理。代理关闭时严格任务失败，不自动直连。设置页仅返回代理 ID，不回显地址中的凭据。登录窗口由用户点击打开，手动完成登录后保存，严格模式的会话绑定代理 ID。Cookie 有效期检查仅提示可能过期，不能证明网站授权有效。

## 本次改动

- `collector/network.py`：HTTP CONNECT 代理池、任务/域名稳定选择、启动前探测、会话绑定、明确禁止直连回退；HTTPX 禁用环境代理覆盖，Chromium 禁用 QUIC、非代理 WebRTC UDP 和本地目标 DNS 解析。
- `collector/processing.py`、`process_worker.py`：SQLite 持久化内容处理队列，独立本地子进程，取消、重试和重启中断标记；使用 RapidOCR、CTranslate2 + Argos 中英模型、faster-whisper tiny。固定 PyAV 16.1.0，解决 19.0.2 与 faster-whisper 1.2.1 的 metadata_errors 参数不兼容。
- `collector/profiles.py`：隔离浏览器登录和 Cookie 导出；不导入日常浏览器。
- `collector/api.py`：兼容旧端点，新增资源库、标签、代理、会话、内容处理及安全类型预览；输入校验响应移除原始输入，避免回显代理密码。
- `collector/ui-v2.html`：中英工作台、资源查看与双语阅读。参考 Zotero 的列表/详情工作流，自写界面；不复制品牌资源或 UI 源码。
- `collector/content.py`：增加 DOCX 表格和 XLSX 内容提取。旧格式不能解析时仍保留原件。

## 选型依据

| 项目 | 实际核查 | 决策 |
|---|---|---|
| [ArchiveBox](https://github.com/ArchiveBox/ArchiveBox) | 更新读取 dev 的 README 与 pyproject：0.9.78rc30，Python >=3.13，MIT；本机采集环境为 Python 3.12。 | 继续保留研究副本；不为迁移引入 Docker/WSL 与第二套数据库。 |
| [abx-dl](https://github.com/ArchiveBox/abx-dl) | 重新获取 main `9af0afc`，1.13.300；源码仍有 Unix 进程组操作，本机原依赖 abxpkg 仍直接 import pwd。新版本没有在本机重新完成运行验证。 | 不声称 Windows 不可能支持，但目前没有足够证据替换已经运行的核心。 |
| [Scrapling](https://github.com/D4Vinci/Scrapling) | 读取 README、pyproject、许可证和 fetchers/requests.py；BSD-3-Clause，静态抓取基于 curl_cffi，提供同步/异步 fetcher。GitHub 推送记录 2026-10-29。 | 有价值的后续站点适配器；本次无需增加另一套网络栈。 |
| [Crawlee Python](https://github.com/apify/crawlee-python) | 1.11.1、Python >=3.10、Apache-2.0；读取官方代理示例，支持自有代理池。推送记录 2026-10-29。 | 调度能力成熟，但仍需自行整合原件、媒体和派生内容；暂不迁移。 |
| [Firecrawl](https://docs.firecrawl.dev/contributing/self-host) | AGPL-3.0，官方自托管说明强调与云端能力差异；读取部署说明和仓库 README。 | 不将云端承诺当成本机能力，不引入其完整服务栈。 |
| [LibreTranslate](https://github.com/LibreTranslate/LibreTranslate) / [Argos](https://github.com/argosopentech/argos-translate) | LibreTranslate AGPL-3.0、推送记录 2026-10-26；自托管翻译服务仍需模型。 | 使用 Argos 模型包配合 CTranslate2 本地推理，避免新增独立 Web 服务。模型与运行时不上传仓库。 |

依赖许可证副本及版本清单位于 `third_party/`。集成代码 MIT 不改变依赖各自的许可条件。模型下载来源、大小与哈希保存在 `runtime/models/manifest.json`；重新分发模型或产品打包前需按具体模型条款复核，当前不分发权重。首次安装运行 `install.ps1 -PythonExe <Python3.12路径>` 后，显式运行 `runtime\venv\Scripts\python.exe app\scripts\setup_models.py` 下载模型。运行时不自动下载模型或调用收费接口。

## API 示例

```powershell
$base = 'http://127.0.0.1:8765'
$job = Invoke-RestMethod "$base/api/jobs" -Method Post -ContentType 'application/json' -Body '{"urls":["https://www.storybookscanada.ca/stories/zh/0112/"],"network_mode":"strict","proxy_ids":["local-system"],"max_depth":0,"max_resources":150}'
Invoke-RestMethod "$base/api/jobs/$($job.id)"
$results = Invoke-RestMethod "$base/api/jobs/$($job.id)/results"
$item = $results.items | Where-Object downloaded | Select-Object -First 1
$work = Invoke-RestMethod "$base/api/items/$($item.id)/process" -Method Post -ContentType 'application/json' -Body '{"action":"translate","source_language":"zh","target_language":"en"}'
Invoke-RestMethod "$base/api/processes/$($work.id)"
# 完成后 GET /api/processes/{id}/files/json 或 /files/text
# OCR: action=ocr；转写: action=transcribe；后续翻译传 source_process_id
# 取消: POST /api/processes/{id}/cancel；重试: POST /api/processes/{id}/retry
```

完整参数与所有兼容端点见 http://127.0.0.1:8765/docs 。采集与内容处理任务分别持久化；重试生成新任务，保留旧证据。旧任务查询不受默认严格模式影响；旧任务重试时若未明确网络模式会采用当前默认值。

## 验收结果与限制

已通过：共 11 项自动化测试，含原有 8 项回归、HTTP 与浏览器代理转发及代理关闭后失败、配置凭据不回显、资源搜索/标签/真实 PPTX 内容处理；中英四个工作区和资源弹窗、1440×1000 与 390×844 无页面溢出和 JS 错误。依赖检查 pip check 通过。

严格代理真实站点：中文绘本发现 87 项、下载 58 项、提取 15 项；原 PDF 10 页、9 张故事图片顺序 01–09 均可打开；二年级课件 PDF 29 页；动态页面下载 4 项；央视讲播视频保存 MP4，完整 FFmpeg 解码成功。所有原件重新计算 SHA-256，API PDF 下载哈希一致。此次样本均无失败资源。PDF 课件未冒充 PPTX；中文教学 PPTX 原件仍缺真实站点成功样本。

本地处理：绘本 PDF 中译英完成；前两页 OCR 正确标记部分结果；视频前 45 秒产生 8 个转写片段，后续翻译生成三种 SRT。完整视频下载验证不等于完整视频转写验证。轻量模型转写可能误识别姓名，翻译发现省略主语和不自然表达；全部标记需要复核，新增纯数字行保留规则。不是专业教材翻译或版式重排工具。

未验证或尚有限制：

- 没有真实会员账号，会员站授权及登录过期后的站点行为未验收。Cookie 保存成功不等于会员权限成功。
- 严格模式为应用级策略，不是 OS 防火墙。没有完整抓包证明所有 DNS/IPv6/WebRTC/插件子进程均无泄漏；代理解析目标域名时，私网目的地控制也依赖可信代理。不能称为绝对匿名或零泄漏。
- 严格模式禁止未经验证的 gallery-dl 子进程；仍可用 HTTP/浏览器采集页面图片。yt-dlp 限制为原生 HTTP/HLS/DASH 协议，其他协议失败而非直连。
- 代理故障切换发生于任务启动；登录会话固定出口。运行中代理失效会失败，不跨出口悄悄恢复。
- HTTP 中断重试重新下载，无跨重启字节级断点续传；已排队任务恢复，运行中任务标记中断后可重试。哈希去重仍限单个采集任务。
- 总存储预算按周期统计，浏览器/媒体与并发写入存在短暂超量窗口，不是文件系统硬配额。
- 文档翻译保留页码/段落或时间轴，不保证所有表格布局、公式或嵌入图片排版。没有 OCR 文本的图像仍保留原件；不解析的格式不冒充解析成功。
- 用户界面支持中英切换；底层异常及技术日志保留原始英文，避免丢失排障信息。

## 数据兼容与恢复

升级前备份目录 `D:\WebCollector\backups\20261029-202316` 包含原始 Git bundle、data 与 profiles（若存在）及本地配置。SQLite 使用备份 API 建立一致副本，integrity_check 返回 ok。新版本仅新增 processes 表及 JSON 字段，旧 jobs/items 表结构保持兼容，未删除已有文件。

恢复时先停止服务，再把当前 app/data/profiles 保存到另一个备份目录。将 source.bundle 克隆到一个新目录进行旧版验证；从备份复制 data/profiles 到独立恢复目录，确认完整后再切换。不要在运行中的数据库上覆盖文件，也不要直接 git reset --hard 丢弃本地修改。恢复旧源码时可保留新增 processes 表，旧版会忽略它；若需要精确旧状态则使用一致性数据库备份及匹配文件副本。
