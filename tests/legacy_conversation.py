from typing import Protocol
from uuid import uuid4
import json

from agent_core.contracts import ExecutePort, Message, PreparedInput, RunRequest, RunResult, RunError, Value, CoreFailure
from agent_core.conversation import ConversationStore


class ConversationService:
    def __init__(self, runner: ExecutePort, store: ConversationStore):
        self.runner, self.store = runner, store
        self._active: set[str] = set()

    async def send(self, conversation_id: str, agent_id: str, text: str) -> RunResult:
        run_id = str(uuid4())
        if conversation_id in self._active:
            return RunResult(run_id=run_id, status="failed", error=RunError(
                code="conversation_conflict", origin="conversation", message="Conversation already running"))
        self._active.add(conversation_id)
        try:
            snapshot = await self.store.load(conversation_id)
            user = Message(role="user", content=text)
            result = await self.runner.execute(RunRequest(run_id=run_id, agent_id=agent_id,
                input=PreparedInput(messages=snapshot.messages + (user,))))
            if result.status == "succeeded":
                answer = result.output if isinstance(result.output, str) else json.dumps(result.output, ensure_ascii=False)
                await self.store.append(conversation_id, snapshot.revision, run_id,
                                        (user, Message(role="assistant", content=answer)))
            return result
        except CoreFailure as exc:
            return RunResult(run_id=run_id, status="failed", error=exc.error)
        except Exception:
            return RunResult(run_id=run_id, status="failed", error=RunError(
                code="store_failed", origin="conversation", message="Conversation store failed"))
        finally:
            self._active.remove(conversation_id)
