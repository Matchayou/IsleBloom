"""One synthetic expert discussion on the isolated preview's configured model."""
import asyncio
import httpx

BASE='http://127.0.0.1:8782/api/'

async def main():
    async with httpx.AsyncClient(timeout=180) as client:
        upload=await client.post(BASE+'upload',params={'research_id':'persistence-main-2','name':'project3-live-fixture.txt'},
            content='虚构材料：样本项目第二主任务只讨论 2026 年的示例范围，不含真实个人或商业信息。'.encode(),
            headers={'Content-Type':'text/plain'})
        upload.raise_for_status()
        ref=upload.json()['ref']
        spec=await client.post(BASE+'task_create',json={'research_id':'persistence-main-2','kind':'synthesize',
            'request':'解释这份虚构材料的范围','required_refs':[ref],
            'model':'deepseek-flash','task_id':'project3-live-expert'})
        spec.raise_for_status()
        discussion=await client.post(BASE+'expert_discuss',json={'task_id':'project3-live-expert',
            'content':'请用一句话说明该虚构材料的范围，并注明这是讨论而非重新执行。',
            'expected_revision':0,'message_id':'project3-live-question'})
        print('live-expert-discussion-http',discussion.status_code)
        if discussion.status_code != 200:
            print('error',discussion.json().get('error','unknown'))
            raise SystemExit(1)
        data=discussion.json()
        print('messages',len(data['messages']),'reply_chars',len(data['messages'][-1]['content']))
        print('reply',data['messages'][-1]['content'][:400])

asyncio.run(main())
