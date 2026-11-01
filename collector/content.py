import hashlib
import mimetypes
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urldefrag
import filetype
from bs4 import BeautifulSoup
import trafilatura


def absolute(base, value):
    if not value:
        return None
    url = urldefrag(urljoin(base, value.strip()))[0]
    return url if urlsplit(url).scheme in ("http", "https") else None


def discover(html, base, include_assets=False):
    soup = BeautifulSoup(html, "lxml")
    refs = []
    base_tag = soup.find("base", href=True)
    base = absolute(base, base_tag["href"]) if base_tag else base
    def add(value, role, label=""):
        url = absolute(base, value)
        if url:
            refs.append(dict(url=url, role=role, label=label[:300], order=len(refs)))
    for element_index, el in enumerate(soup.find_all(True)):
        tag = el.name
        if tag=='img':
            start=len(refs)
            # Keep all variants; a shared element is evidence of association, not proof of identical crop.
            variants=[]
            for part in el.get('srcset',el.get('data-srcset','')).split(','):
                bits=part.strip().split()
                if bits:
                    descriptor=bits[-1] if len(bits)>1 else '0w'
                    try:rank=float(descriptor[:-1])
                    except ValueError:rank=0
                    variants.append((rank,bits[0]))
            for attr in ('data-original','data-src'):
                if el.get(attr):add(el[attr],'resource',el.get('alt',''))
            for _,value in sorted(variants,reverse=True):add(value,'resource',el.get('alt',''))
            add(el.get('src'),'resource',el.get('alt',''))
            group=hashlib.sha256((base+'#image-'+str(element_index)).encode()).hexdigest()[:20]
            for ref in refs[start:]:ref.update(image_group=group,image_order=element_index,association='same_element_unverified_crop')
            continue
        if tag == "a" and el.get("href"):
            add(el["href"], "attachment" if el.has_attr("download") else "link", el.get_text(" ", strip=True))
        if tag in ("img", "source", "video", "audio", "track", "embed", "iframe", "object"):
            role = "frame" if tag == "iframe" else "resource"
            for attr in ("src", "data-src", "data-original", "poster", "data"):
                if el.get(attr):
                    add(el[attr], role, el.get("alt", ""))
            for part in el.get("srcset", el.get("data-srcset", "")).split(","):
                if part.strip():
                    add(part.strip().split()[0], role, el.get("alt", ""))
        if include_assets and tag in ("script", "link"):
            add(el.get("src") or el.get("href"), "resource")
    return soup, refs


def safe_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", unquote(name)).strip(" .")[:160]
    if not name or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}:
        name = "resource_" + name
    return name


def filename(url, headers, content_type):
    cd = headers.get("content-disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"([^\"]+)\"|filename=([^;]+)", cd, re.I)
    name = next((v for v in m.groups() if v), "") if m else Path(urlsplit(url).path).name
    name = safe_name(name or "resource")
    if not Path(name).suffix:
        name += mimetypes.guess_extension(content_type) or ".bin"
    return name


def inspect_file(path, declared=""):
    with path.open("rb") as f:
        head = f.read(8192)
    start = head.lstrip(b"\xef\xbb\xbf \r\n\t").lower()
    if start.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        return "text/html"
    guessed = filetype.guess(head)
    kind = guessed.mime if guessed else declared.split(";")[0] or "application/octet-stream"
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "[Content_Types].xml" in names:
                for prefix, suffix in (("ppt/", "presentationml.presentation"), ("word/", "wordprocessingml.document"), ("xl/", "spreadsheetml.sheet")):
                    if any(n.startswith(prefix) for n in names):
                        return "application/vnd.openxmlformats-officedocument." + suffix
            if "mimetype" in names and z.getinfo("mimetype").file_size < 200:
                if z.read("mimetype") == b"application/epub+zip":
                    return "application/epub+zip"
    return kind


def extract_text(path, mime):
    if mime == "application/pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n\n".join(p.extract_text() for p in reader.pages[:500]), {"pages": len(reader.pages)}
    if "presentationml" in mime:
        from pptx import Presentation
        deck = Presentation(path)
        return "\n\n".join("\n".join(s.text for s in slide.shapes if s.has_text_frame) for slide in deck.slides), {"slides": len(deck.slides)}
    if "wordprocessingml" in mime:
        from docx import Document
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs) + '\n\n' + '\n\n'.join('\n'.join('\t'.join(c.text for c in row.cells) for row in table.rows) for table in doc.tables), {}
    if 'spreadsheetml' in mime:
        from openpyxl import load_workbook
        book=load_workbook(path,read_only=True,data_only=False)
        try:
            return '\n\n'.join('## '+sheet.title+'\n'+'\n'.join('\t'.join('' if c is None else str(c) for c in row) for row in sheet.iter_rows(values_only=True)) for sheet in book), {'sheets':book.sheetnames}
        finally:
            book.close()
    if mime.startswith("text/") or mime in ("application/json", "application/xml"):
        return path.read_text(encoding="utf-8", errors="replace")[:2_000_000], {}
    return None, {}


def article(html, url):
    soup = BeautifulSoup(html, "lxml")
    text = trafilatura.extract(html, url=url, include_tables=True, include_links=True, output_format="markdown")
    if not text:
        for el in soup(["script", "style", "noscript"]):
            el.decompose()
        text = soup.get_text("\n", strip=True)
    return text, dict(title=soup.title.get_text(strip=True) if soup.title else "", metadata={el.get("name") or el.get("property"): el.get("content") for el in soup.find_all("meta", content=True) if el.get("name") or el.get("property")}, tables=[[[c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])] for row in table.find_all("tr")] for table in soup.find_all("table")])
