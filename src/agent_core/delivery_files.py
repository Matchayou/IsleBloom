"""File deliveries are immutable views of atomically published model output."""
import hashlib
import re
from pathlib import PurePosixPath
from pydantic import Field, model_validator
from .contracts import Value

MEDIA_TYPES = {'.html': 'text/html', '.htm': 'text/html', '.md': 'text/markdown',
               '.txt': 'text/plain', '.csv': 'text/csv', '.json': 'application/json',
               '.svg': 'image/svg+xml', '.css': 'text/css', '.js': 'text/javascript'}
FILE_INSTRUCTIONS = ('\n用户要求生成文件时，必须在 files 中交付每个文件的 name 和完整 content。'
    'name 使用带扩展名的独立文件名，不含路径。支持 HTML、Markdown、文本、CSV、JSON、SVG、CSS 和 JavaScript。'
    '报告说明交付内容，不重复整份文件代码；不能只声称文件已生成而省略内容。HTML 必须含完整文档与结束标签，'
    '样式与交互脚本内联；地图等外部依赖使用绝对 HTTPS 地址，不能引用本地相对文件路径。')

class GeneratedFile(Value):
    name: str = Field(min_length=1, max_length=180)
    content: str = Field(min_length=1)

    @model_validator(mode='after')
    def valid_file(self):
        if self.name in ('.', '..') or any(c in self.name for c in '/\\\r\n\x00'):
            raise ValueError('File name must be a basename without control characters or paths')
        if any(ord(c) < 32 for c in self.name): raise ValueError('Invalid file name')
        suffix = PurePosixPath(self.name).suffix.lower()
        if suffix not in MEDIA_TYPES: raise ValueError('Unsupported generated file extension')
        if suffix in ('.html', '.htm') and not re.fullmatch(
                r'\s*(?:<!doctype\s+html\s*>\s*)?<html\b[^>]*>[\s\S]*</html>\s*', self.content, re.I):
            raise ValueError('HTML file must contain a complete document')
        return self

    def metadata(self):
        content = self.content.encode('utf-8')
        digest = hashlib.sha256(content).hexdigest()
        return {'file_id': hashlib.sha256(self.name.encode() + b'\0' + content).hexdigest(), 'name': self.name, 'bytes': len(content),
                'media_type': MEDIA_TYPES[PurePosixPath(self.name).suffix.lower()], 'sha256': digest}


def files_from_output(output):
    files = [GeneratedFile.model_validate(value) for value in output.get('files', ())]
    # A complete HTML document in a report is itself a deliverable, not a prose claim.
    report = output.get('report', '')
    for index, match in enumerate(re.finditer(r'<!doctype\s+html\s*>[\s\S]*?</html\s*>', report, re.I), 1):
        content = match.group()
        if not any(file.content.strip() == content.strip() for file in files):
            files.append(GeneratedFile(name='handbook.html' if index == 1 else f'handbook-{index}.html', content=content))
    names = [file.name for file in files]
    if len(names) != len(set(names)): raise ValueError('Generated file names must be unique')
    return files
