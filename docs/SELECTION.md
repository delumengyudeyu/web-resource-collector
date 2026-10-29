# 选型与源码验证（2026-10-29）

目标是跨网站发现信息、保存原始资源并提供稳定的本地 API；中文低年级教育内容是验收场景，不限制核心能力。

| 候选 | 核查结果 | 本次决定 |
|---|---|---|
| [ArchiveBox](https://github.com/ArchiveBox/ArchiveBox) | 源码克隆于 `eeedec4d865bc6ed0d89282de969861b1d2a5c94`。本次 checkout 标注 MIT。README 明确不正式支持无 Docker/WSL 的原生 Windows；项目包含归档管理、浏览器和下载插件。 | 本机未安装 Docker、WSL 不可用，保留研究副本，不改动操作系统安装虚拟化环境。 |
| [abx-dl](https://github.com/ArchiveBox/abx-dl) | checkout `03a762b55c3f9cbd2c7aa7c67351815307c3d6c1`，版本 1.13.296，MIT。按仓库要求用 uv 安装并运行帮助；实际失败：`abxpkg/binprovider.py` 导入 `pwd`，Windows 不支持。执行模块也使用 `os.killpg` 和 Unix 信号。 | 不把未能启动的组件列为已部署。保留源码与本地失败日志。 |
| [Crawlee](https://github.com/apify/crawlee) / [Crawl4AI](https://github.com/unclecode/crawl4ai) | 官方说明支持网页、动态页面和结构化提取，但本任务仍需要补齐多媒体下载、任意附件保存、四阶段状态和本地接口。 | 可作为后续可替换适配器，本版不引入第二套爬虫调度。未声称部署验证。 |
| [Firecrawl](https://docs.firecrawl.dev/contributing/self-host) | 自托管默认功能与云端不同，默认栈不提供全部页面动作和高级抓取能力。 | 当前 Windows 环境中不是最省依赖的路线。未安装。 |

最终方案：独立的 WebCollector 集成项目，复用 Playwright 浏览器、HTTPX 下载、BeautifulSoup 链接发现、Trafilatura 正文提取、yt-dlp 媒体提取、gallery-dl 图集提取、pypdf/python-pptx/python-docx 文件解析。自写部分是任务管理、预算控制、适配连接、元数据和 API，不重写这些组件的核心能力。

采集适配器通过统一任务 API 使用，额外站点发现规则可由 `webcollector.discovery` Python entry point 扩展。不要把某个插件支持的网站名单当成系统整体的限制。

已安装版本及随包许可证在 `third_party/dependencies.json` 和相邻目录。集成代码采用 MIT，依赖保持各自许可证；尤其 gallery-dl 的 GPL 条款不会因从子进程调用就自动消失。本仓库不复制这些项目的实现代码、不分发运行环境或素材。未来若制作安装包或对外提供服务，应按实际依赖、组合和分发方式继续审查。
