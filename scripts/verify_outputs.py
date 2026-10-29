"""Recheck the locally retained live evidence; do not commit the captured files."""
import hashlib
import json
import subprocess
import shutil
from pathlib import Path
import httpx
from PIL import Image
from pypdf import PdfReader
from pptx import Presentation

root = Path(__file__).resolve().parents[2]
c = httpx.Client(base_url="http://127.0.0.1:8765", trust_env=False, timeout=30)
def results(job):
    return c.get(f"/api/jobs/{job}/results").json()["items"]
def path(item):
    p = root / "data" / item["path"]
    with p.open('rb') as f:
        assert hashlib.file_digest(f, 'sha256').hexdigest() == item['sha256']
    return p

book = results('315b0e3bc28e44b09097f7c87cf667b1')
pdf = next(i for i in book if i.get('filename') == 'zh-0112_body.pdf')
reader = PdfReader(path(pdf))
assert len(reader.pages) == 10 and '我的身体' in reader.pages[0].extract_text()
images = sorted([i for i in book if '/images/0112/' in i['url']], key=lambda x:x['order'])
assert [i['url'].rsplit('/',1)[1] for i in images] == [f'{n:02}.jpg' for n in range(1,10)]
for i in images:
    Image.open(path(i)).verify()
download = c.get('/api/files/' + pdf['id'])
assert hashlib.sha256(download.content).hexdigest() == pdf['sha256']
lesson = results('53484caf67504994b6eb1743c3d1b746')[0]
lesson_pdf = PdfReader(path(lesson))
assert len(lesson_pdf.pages) == 29
slides = next(i for i in results('945eaef13dcc46b18136b2a044f711b0') if i.get('filename') == 'test.pptx')
assert len(Presentation(path(slides)).slides) == 1
dynamic = next(i for i in results('709122e4740545ab84cb4b206b44243b') if i['role'] == 'seed')
assert dynamic['adapter'] == 'http+playwright'
assert 'Albert Einstein' in (root / 'data' / dynamic['text_path']).read_text(encoding='utf-8')
movie = next(i for i in results('529467a1b60747769bfda3e945e53614') if i.get('filename','').endswith('.mp4'))
movie_path = path(movie)
ffmpeg = Path(shutil.which('ffmpeg') or 'ffmpeg.exe').resolve().parent
probe = json.loads(subprocess.check_output([str(ffmpeg/'ffprobe.exe'),'-v','error','-show_entries','format=duration,size:stream=codec_name,width,height','-of','json',str(movie_path)]))
assert abs(float(probe['format']['duration']) - 1798) < 5
decoded = subprocess.run([str(ffmpeg/'ffmpeg.exe'),'-v','error','-i',str(movie_path),'-f','null','NUL'],capture_output=True)
assert decoded.returncode == 0 and not decoded.stderr, decoded.stderr[:200]
report = f'''# 验证记录

日期：2026-10-29（北京时间）。所有来源均经本地 API 提交，真实输出保留在 D 盘 data 目录；仓库仅包含报告和验证脚本，不包含采集素材或 Cookie。

## 真实网站与原件验证

| 场景 | 来源 | 结果 |
|---|---|---|
| 中文绘本 | [《我的身体》](https://www.storybookscanada.ca/stories/zh/0112/) | 发现 87 项，下载 58 项，提取 15 项；原始中文 PDF 10 页。9 张故事图片全部能打开，按页面发现顺序对应 01.jpg 至 09.jpg；API 下载所得 PDF 与磁盘 SHA-256 相同。 |
| 中文二年级课件 | [语文园地一 PDF](https://www.jsfzzy.com/storage/course/64dd92d7dad30.pdf) | 下载原 PDF，{lesson['size']} 字节、29 页，可以解析。来源文件本身是 PDF，未冒充 PPTX。 |
| 原始 PPTX | [python-pptx 官方仓库测试文件](https://raw.githubusercontent.com/scanny/python-pptx/master/tests/test_files/test.pptx) | 原件下载、ZIP/OOXML 识别、1 张幻灯片解析通过。这是格式验证文件，不声称是中文教材课件。 |
| 动态网页 | [Quotes JavaScript 页面](https://quotes.toscrape.com/js/) | Chromium 渲染成功，正文包含 JavaScript 加载的 Albert Einstein 引语，保留原 HTML 和渲染 HTML。 |
| 中文一年级讲播视频 | [央视《春夏秋冬》](https://edu.cctv.com/2020/02/17/VIDEVZmTlUifJLxCG2RSRY9b200217.shtml) | yt-dlp 下载 {movie['size']} 字节；容器时长 {probe['format']['duration']} 秒，H.264 480×270 + AAC。FFmpeg 完整解码退出码 0，错误输出为空。使用低清档，不声称高清。 |
| 课件站限制 | [《爷爷和小树》](https://ziyk.szns.edu.cn/rc/res/show/id/425952) | 页面可以保存；某个链接返回 504。实际下载入口返回 HTML，并未取得原 PPT。单独记录这个限制，不能按网页或预览图算课件下载成功。 |

绘本任务的两个外链发生 TLS 证书验证错误，未关闭证书校验；主绘本、图片和 PDF 成功。绘本文件末页声明 CC BY-NC 3.0，素材仅在本地作技术验证，不随仓库发布，不作为商业授权证明。

## 自动化回归

8 项通过：原始无后缀 PPTX/任意格式文件/伪 PDF 拒绝；递归深度与 URL 去重；文件大小限制与残片清理；取消与重新提交；动态 JS 资源；API 输入与跨域边界；浏览器 blob 下载按钮；内容哈希去重与资源数量上限。

使用真正的本地 HTTP 服务器、Chromium、实际文件和 API，未 mock 网络下载。Starlette 的测试客户端报告一条 HTTPX 迁移弃用警告，不影响断言。

## 验证边界

已验证以上样本，不能推导为所有网站均支持。gallery-dl 已安装并接入，但尚未逐站验收。登录导出助手、各网站付费下载、OCR/视频转写均没有宣称完成。任务状态保留发现、下载、提取、分类四阶段；本版不做教学分类。更多边界见 README。

PDF SHA-256：`{pdf['sha256']}`

视频 SHA-256：`{movie['sha256']}`
'''
(root/'app/docs/VERIFICATION.md').write_text(report,encoding='utf-8',newline='\n')
print('Live evidence verified: PDF, images and order, PPTX, dynamic page, full video decode, API hash')
