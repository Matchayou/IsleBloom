"""Advanced manual asset management; no automatic evolution or memory writes."""
from ..methods.models import AssetVersion


def method_endpoints(service, respond):
    async def assets(request): return respond(service.catalog())
    async def asset_save(request): return respond(service.save(AssetVersion.model_validate(await request.json())),201)
    async def asset_publish(request):
        data = await request.json()
        return respond(service.publish(data['category'],data['asset_id'],data['version'],
            data['expected'],data['actor'],data['reason']))
    async def workflow_compile(request): return respond(await service.instantiate(**await request.json()),201)
    async def workflow_instance(request): return respond(service.instance(request.query_params['id']))
    async def workflow_propose(request):
        data = await request.json()
        return respond(await service.propose(data['id'], data.get('origin_node_id')),201)
    return dict(assets=assets,asset_save=asset_save,asset_publish=asset_publish,
                workflow_compile=workflow_compile,workflow_instance=workflow_instance,workflow_propose=workflow_propose)
