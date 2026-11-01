# WebCollector 0.3：质量升级与验收

日期：2026-11-01。实施依据：QUALITY-UPGRADE-TASK.md。状态：已实现并部署本轮核心质量功能；下方部分验证和未验证项保留，不代表原始全部产品愿景已完成。

## 变更与选择依据

保留现有 FastAPI、SQLite JSON 记录、HTTP/Playwright 工作流和本地处理器。在原入口内新增 quality.py（候选策略）与 media.py（有界子进程、尝试、验证、登记），不迁移到另一套采集平台。既有成熟 yt-dlp 提取器和 FFmpeg 足以解决本轮主要缺口，重新换用 ArchiveBox/Firecrawl 等会扩大迁移范围。本轮没有重新宣称这些平台已经满足全部需求；前轮选型见 UPGRADE.md、原有研究资料。

复核已安装 yt-dlp 2026.10.26 的实际格式输出和参数。官方依据：[格式选择及排序](https://github.com/yt-dlp/yt-dlp#format-selection)、[FFmpeg streamcopy](https://ffmpeg.org/ffmpeg.html#Streamcopy)。使用现有锁定依赖，没有新添商业服务、下载来源不明插件或关闭 TLS。保留 third_party/ 下原有许可证材料；新增代码为本项目修改，无复制第三方源码。

- 默认 best_available；支持 cap_1080、cap_720、data_saver，竖屏按短边上限。
- 候选记录格式、尺寸、音轨、预估大小、排除原因；失败可有界降级。预算高于画质优先级，默认 100 MB/500 MB 不变。
- 兼容旧 low/best；新旧冲突返回 422；旧任务重试保持旧策略。设置只影响后续任务。
- HLS/DASH 使用原生下载器，经代理下载，本地无损合并。正式登记前验证流、时长、尺寸，拒绝缺片和损坏文件。source_streams_remux 不冒充源容器字节一致。
- 进度来自子进程阶段和磁盘占用；取消会终止子进程树并等待退出。临时网络或代理故障停止降级，不尝试直连。
- 图片发现优先 original/lazy/srcset，记录实际像素、同元素关联及页序；不同裁切不删除。PDF/PPTX 等仍保留原格式，不打视频清晰度标签。
- 工作台保留现有布局，增加质量策略、预算、默认值、实际质量、尝试原因、质量统计和“调整设置后重试”；中英文和三个视口验证。

## 本地使用与 API

工作台 http://127.0.0.1:8765 ，API 文档 http://127.0.0.1:8765/docs 。启动/停止：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\WebCollector\app\start.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File D:\WebCollector\app\stop.ps1
```

下例明确提高单次验收预算，不改变用户默认预算。代理名称须是已配置真实代理。

```powershell
$body = @{
 urls=@('https://www.bilibili.com/video/BV1rG411r7BQ/')
 media=$true
 quality_policy='best_available'
 fallback_enabled=$true
 max_format_attempts=5
 media_timeout_seconds=1800
 network_mode='strict'
 proxy_ids=@('local-system')
 max_file_mb=1000
 max_total_mb=2500
 max_links_per_page=1
} | ConvertTo-Json
$job=Invoke-RestMethod http://127.0.0.1:8765/api/jobs -Method Post -ContentType application/json -Body $body
Invoke-RestMethod "http://127.0.0.1:8765/api/jobs/$($job.id)"
$result=Invoke-RestMethod "http://127.0.0.1:8765/api/jobs/$($job.id)/results"
$file=$result.items | Where-Object { $_.downloaded -and $_.adapter -eq 'yt_dlp' } | Select-Object -First 1
Invoke-WebRequest "http://127.0.0.1:8765/api/files/$($file.id)" -OutFile $file.filename
# 取消：POST /api/jobs/{id}/cancel；同参数新建重试：POST /api/jobs/{id}/retry
```

设置使用 PATCH /api/settings：default_quality_policy、default_fallback_enabled；保持其他设置原值。质量在结果 quality 字段内，原有 discovered/downloaded/extracted/classified 不变。best_discovered 表示提取器当前候选中选择并已探测文件，不能证明网站或账号不存在未发现的更佳版本。日常 full_decode=not_run；本次独立完整解码证据见下方，不伪造日常已执行全片解码。

## 实际验收

环境：Windows / D 盘，Python 3.12.14，yt-dlp 2026.10.26，现有 FFmpeg/ffprobe，已配置 HTTP 代理。视频验收没有导入会员 Cookie。原始素材、候选及日志保留于 D:\WebCollector\research 和 data，未提交 Git。

| 验收 | 结果及证据 |
|---|---|
| 原有功能回归 + 新质量测试 | 通过：24 passed in 37.82s；pytest -q；pip check 无依赖冲突 |
| 4K/1080/720 选择 | 通过：真实 HTTP 服务上的确定性 HLS 素材，默认 2160、cap_720 为 720；不是公共网站 4K 证明 |
| 顶级链接缺片、关闭降级 | 通过：坏 4K 分片转 1080，记录两次尝试；关闭降级不登记媒体 |
| 分离音视频 | 通过：DASH 两轨真实下载、合并、ffprobe 确认 1080 与 AAC |
| 代理错误和临时 503 | 通过限定路径：HTTP/浏览器代理停止测试；媒体代理分片阶段返回 503，不保存视频，不换直连，不继续较低候选；未作操作系统全流量捕包 |
| 损坏媒体、取消 | 通过：假 MP4 被拒；真实子进程取消后 PID 不存活 |
| 预算、DRM、协议、旧字段 | 通过策略测试；未知大小超额和磁盘满全场景压力测试未完成 |
| 中文高清讲播视频 | 通过：Bilibili 人教版一年级《春夏秋冬》，任务 77c3df5d28e24fb2a10c42f8587a18f3，格式 30080+30280，1920×1080、30 fps、H.264+AAC、635.274 秒、216350686 字节；全片 FFmpeg -xerror 解码成功，API 文件 SHA-256 一致 |
| 中文绘本 | 通过本样本：Storybooks Canada 中文 0112，任务 73b9232feb6c4ddaa9a29f0630063413；58 项保存；正文 01.jpg–09.jpg 为 546×546、可解码，DOM 页序递增；原中文 PDF 10 页、PDF 渲染成功。书册拼版版本 6 页独立保存，不当成缺页 |
| 中文课件原件 | 通过：jsfzzy 原 PDF，任务 49156d928bce4fb5888d117dd76d520d，29 页可渲染；未声称得到 PPTX |
| 动态网页 | 通过：quotes.toscrape.com/js，任务 db775d4449c841929573518e74b52d47，真实浏览器采集完成 |
| 中英 UI | 通过自动检查与截图：1440/768/390，四工作区、中英切换、详情窗口、质量控件可见、页面不横向溢出；没有完整屏幕阅读器审计 |
| 仅低清来源 | 部分通过：央视一年级样本仅返回 480×270；已保存且标注 no_hd_source。另一个央视页 502、芒果页 TLS 错误，如实失败，未关闭证书验证 |
| 翻译/OCR/真实会员权限 | 本轮未重新验收质量；保留旧功能及测试，见 UPGRADE.md，不宣称教学专业准确性或会员权限可突破 |

视频 SHA-256：c90dec1585738603429f91a40c53c86eee518da63eae640f151748551d66b3f9。

证据文件（本地，不上传）：research/quality-evidence.json、quality-hd.json、quality-book.json、quality-lesson.json、quality-dynamic.json；截图 upgrade-desktop.png、quality-tablet.png、upgrade-mobile.png。quality_validate.py 可复核已保存样本的文件哈希、图片解码、PDF 渲染、完整音视频解码及 API 字节。测试只说明这些样本与条件，不承诺全网支持。

## 明确的剩余限制

- 目前同格式重试由 yt-dlp 控制，有指数等待及硬超时；未完成各站 Retry-After 精确遵循验收。401/403 仍不能可靠区分整个会话过期与单格式权限不足；没有通用签名刷新协议。
- HDR/SDR 和语言保留候选信息，默认不转码；排序不能跨编码证明感知质量最优。显式语言参数依赖来源语言元数据，未知语言不猜测。来源未列出的音轨、隐蔽原图和正片/预告差异不能普遍自动判断。
- 图片同元素关联是线索，不能证明不同尺寸是同裁切。当前保留全部版本，最大像素只是组内偏好；自定义绘本页面结构仍需要站点适配器。
- 源数据可能有未知尺寸。严格分辨率上限不接受未知尺寸视频候选；无尺寸来源仍不能宣称高清。任意直接文件没有格式枚举时显示质量未知。
- 轮询限额约 200ms；极快写入可短暂超过配额，结束前再检查。并发任务磁盘全局预留不是事务式容量分配。FFmpeg 合并也占临时空间；预算不足可能导致可下载候选失败。
- 未实现自动完整解码开关、跨重启字节续传、全库哈希去重、完整系统级 DNS/WebRTC/IPv6 防泄漏审计；代理故障测试不等于绝对匿名。格式下载进程内部所有请求不能保证统一的请求计数/频率，已设置提取器 sleep-requests。
- 缺 FFmpeg、字幕单独成功、特殊音轨和权限刷新等完整组合矩阵尚未覆盖；现有验证逻辑拒绝无完整媒体或期望流缺失，但不把未测组合标为通过。
- 当前翻译/OCR/转写仍需人工复核，私人文件不发送外部模型。本轮没有购买服务或更改宿主个性化设置。

## 数据兼容与恢复

本轮不改 SQLite 表结构，新增字段在 JSON 对象内；历史结果不回填未知质量、不批量重下载；新设置有缺省值。旧应用可读取旧核心字段。备份：D:\WebCollector\backups\quality-20261101-111820，含 Git bundle、任务文档、数据库一致备份、profiles 和设置；SQLite integrity_check=ok。

恢复优先仅回退代码到 57a3b162d960f98cac6099f01099d1af548c3909，保留现有 data/profiles，避免丢掉本轮新增资料。操作前停服务，再备份当前代码与仓库外数据。若确需恢复数据库或配置，先将当前目录另存为日期归档，再从上述备份拷贝恢复；不要直接覆盖唯一的最新资料。恢复后启动并检查 /health、旧任务及原件下载。完整旧版环境说明见 UPGRADE.md。

自动测试已统一使用临时 COLLECTOR_HOME。开发早期单独运行质量测试时曾误用真实数据库，留下少量本地测试任务并短暂把一个运行中验收任务标为 interrupted；未删除用户原件，该验收任务随后完成。现已由 tests/conftest.py 在应用导入前隔离测试数据，后续回归不触碰生产库。
