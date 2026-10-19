# 修复后版本1真实执行验证

日期2026-10-12；提案cda35191-f054-471f-a836-44b7ba2ce55d，人工批准版本1。批准预算：三次运行，单步600秒，总1800秒，120000 tokens，fail_fast。

## 实际状态

第一步run 9e970fa7-9221-4574-9269-eea313479d9b，11:52:42.422启动，11:53:39.130完成，56.708秒。运行status=succeeded，结果version1 completion=partial。计划状态blocked，后两步waiting，没有运行和正式结果。三步成功执行与最终综合报告尚未通过。

## 运行修复的实测

4次模型请求全部confirmed：调查low，收尾none；耗时约6.32/7.32/6.29/15.76秒。0次摘要。6次工具调用全部有返回，包括一次PDF获取不可用观察。达到工具预算后关闭调查，出现一次DSML读取意图，被识别为model_protocol并纠正；没有执行其中额外的read_source。未超时、未扩展工具额度、没有unknown模型请求。

输入42695+输出2800=45495 tokens，usage.complete=true，低于120000批准额度；4轮模型、6次工具低于/达到本步8/6上限。

## 保存的研究内容

3份来源：arXiv摘要页、Google Research Blog官方ReAct说明全文、OpenAI Agents SDK Human-in-the-loop文档全文。保存3份版本引用、4条事实声明及其证据关系。正式结果ID result:9e970fa7-9221-4574-9269-eea313479d9b。报告副本live-fixed-step-1-report.md，完整结果及证据live-fixed-step-1-result.json。

模型missing两项：OpenReview论文全文PDF不可用；arXiv abs仅为摘要页，缺论文正文逐轮轨迹细节。公开博客支持机制核心，仍不能声称取得论文全文。

## 新发现的阻塞

app/commits.py用missing是否非空直接决定NodeResultVersion.complete/partial，research/plans.py只允许complete上游继续；但批准规划的analysis要求“无实测数字一律标missing”，第一步交付也允许说明full text或missing状态。两者没有区分阻断缺项与资料局限，导致明确写出缺口后被整体阻断。

本轮没有修改missing、伪造complete、重放第1步或绕过依赖守卫，也没有把程序运行succeeded宣称为三步研究成功。下一项修复应将交付完成标准、阻断缺项、允许的局限说明明确分开，保留真实证据。

PDF不可用的本地原因：TinyFishSearchProvider.fetch的URL一致性检查拒绝了返回，产生Fetched source redirected or changed URL。现有异常未保存requested/final_url，因此不能仅凭这条文字判断具体跳转目标或哪一字段不一致。未通过绕过URL守卫获取资料。

## 保存与前端

重复API读取当前结果与首次保存逐项相同。第一轮result:887ad94a-c028-4ba3-96da-46993988a306的version/output与原快照相同；第二轮run 0493e159-04c6-438f-ad26-73dd962de3d6的完整RunResult与原快照相同，旧unknown计量保持。前两轮未被覆盖。

前端刷新显示批准版本1、执行受阻；第一步“不完整交付，已阻止后续步骤”，展开可见45495 tokens用量及保存的报告；后两步等待执行。截图live-fixed-partial-report.jpg，核对live-fixed-execution-check.json。

已验证确认→真实模型/工具→结果和证据保存→前端读取、以及缺口阻断。未验证三步全部成功，未产生最终对照表或综合报告。正式服务继续运行。
