"""Local ranked retrieval. SQLite owns BM25; queries never contain FTS syntax."""
import re
import sqlite3
from contextlib import closing


def tokens(text):
    words = []
    for part in re.findall(r'[a-zA-Z0-9]+|[\u3400-\u9fff]+', text.casefold()):
        if '\u3400' <= part[0] <= '\u9fff':
            words.extend(part[i:i+2] for i in range(len(part)-1))
            if len(part) == 1: words.append(part)
        elif len(part) > 1:
            words.append(part)
    return words


def rank(query, documents, *, limit=8):
    """Return (original index, positive score), excluding nonmatches."""
    terms = list(dict.fromkeys(tokens(query)))[:128]
    if not terms or not documents: return []
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute('CREATE VIRTUAL TABLE documents USING fts5(text)')
        db.executemany('INSERT INTO documents(rowid,text) VALUES (?,?)',
            ((i+1, ' '.join(tokens(text))) for i, text in enumerate(documents)))
        expression = ' OR '.join('"' + term + '"' for term in terms)
        return [(key-1, -score) for key, score in db.execute(
            'SELECT rowid,bm25(documents) FROM documents WHERE documents MATCH ? '
            'ORDER BY bm25(documents),rowid LIMIT ?', (expression, limit))]
