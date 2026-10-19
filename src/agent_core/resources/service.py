from .ports import BlobStore, Parser, ResourceRepository
from .snapshots import (Resource, SourceSnapshot, ResourceIngestion, ParseResult,
                        ParseIssue, ResourceRef)


class ResourceService:
    """Trusted ingestion API. The scope facade owns caller authorization."""
    def __init__(self, repository: ResourceRepository, blobs: BlobStore, parser: Parser, observe_parse=lambda _: None):
        self.repository, self.blobs, self.parser = repository, blobs, parser
        self.observe_parse = observe_parse

    def ingest(self, *, owner, scope_id, name, content, media_type, kind='upload',
               resource_id=None, dependencies=(), url=None, actual_url=None,
               query=None, title=None, summary=None, run_id=None):
        resource = self.repository.resource(resource_id) if resource_id else Resource(owner=owner, name=name, kind=kind)
        if resource.owner != owner or resource.kind != kind:
            raise PermissionError('Resource ownership/kind mismatch')
        snapshot = SourceSnapshot(resource_id=resource.id, blob_hash=self.blobs.put(content),
                                  media_type=media_type, dependencies=dependencies)
        entry = ResourceIngestion(resource_id=resource.id, snapshot_id=snapshot.id, owner=owner,
            scope_id=scope_id, name=name, kind=kind, url=url, actual_url=actual_url,
            query=query, title=title, summary=summary, run_id=run_id)
        key = f'{self.parser.key}|{media_type}'
        parsed = ParseResult(snapshot_id=snapshot.id, parser_key=key)
        cached = self.repository.cached_parse(snapshot.blob_hash, key)
        self.repository.register(resource, snapshot, entry, parsed)
        def report(value):
            try:
                self.observe_parse(value)
            except Exception:
                pass  # Display errors must not change parse outcomes.
        report(parsed)
        def progress(phase):
            value = parsed.model_copy(update={'status': 'processing', 'phase': phase})
            self.repository.save_parse(value)
            report(value)
        try:
            progress('parsing')
            if cached:
                result = parsed.model_copy(update={'status': cached.status, 'phase': 'cache',
                    'fragments': cached.fragments, 'issues': cached.issues,
                    'ocr_pages': cached.ocr_pages, 'cached_from': cached.id})
            else:
                output = self.parser.parse(content, media_type, progress)
                # Only nonempty located fragments become readable.
                status = ('partial' if output.issues else 'ready') if output.fragments else 'failed'
                issues = output.issues or (() if output.fragments else (ParseIssue(location='document', reason='No readable content'),))
                result = parsed.model_copy(update={'status': status, 'phase': 'finished',
                    'fragments': output.fragments, 'issues': issues, 'ocr_pages': output.ocr_pages})
        except Exception as exc:
            result = parsed.model_copy(update={'status': 'failed', 'phase': 'finished',
                'issues': (ParseIssue(location='document', reason=f'{type(exc).__name__}: {exc}'),)})
        self.repository.save_parse(result)
        report(result)
        return ResourceRef(resource_id=resource.id, snapshot_id=snapshot.id, parse_id=result.id)

    def describe(self, ref):
        resource = self.repository.resource(ref.resource_id)
        snapshot = self.repository.snapshot(ref.snapshot_id)
        parsed = self.repository.parse(ref.parse_id)
        if snapshot.resource_id != resource.id or parsed.snapshot_id != snapshot.id:
            raise PermissionError('Mismatched resource version')
        return resource, snapshot, parsed

    def read(self, ref):
        _, _, parsed = self.describe(ref)
        if parsed.status not in ('ready', 'partial'):
            raise ValueError(f'Parse {parsed.id} is {parsed.status}')
        return parsed.fragments

    def source_metadata(self, ref):
        resource, _, _ = self.describe(ref)
        if resource.kind != 'web': return {}
        entry = self.repository.ingestion(ref.snapshot_id)
        return {'url': entry.url, 'actual_url': entry.actual_url,
                'fetched_at': entry.at.isoformat()}

    def original(self, ref):
        _, snapshot, _ = self.describe(ref)
        return self.blobs.get(snapshot.blob_hash)
