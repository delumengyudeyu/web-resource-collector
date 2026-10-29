# 验证记录

日期：2026-10-29（北京时间）。所有来源均经本地 API 提交，真实输出保留在 D 盘 data 目录；仓库仅包含报告和验证脚本，不包含采集素材或 Cookie。

## 真实网站与原件验证

| 场景 | 来源 | 结果 |
|---|---|---|
| 中文绘本 | [《我的身体》](https://www.storybookscanada.ca/stories/zh/0112/) | 发现 87 项，下载 58 项，提取 15 项；原始中文 PDF 10 页。9 张故事图片全部能打开，按页面发现顺序对应 01.jpg 至 09.jpg；API 下载所得 PDF 与磁盘 SHA-256 相同。 |
| 中文二年级课件 | [语文园地一 PDF](https://www.jsfzzy.com/storage/course/64dd92d7dad30.pdf) | 下载原 PDF，771131 字节、29 页，可以解析。来源文件本身是 PDF，未冒充 PPTX。 |
| 原始 PPTX | [python-pptx 官方仓库测试文件](https://raw.githubusercontent.com/scanny/python-pptx/master/tests/test_files/test.pptx) | 原件下载、ZIP/OOXML 识别、1 张幻灯片解析通过。这是格式验证文件，不声称是中文教材课件。 |
| 动态网页 | [Quotes JavaScript 页面](https://quotes.toscrape.com/js/) | Chromium 渲染成功，正文包含 JavaScript 加载的 Albert Einstein 引语，保留原 HTML 和渲染 HTML。 |
| 中文一年级讲播视频 | [央视《春夏秋冬》](https://edu.cctv.com/2020/02/17/VIDEVZmTlUifJLxCG2RSRY9b200217.shtml) | yt-dlp 下载 77262987 字节；容器时长 1800.480000 秒，H.264 480×270 + AAC。FFmpeg 完整解码退出码 0，错误输出为空。使用低清档，不声称高清。 |
| 课件站限制 | [《爷爷和小树》](https://ziyk.szns.edu.cn/rc/res/show/id/425952) | 页面可以保存；某个链接返回 504。实际下载入口返回 HTML，并未取得原 PPT。单独记录这个限制，不能按网页或预览图算课件下载成功。 |

绘本任务的两个外链发生 TLS 证书验证错误，未关闭证书校验；主绘本、图片和 PDF 成功。绘本文件末页声明 CC BY-NC 3.0，素材仅在本地作技术验证，不随仓库发布，不作为商业授权证明。

## 自动化回归

8 项通过：原始无后缀 PPTX/任意格式文件/伪 PDF 拒绝；递归深度与 URL 去重；文件大小限制与残片清理；取消与重新提交；动态 JS 资源；API 输入与跨域边界；浏览器 blob 下载按钮；内容哈希去重与资源数量上限。

使用真正的本地 HTTP 服务器、Chromium、实际文件和 API，未 mock 网络下载。Starlette 的测试客户端报告一条 HTTPX 迁移弃用警告，不影响断言。

## 验证边界

已验证以上样本，不能推导为所有网站均支持。gallery-dl 已安装并接入，但尚未逐站验收。登录导出助手、各网站付费下载、OCR/视频转写均没有宣称完成。任务状态保留发现、下载、提取、分类四阶段；本版不做教学分类。更多边界见 README。

PDF SHA-256：`4924fbc23d074c9b8d87a1c5a02b10d2babb18e882ef0dc5494557958d0ef8ff`

视频 SHA-256：`029ac2c7fd31b3bc89b857f5705c2342365f576d3594dcdf15bf60fdd59877ee`
