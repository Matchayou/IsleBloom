"""Docling native document parsing plus explicit Tesseract OCR for scan pages.

No model download, remote parser, or silently substituted parser. PDF native cells
retain page/bbox; OCR retains page; rich tables are supported in HTML/CSV/DOCX.
"""
import hashlib
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from ...resources.snapshots import ParsedContent, Fragment, ParseIssue


class DocumentParser:
    def __init__(self, *, tessdata=None, languages='eng', max_pages=300):
        self.tessdata = Path(tessdata).resolve() if tessdata else None
        self.languages, self.max_pages = languages, max_pages
        binary_version = subprocess.run(['tesseract', '--version'], capture_output=True, text=True, check=True).stdout.splitlines()[0]
        config = [version(name) for name in ('docling', 'docling-core', 'docling-parse', 'pypdfium2', 'pillow')]
        config += [binary_version, languages, str(max_pages)]
        if self.tessdata:
            for language in languages.split('+'):
                config.append(hashlib.sha256((self.tessdata / f'{language}.traineddata').read_bytes()).hexdigest())
        else:
            # Resolve the installed training-data path; include its contents in the cache key.
            output = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True, check=True).stdout
            directory = Path(output.split('"')[1])
            for language in languages.split('+'):
                config.append(hashlib.sha256((directory / f'{language}.traineddata').read_bytes()).hexdigest())
        self.key = 'docling-native-tesseract-v1:' + hashlib.sha256('|'.join(config).encode()).hexdigest()
        self.converter = None

    def _converter(self):
        if self.converter is None:
            from docling.document_converter import DocumentConverter, NativePdfFormatOption
            from docling.datamodel.base_models import InputFormat
            self.converter = DocumentConverter(allowed_formats=[InputFormat.PDF, InputFormat.MD,
                InputFormat.HTML, InputFormat.CSV, InputFormat.DOCX],
                format_options={InputFormat.PDF: NativePdfFormatOption()})
        return self.converter

    def _ocr(self, image):
        with TemporaryDirectory(prefix='research-ocr-') as temp:
            path = Path(temp) / 'page.png'
            image.save(path)
            args = ['tesseract', str(path), 'stdout', '-l', self.languages, '--psm', '6']
            if self.tessdata:
                args += ['--tessdata-dir', str(self.tessdata)]
            value = subprocess.run(args, capture_output=True, text=True, timeout=60, check=True).stdout.strip()
            if not value:
                raise ValueError('OCR produced no readable text')
            return value

    def parse(self, content, media_type, progress):
        if media_type == 'text/plain':
            from .text import TextParser
            return TextParser().parse(content, media_type, progress)
        from docling_core.types.io import DocumentStream
        from docling_core.types.doc import TableItem
        from PIL import Image
        import pypdfium2 as pdfium
        if media_type in ('image/png', 'image/jpeg'):
            progress('ocr:page:1')
            try:
                with Image.open(BytesIO(content)) as im:
                    text = self._ocr(im)
                return ParsedContent(fragments=(Fragment(location='page:1', text=text, kind='ocr'),), ocr_pages=(1,))
            except Exception as exc:
                return ParsedContent(issues=(ParseIssue(location='page:1', reason=f'{type(exc).__name__}: {exc}'),), ocr_pages=(1,))
        suffix = {'application/pdf': 'pdf', 'text/markdown': 'md', 'text/html': 'html',
                  'text/csv': 'csv', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx'}
        if media_type not in suffix:
            raise ValueError(f'Unsupported media type: {media_type}')
        fragments, issues, ocr_pages = [], [], []
        pdf = pdfium.PdfDocument(content) if media_type == 'application/pdf' else None
        try:
            page_count = len(pdf) if pdf else 1
            if page_count > self.max_pages:
                raise ValueError(f'Page limit exceeded: {page_count}')
            for number in range(1, page_count + 1):
                location = f'page:{number}' if pdf else 'document'
                try:
                    progress(f'parsing:{location}')
                    result = self._converter().convert(DocumentStream(name='source.' + suffix[media_type],
                        stream=BytesIO(content)), page_range=(number, number) if pdf else (1, 9223372036854775807))
                    page_fragments = []
                    for item, _ in result.document.iterate_items():
                        text = item.export_to_markdown(doc=result.document) if isinstance(item, TableItem) else getattr(item, 'text', '')
                        if text and text.strip():
                            provenance = item.prov[0] if item.prov else None
                            loc = f'page:{provenance.page_no}:{item.self_ref}:bbox={provenance.bbox.model_dump_json()}' if provenance else item.self_ref
                            page_fragments.append(Fragment(location=loc, text=text.strip(), kind='table' if isinstance(item, TableItem) else 'text'))
                    # Full-page OCR on bitmap-containing pages also covers mixed native/scan pages.
                    if pdf and (not page_fragments or result.document.pictures):
                        ocr_pages.append(number)
                        progress(f'ocr:{location}')
                        page = pdf[number - 1]
                        bitmap = page.render(scale=3)
                        try:
                            text = self._ocr(bitmap.to_pil())
                            page_fragments = [Fragment(location=location, text=text, kind='ocr')]
                        finally:
                            bitmap.close()
                            page.close()
                    if not page_fragments:
                        raise ValueError('No located text')
                    fragments.extend(page_fragments)
                    for error in result.errors:
                        issues.append(ParseIssue(location=location, reason=str(error.error_message)))
                except Exception as exc:
                    issues.append(ParseIssue(location=location, reason=f'{type(exc).__name__}: {exc}'))
        finally:
            if pdf:
                pdf.close()
        return ParsedContent(fragments=tuple(fragments), issues=tuple(issues), ocr_pages=tuple(ocr_pages))
