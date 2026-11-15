"""Read small research discussion DOCX documents safely using only Python standard library."""
from __future__ import annotations
import io,zipfile,re,xml.etree.ElementTree as ET

def get_text(blob):
    if len(blob)>1_000_000:raise ValueError('Word discussion file is too large (1 MB limit)')
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            names=z.namelist()
            if len(names)>100 or 'word/document.xml' not in names:raise ValueError('not a valid Word DOCX document')
            if any('vbaProject' in n or n.endswith('.bin') for n in names):raise ValueError('macros/embedded binary content is not allowed')
            if sum(x.file_size for x in z.infolist())>6_000_000:raise ValueError('DOCX document too large when extracted')
            root=ET.fromstring(z.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        text='\n'.join(' '.join(n.text or '' for n in par.findall('.//w:t',ns)) for par in root.findall('.//w:p',ns))
        return text.strip()
    except (OSError,zipfile.BadZipFile,ET.ParseError,KeyError,RuntimeError) as e:
        raise ValueError('invalid Word discussion file: '+str(e))

def validate(blob,kind):
    text=get_text(blob)
    if 'REPLACE THIS' in text.upper() or 'WRITE YOUR' in text.upper():raise ValueError(kind+': unfinished Word template')
    words=re.findall(r"\b[\w'-]+\b",text)
    limit=(140,360) if kind=='stage1' else (160,390)
    if not limit[0]<=len(words)<=limit[1]:raise ValueError(f'{kind}: discussion should contain {limit[0]}–{limit[1]} words (currently {len(words)})')
    return text
