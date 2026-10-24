"""Bounded byte checks. A file extension alone is never a passing verdict."""
import hashlib
import io
import zipfile
from xml.etree import ElementTree


def verify_format(data: bytes, format: str, content_type: str, name: str, sha256: str):
    if hashlib.sha256(data).hexdigest() != sha256:
        return False
    if format == 'markdown':
        if content_type != 'text/markdown' or not name.lower().endswith('.md') or b'\x00' in data:
            return False
        try:
            return bool(data.decode('utf-8').strip()) and not zipfile.is_zipfile(io.BytesIO(data))
        except UnicodeDecodeError:
            return False
    if format != 'docx' or content_type != 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' or not name.lower().endswith('.docx'):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if len(z.infolist()) > 1000 or sum(i.file_size for i in z.infolist()) > 16 * 1024 * 1024:
                return False
            if any(z.getinfo(n).file_size > 2 * 1024 * 1024 for n in ('[Content_Types].xml', 'word/document.xml')):
                return False
            type_bytes, doc_bytes = z.read('[Content_Types].xml'), z.read('word/document.xml')
            if any(b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper() for data in (type_bytes, doc_bytes)):
                return False
            types = ElementTree.fromstring(type_bytes)
            document = ElementTree.fromstring(doc_bytes)
            return (document.tag == '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document'
                and any(e.attrib.get('PartName') == '/word/document.xml' and e.attrib.get('ContentType') == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml' for e in types))
    except (KeyError, ValueError, zipfile.BadZipFile, ElementTree.ParseError, RuntimeError):
        return False
