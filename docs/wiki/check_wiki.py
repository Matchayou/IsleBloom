"""Verify this Wiki's source locations and analysis snapshot without running agents."""
import ast
import hashlib
import json
import re
from pathlib import Path


def symbols(path):
    result = {}

    def visit(nodes, prefix=""):
        for node in nodes:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                key = prefix + node.name
                result[key] = node.lineno
                if isinstance(node, ast.ClassDef):
                    visit(node.body, key + ".")

    visit(ast.parse(path.read_text(encoding="utf-8")).body)
    return result


def main():
    directory = Path(__file__).resolve().parent
    root = directory.parents[1]
    document = (directory / "project-systems-wiki.md").read_text(encoding="utf-8")
    manifest = json.loads((directory / "source-manifest.json").read_text(encoding="utf-8"))
    errors = []
    cache = {}
    for source in manifest["sources"]:
        path = root / source["path"]
        if not path.is_file():
            errors.append(f"Missing source: {source['path']}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
            errors.append(f"Source changed since analysis: {source['path']}")
    for reference in manifest["references"]:
        path = root / reference["path"]
        if not path.is_file():
            continue
        if reference["symbol"]:
            if path not in cache:
                cache[path] = symbols(path)
            actual = cache[path].get(reference["symbol"])
            if actual != reference["line"]:
                errors.append(f"Symbol moved/missing: {reference['path']}::{reference['symbol']}")
        target = f"<{path}:{reference['line']}>"
        if target not in document:
            errors.append(f"Missing document link: {reference['path']}:{reference['line']}")
    if "[[" in document:
        errors.append("Unresolved source reference placeholder")
    chapters = re.findall(r"^## (\d+)\. ", document, flags=re.MULTILINE)
    if chapters != [str(i) for i in range(1, 25)]:
        errors.append("Expected the 24 ordered system/overview chapters")
    if document.count("```") % 2:
        errors.append("Unbalanced fenced code blocks")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Wiki verified: 24 chapters, {len(manifest['references'])} references, "
          f"{len(manifest['sources'])} unchanged source files.")


if __name__ == "__main__":
    main()
