"""Read the dedicated CE5 live results in the product UI. No models or seeding."""
import os
import uvicorn
from pathlib import Path
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.parsing.text import TextParser
from agent_core.composition import build_resources,build_controlled_services
from agent_core.app.api import create_app
class NoModel:
    def get_model(self,name):raise ValueError('Read-only CE5 preview has no model')
root=Path(os.environ['CE5_EVIDENCE_ROOT']).resolve()
db=PostgresDatabase(os.environ['CE5_TEST_DATABASE_URL'])
try:
    scopes=build_resources(db,root/'blobs',parser=TextParser())
    research,tasks,_=build_controlled_services(db,scopes,private_root=root/'private',model_provider=NoModel())
    uvicorn.run(create_app(research,'.ce5/frontend',tasks),host='127.0.0.1',port=8795,log_level='warning')
finally:db.close()
