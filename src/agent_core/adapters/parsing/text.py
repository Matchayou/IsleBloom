"""Plain UTF-8 source parsing, shared with the document parser's text path."""
from ...resources.snapshots import ParsedContent, Fragment


class TextParser:
    key = 'utf8-lines-v1'

    def parse(self, content, media_type, progress):
        if media_type != 'text/plain':
            raise ValueError('TextParser accepts text/plain only')
        text = content.decode('utf-8', errors='strict')
        return ParsedContent(fragments=tuple(Fragment(location=f'line:{i}', text=line)
            for i, line in enumerate(text.splitlines(), 1) if line.strip()))
