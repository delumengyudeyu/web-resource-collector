"""Local-only OCR, text extraction, timestamped ASR and segment-aligned translation."""
import hashlib
import json
import re
import sys
from pathlib import Path
from datetime import timedelta
from . import store, processing
from .content import extract_text

def segments_for(item, spec):
    if spec.get('source_process_id'):
        parent = processing.get(spec['source_process_id'])
        data = json.loads(processing.safe_path(parent['files']['json']).read_text(encoding='utf-8'))
        return data['segments'], parent.get('partial', False)
    path = processing.safe_path(item['path'])
    mime = item.get('content_type','')
    if mime == 'application/pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        return [dict(page=n+1,text=p.extract_text() or '') for n,p in enumerate(reader.pages[:spec['max_pages']])],len(reader.pages)>spec['max_pages']
    if 'presentationml' in mime:
        from pptx import Presentation
        slides = Presentation(path).slides
        return [dict(page=n+1,text='\n'.join(s.text for s in slide.shapes if s.has_text_frame)) for n,slide in enumerate(slides) if n<spec['max_pages']], len(slides)>spec['max_pages']
    if path.suffix.lower() in ('.srt','.vtt'):
        import srt
        text=path.read_text(encoding='utf-8-sig')
        if path.suffix.lower()=='.vtt':
            text=re.sub(r'^WEBVTT[^\n]*\n','',text)
            text=re.sub(r'(\d{2}:\d{2}:\d{2})\.(\d{3})',r'\1,\2',text)
            text='\n\n'.join(str(i+1)+'\n'+b for i,b in enumerate(text.strip().split('\n\n')))
        return [dict(start=c.start.total_seconds(),end=c.end.total_seconds(),text=c.content) for c in srt.parse(text)],False
    if item.get('text_path'):
        text=processing.safe_path(item['text_path']).read_text(encoding='utf-8')
    else:
        text,_=extract_text(path,mime)
    if not text:
        raise ValueError('No text layer; run OCR or transcription first, then select that result')
    return [dict(paragraph=n+1,text=t) for n,t in enumerate(re.split(r'\n\s*\n',text)) if t.strip()],False

def ocr(item,spec,progress):
    from rapidocr_onnxruntime import RapidOCR
    from PIL import Image
    import numpy as np
    engine=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=1)
    path=processing.safe_path(item['path'])
    doc=None
    if item.get('content_type')=='application/pdf':
        import pypdfium2 as pdfium
        doc=pdfium.PdfDocument(str(path))
        count=len(doc)
    else:
        count=1
    out=[]
    try:
        for i in range(min(count,spec['max_pages'])):
            if doc:
                page=doc[i]
                bitmap=page.render(scale=2)
                im=bitmap.to_pil().copy()
                bitmap.close()
                page.close()
            else:
                im=Image.open(path).convert('RGB')
            if im.width*im.height>40_000_000:
                raise ValueError('Image exceeds OCR pixel limit')
            lines,_=engine(np.asarray(im))
            im.close()
            for line,(box,text,confidence) in enumerate(lines or []):
                out.append(dict(page=i+1,line=line+1,text=text,confidence=float(confidence),box=box,
                                needs_review=float(confidence)<.9))
            progress(i+1,min(count,spec['max_pages']))
    finally:
        if doc:
            doc.close()
    if not out:
        raise ValueError('OCR detected no text; original file retained')
    return out,count>spec['max_pages']

def transcribe(item,spec,progress):
    from faster_whisper import WhisperModel
    model=WhisperModel(str(store.ROOT/'runtime/models/whisper-tiny'),device='cpu',compute_type='int8',cpu_threads=4,local_files_only=True)
    segments,info=model.transcribe(str(processing.safe_path(item['path'])),language=spec['source_language'],
               beam_size=3,vad_filter=True,clip_timestamps=f"0,{spec['max_seconds']}")
    out=[]
    for s in segments:
        out.append(dict(start=s.start,end=s.end,text=s.text,avg_logprob=s.avg_logprob,no_speech_prob=s.no_speech_prob,needs_review=True))
        progress(min(s.end,spec['max_seconds']),min(info.duration,spec['max_seconds']))
    if not out:
        raise ValueError('No speech detected')
    return out,info.duration>spec['max_seconds']

