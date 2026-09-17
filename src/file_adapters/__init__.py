"""Concrete File Assistant adapters."""

from .docling import DoclingStructuredDocumentProvider
from .docx import DocxAdapter
from .gotenberg import GotenbergConversionProvider
from .markitdown import MarkItDownFastTextViewProvider
from .pdf import PdfAdapter
from .pptx import PptxAdapter
from .writers import DocxWriter, PptxWriter, XlsxWriter
from .xlsx import XlsxAdapter

__all__ = [
    "DocxAdapter",
    "DoclingStructuredDocumentProvider",
    "MarkItDownFastTextViewProvider",
    "PdfAdapter",
    "PptxAdapter",
    "XlsxAdapter",
    "GotenbergConversionProvider",
    "DocxWriter",
    "XlsxWriter",
    "PptxWriter",
]
