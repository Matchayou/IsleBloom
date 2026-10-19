# Stage 7 首次全量回归失败摘录

首次 `TEST_DATABASE_URL=...55445/stage7_release .venv/bin/python -m pytest -q` 在前 51 项后出现连续失败。为避免同一 worker 启动错误重复等待 20 秒，手动中止该轮并单独运行：

```text
.venv/bin/python -m pytest -xq 'tests/test_durable_process.py::test_kill_worker_and_resume_only_remaining[before_call]'
FAILED ... Timed out: None
1 failed in 20.38s
```

随后读取该用例的临时 `worker.log`，其中的实际异常：

```text
File "src/agent_core/research_cli.py", line 58, in run
    create_app(service,args.frontend,task_service,durable,evaluation)
NameError: name 'evaluation' is not defined
```

这是当时终端和临时日志的人工摘录；原临时目录已由 pytest 清理。修复为 `task_service.evaluation` 后，该真实杀进程恢复用例 `1 passed in 4.74s`，最终全量输出见 `stage7-regression.txt`。