class Translator:
    def __init__(self,source,target):
        import ctranslate2
        import sentencepiece
        root=store.ROOT/'runtime/models'/f'{source}-{target}'
        model=next(root.glob('**/model.bin')).parent
        sp=next(root.glob('**/sentencepiece.model'))
        self.tokenizer=sentencepiece.SentencePieceProcessor(model_file=str(sp))
        self.model=ctranslate2.Translator(str(model),device='cpu',compute_type='int8',inter_threads=1,intra_threads=4)

    def translate(self,text):
        # Keep line boundaries and process every token. No silent context-window truncation.
        lines=[]
        for line in text.split('\n'):
            if re.fullmatch(r'[\d\s.,:/%+()\-]+',line):
                lines.append(line)
                continue
            tokens=self.tokenizer.encode(line,out_type=str)
            parts=[]
            for start in range(0,len(tokens),192):
                result=self.model.translate_batch([tokens[start:start+192]],beam_size=4,max_decoding_length=512)[0]
                if len(result.hypotheses[0])>=512:
                    raise ValueError('Translation output limit reached; split the source into smaller segments')
                parts.append(self.tokenizer.decode(result.hypotheses[0]).replace('\u2581',' ').strip())
            lines.append(' '.join(parts))
        return '\n'.join(lines)

def main(pid):
    record=processing.get(pid)
    spec=record['spec']
    item=store.get_item(record['item_id'])
    def progress(done,total):
        record['progress']=min(99,round(100*done/max(total,1)))
        processing.save(record)
    try:
        action=spec['action']
        if action=='ocr':
            segments,partial=ocr(item,spec,progress)
        elif action=='transcribe':
            segments,partial=transcribe(item,spec,progress)
        else:
            segments,partial=segments_for(item,spec)
        if action=='translate':
            translator=Translator(spec['source_language'],spec['target_language']) if spec['source_language']!=spec['target_language'] else None
            segments=[dict(s) for s in segments]
            for i,s in enumerate(segments):
                s['translation']=translator.translate(s['text']) if translator else s['text']
                s['needs_review']=True
                progress(i+1,len(segments))
        data=dict(item_id=item['id'],source_sha256=item.get('sha256'),action=action,spec=spec,
                  partial=partial,needs_review=True,segments=segments)
        markdown=[]
        for s in segments:
            locator=f"Page {s['page']}" if 'page' in s else f"{s['start']:.2f}–{s['end']:.2f}s" if 'start' in s else f"Paragraph {s.get('paragraph',1)}"
            markdown.append('### '+locator+'\n\n'+s['text']+ ('\n\n'+s['translation'] if 'translation' in s else ''))
        outputs={'json':json.dumps(data,ensure_ascii=False,indent=2),'text':'\n\n'.join(markdown)}
        if segments and all('start' in s for s in segments):
            import srt
            for mode in ['original','translated','bilingual']:
                if mode!='original' and action!='translate':
                    continue
                captions=[srt.Subtitle(i+1,timedelta(seconds=s['start']),timedelta(seconds=s['end']),
                    s['text'] if mode=='original' else s['translation'] if mode=='translated' else s['text']+'\n'+s['translation']) for i,s in enumerate(segments)]
                outputs[mode+'_srt']=srt.compose(captions)
        size=sum(len(s.encode()) for s in outputs.values())
        if size>spec['max_output_mb']*1024**2:
            raise ValueError('Derived output exceeds storage limit')
        from .limits import ensure_storage
        ensure_storage(size)
        folder=store.DATA/'derived'/pid
        folder.mkdir(parents=True,exist_ok=True)
        record['files']={}
        record['hashes']={}
        for key,text in outputs.items():
            p=folder/(key+('.srt' if key.endswith('_srt') else '.json' if key=='json' else '.md'))
            p.write_text(text,encoding='utf-8')
            record['files'][key]=str(p.relative_to(store.DATA))
            record['hashes'][key]=hashlib.sha256(p.read_bytes()).hexdigest()
        record.update(status='partial' if partial else 'completed',partial=partial,progress=100,segments=len(segments),finished_at=store.now())
        processing.save(record)
    except Exception as exc:
        record.update(status='failed',error=type(exc).__name__+': '+str(exc)[:400])
        processing.save(record)

if __name__=='__main__':
    main(sys.argv[1])
