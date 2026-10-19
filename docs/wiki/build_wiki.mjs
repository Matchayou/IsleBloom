/** Build a dependency-free, offline HTML Wiki from the source-backed Markdown. */
import fs from 'node:fs';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import React from '../../frontend/node_modules/react/index.js';
import {renderToStaticMarkup} from '../../frontend/node_modules/react-dom/server.node.js';
import Markdown from '../../frontend/node_modules/react-markdown/index.js';
import remarkGfm from '../../frontend/node_modules/remark-gfm/index.js';
const here=path.dirname(fileURLToPath(import.meta.url));
const root=path.resolve(here,'../..');
const read=name=>fs.readFileSync(path.join(here,name),'utf8');
const markdown=read('project-systems-wiki.md');
const manifest=JSON.parse(read('source-manifest.json'));
const views=JSON.parse(read('system-views.json'));
const flowNodes=JSON.parse(read('flow-nodes.json'));
const renderedNodes=new Set();
const sources=JSON.parse(read('source-snapshot.json'));
for(const entry of manifest.sources){const text=sources[entry.path];if(text===undefined||createHash('sha256').update(text).digest('hex')!==entry.sha256)throw new Error(`Snapshot fingerprint mismatch: ${entry.path}`);}
const e=React.createElement;
const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const extraRefs=JSON.parse(execFileSync('python3',['-c', `
import ast,json,sys
sources=json.load(sys.stdin)
refs=[]
def walk(nodes,file,prefix=''):
 for node in nodes:
  if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
   name=prefix+node.name
   signature=(('async ' if isinstance(node,ast.AsyncFunctionDef) else '')+'def '+node.name+'('+ast.unparse(node.args)+')'+(' -> '+ast.unparse(node.returns) if node.returns else '')) if not isinstance(node,ast.ClassDef) else 'class '+node.name
   refs.append(dict(symbol=name,path=file,line=node.lineno,signature=signature))
   if isinstance(node,ast.ClassDef):walk(node.body,file,name+'.')
for file,text in sources.items():
 if file.startswith('src/agent_core/') and file.endswith('.py'):walk(ast.parse(text).body,file)
print(json.dumps(refs))
`],{cwd:root,encoding:'utf8',input:JSON.stringify(sources)}));
const refs=new Map([...extraRefs,...manifest.references.filter(r=>r.symbol)].map(r=>[r.symbol,r]));
const sections=[...markdown.matchAll(/^## (\d+)\. (.+)\n([\s\S]*?)(?=^## \d+\. |$(?![\s\S]))/gm)].map(m=>({number:Number(m[1]),title:m[2],body:m[3]}));
if(sections.length!==24)throw new Error(`Expected 24 chapters, found ${sections.length}`);
const exactRefs=new Map(extraRefs.map(ref=>[ref.path+'::'+ref.symbol,ref]));
function resolveFunction(name){
  const [module,symbol]=name.includes('::')?name.split('::'):[null,name];
  const ref=module?exactRefs.get('src/agent_core/'+module+'::'+symbol):refs.get(symbol);
  if(!ref)throw new Error(`Unresolved node function: ${name}`);
  const actual=exactRefs.get(ref.path+'::'+ref.symbol);
  if(!actual||actual.line!==ref.line)throw new Error(`Stale function location: ${name}`);
  return {...actual};
}
const nodeData=Object.fromEntries(Object.entries(flowNodes).map(([id,node])=>{
  for(const key of ['title','input','output','role'])if(!node[key]?.trim())throw new Error(`Missing ${key}: ${id}`);
  if(!node.functions?.length)throw new Error(`No source functions: ${id}`);
  const chapters=[...new Set([node.chapter,...node.related])];
  const systems=chapters.map(number=>{const section=sections.find(s=>s.number===number);if(!section)throw new Error(`Missing related system ${id}:${number}`);return {number,title:section.title};});
  const match=id.match(/^step-(\d+)-(\d+)$/);
  return [id,{...node,functions:node.functions.map(resolveFunction),systems,
    previous:match&&Number(match[2])>1?`step-${match[1]}-${Number(match[2])-1}`:null,
    next:match&&Number(match[2])<6?`step-${match[1]}-${Number(match[2])+1}`:null}];
}));
function trigger(id,content,className=''){
  if(!nodeData[id])throw new Error(`Diagram node has no metadata: ${id}`);
  renderedNodes.add(id);
  return `<button type="button" class="node-trigger ${className}" data-node="${id}" aria-haspopup="dialog" aria-controls="node-dialog" aria-label="查看节点详情：${escape(nodeData[id].title)}">${content}</button>`;
}

const sourceLink=(symbol,label)=>{const ref=refs.get(symbol);if(!ref)throw new Error(`Unresolved function ${symbol}`);return `<a class="source-link" href="#source-dialog" data-source="${escape(ref.path)}" data-line="${ref.line}">${escape(label||symbol)} <span aria-hidden="true">↗</span></a>`};
function flow(steps,label,chapter){return `<div class="visual-label">REQUEST FLOW / 请求链路 <span class="diagram-help">点击节点查看函数与数据流</span></div><ol class="request-flow" aria-label="${escape(label)}">${steps.map(([title,detail],i)=>`<li>${trigger(`step-${chapter}-${i+1}`,`<b>${escape(title)}</b><small>${escape(detail)}</small>`,'flow-trigger')}</li>`).join('')}</ol>`}
function architecture(){let nodeIndex=0;const lanes=[['入口',['React 工作区 / CLI|接收操作，展示状态']],['业务',['Research / Chat / Project|节点、消息与项目','Task / Collaboration / Plan|工作单、交接与计划','Method / Routing|职责资产与选择']],['输入',['Scope / ContextBuilder|授权版本、固定输入']],['执行',['ExecutionService / CoreRunner|运行契约、工具守卫','ControlledEngine|准入、预算、writer']],['运行时',['OpenAIEngine / SDK Runner|模型与工具循环','BrokerModel / Channel|受控调用通道']],['外部操作',['Broker → Provider|实际 Responses 调用','Broker → Core invoke → Tools|获准工具与业务 handler']],['存储',['ControlledLedger / Checkpoints|操作、用量与恢复','ResearchLedger / TaskLedger|结果、证据与收据','PostgreSQL / 私有工件 / Blob|版本化状态和完整原文']]];return `<figure class="architecture" aria-label="项目分层架构和调用方向">${lanes.map(([label,nodes],i)=>`${i?`<div class="arch-arrow">↓ ${i===5?'调用前校验与 reservation':i===6?'响应回执与事务发布':'明确契约 / 获准输入'}</div>`:''}<div class="arch-lane"><div class="arch-label">${label}</div><div class="arch-nodes">${nodes.map(n=>{const [a,b]=n.split('|');return trigger(`arch-${++nodeIndex}`,`<b>${escape(a)}</b><small>${escape(b)}</small>`,`arch-node ${i===3||i===5?'accent':''}`)}).join('')}</div></div>`).join('')}<figcaption class="diagram-caption">关键回路：SDK → Broker → 工具结果 → SDK 下一轮；工具进入业务服务时再次校验权限。Collaboration 通过 Task 执行专家；业务结果与受控操作分别提交。</figcaption></figure>`}
function sequence(code){const people={};for(const m of code.matchAll(/participant (\w+) as (.+)/g))people[m[1]]=m[2];let index=0;const rows=[];for(const line of code.split('\n')){const match=line.trim().match(/^(\w+)(--?>>)(\w+):\s*(.+)$/);if(match){const [,from,arrow,to,action]=match;rows.push(`<tr><td>${String(++index).padStart(2,'0')}</td><td>${escape(people[from]||from)}</td><td>${arrow.startsWith('--')?'⇢':'→'}</td><td>${escape(people[to]||to)}</td><td>${trigger(`seq-${index}`,escape(action),'sequence-trigger')}</td></tr>`)}else if(line.trim().startsWith('loop '))rows.push(`<tr class="loop"><td colspan="5">↻ ${escape(line.trim().slice(5))} · 以下调用在 SDK 循环中重复</td></tr>`);else if(line.trim()==='end')rows.push('<tr class="loop"><td colspan="5">结束模型与工具轮次，转入最终结果提交</td></tr>')};return `<div class="table-wrap"><table class="sequence"><caption>完整请求时序 · 实线为调用，虚线为结果返回</caption><thead><tr><th scope="col">步骤</th><th scope="col">发起方</th><th scope="col">方向</th><th scope="col">接收方</th><th scope="col">操作</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>`}
function state(){const node=(i,label,cls='')=>trigger(`state-${i}`,label,`state-node ${cls}`);return `<figure class="state-flow" aria-label="专家任务从冻结、执行、提交到审核和采用的状态流">${node(1,'冻结 TaskSpec')}<span class="arrow">→</span>${node(2,'执行 Run')}<span class="arrow">→</span>${node(3,'保存 Result / Receipt')}<span class="arrow">→</span>${node(4,'pending_review<br>或 partial_pending_review')}<span class="arrow">→</span>${node(5,'独立审核')}<span class="arrow">→</span><div class="state-branches">${node(6,'passed → adopted','state-good')}${node(7,'partial · 保留缺口')}${node(8,'rework · 返工')}</div><figcaption class="diagram-caption">点击节点查看对应函数与状态条件。执行成功、已保存、通过审核、已采用，是不同状态。</figcaption></figure>`}
const components={
 a({href,children}){if(href?.startsWith(root+'/')){const m=href.match(/^(.*):(\d+)$/);const relative=path.relative(root,m?m[1]:href);return e('a',{href:'#source-dialog',className:'source-link','data-source':relative,'data-line':m?m[2]:1},children)}if(href?.startsWith('#')){const n=href.match(/^#(\d+)-/);if(n)href='#section-'+n[1]}return e('a',{href},children)},
 table({children}){return e('div',{className:'table-wrap'},e('table',null,children))},
 th({children,...props}){return e('th',{...props,scope:'col'},children)},
 pre({children}){const code=children?.props;if(code?.className==='language-mermaid'){const text=String(code.children);const diagram=text.includes('sequenceDiagram')?sequence(text):text.includes('flowchart TB')?architecture():state();return e('div',{dangerouslySetInnerHTML:{__html:diagram}})}return e('pre',null,children)},
 h3({children}){return e('h3',null,children)},
};
function renderMarkdown(value){return renderToStaticMarkup(e(Markdown,{remarkPlugins:[remarkGfm],components,urlTransform:url=>url},value))}
function overview(view,title,chapter){return `<div class="visual-label">SYSTEM CONTRACT / 系统职责</div><div class="table-wrap glance"><table><caption>${escape(title)} · 输入、输出与协作边界</caption><tbody>${[['职责',view.role],['输入',view.input],['输出',view.output],['协作',view.peers]].map(([a,b])=>`<tr><td>${a}</td><td>${escape(b)}</td></tr>`).join('')}</tbody></table></div>${flow(view.steps,title+'完整请求链路',chapter)}<div class="visual-label">SOURCE MAP / 关键源码函数</div><div class="table-wrap"><table><thead><tr><th scope="col">源码函数</th><th scope="col">在链路中的作用</th><th scope="col">定义位置</th></tr></thead><tbody>${view.functions.map(([name,role])=>{const ref=refs.get(name);if(!ref)throw new Error(name);return `<tr><td>${sourceLink(name)}</td><td>${escape(role)}</td><td><code>${escape(ref.path.replace('src/agent_core/',''))}:${ref.line}</code></td></tr>`}).join('')}</tbody></table></div>`}
const groups=[['总览与全局链路',1,3],['系统设计与实现',4,22],['排查与场景串联',23,24]];
const nav=groups.map(([label,from,to])=>`<div class="nav-group">${label}</div>${sections.filter(s=>s.number>=from&&s.number<=to).map(s=>`<a class="nav-link" href="#section-${s.number}"><span>${String(s.number).padStart(2,'0')}</span>${escape(s.title.replace('系统',''))}</a>`).join('')}`).join('');
const body=sections.map(s=>{const view=views[s.number];const original=renderMarkdown(s.body);return `<section class="chapter" id="section-${s.number}" aria-labelledby="title-${s.number}"><div class="chapter-head"><span class="chapter-number">${String(s.number).padStart(2,'0')}</span><h2 id="title-${s.number}">${escape(s.title)}</h2></div>${view?`${overview(view,s.title,s.number)}<details class="details-body" open><summary>完整技术讲解 · 设计原因、调用细节、协作与边界</summary>${original}</details>`:original}</section>`}).join('');
const sourcePaths=new Set(manifest.sources.map(s=>s.path));
for(const view of Object.values(views))for(const [symbol] of view.functions)sourcePaths.add(refs.get(symbol).path);
for(const node of Object.values(nodeData))for(const ref of node.functions)sourcePaths.add(ref.path);
if(renderedNodes.size!==Object.keys(nodeData).length)throw new Error(`Unrendered node metadata: ${Object.keys(nodeData).filter(id=>!renderedNodes.has(id))}`);
for(const path of sourcePaths)if(sources[path]===undefined)throw new Error(`Source missing from analysis snapshot: ${path}`);
const intro=markdown.match(/^> (.+)$/m)?.[1]||'';
const json=JSON.stringify(sources).replace(/</g,'\\u003c');
const nodeJson=JSON.stringify(nodeData).replace(/</g,'\\u003c');
const html=`<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="Research Agent Core 系统 Wiki：19 个系统的完整请求链路、源码函数、表格与流程图。"><title>系统架构 Wiki · Research Agent Core</title><style>${read('wiki.css')}</style></head>
<body><a class="skip" href="#content">跳到正文</a><aside class="sidebar"><div class="brand"><span class="brand-mark" aria-hidden="true">R</span><b>Research Agent Core</b><small>系统架构 & 源码 Wiki</small></div><nav class="nav-scroll" aria-label="章节目录">${nav}</nav><div class="sidebar-foot">工作区源码快照 · 2026.10.12<br>24 章节 / 19 系统 / 离线可读</div></aside><div class="reading-progress" aria-hidden="true"></div><div class="workspace"><header class="topbar"><button id="menu" class="quiet-button menu-button" aria-expanded="false" aria-label="切换章节目录">目录</button><span class="crumb">项目文档 <span aria-hidden="true"> / </span> 系统架构</span><div class="search" role="search"><label for="search" hidden>搜索系统、函数与状态</label><input id="search" type="search" placeholder="搜索系统、源码函数或状态…" autocomplete="off" aria-controls="search-results"><div id="search-results" class="search-results" hidden></div><span id="search-count" role="status" hidden></span></div><button class="quiet-button print-button" id="print">打印 / PDF</button></header><main id="content"><section class="hero"><div class="eyebrow">Architecture handbook / 2026.10</div><h1>从一次请求，理解整个系统。</h1><p class="lead">从 Harness 与 Agent Runtime 出发，沿资料、权限、上下文、记忆和多智能体交接，追踪直到结果提交与恢复。每个系统都提供职责表、请求链路和源码函数定位。点击流程图节点，查看具体输入输出并跳转相关章节。</p><div class="hero-meta"><span><strong>19</strong> 系统设计</span><span><strong>24</strong> 完整章节</span><span><strong>21</strong> 请求与状态流程图</span><span><strong>${manifest.references.length}</strong> 源码 / 测试定位</span></div><div class="entry-links"><a href="#section-3">先看完整请求链路 →</a><a href="#section-6">理解 Harness</a><a href="#section-14">多智能体如何配合</a><button id="expand" class="quiet-button" data-expanded="true">收起完整讲解</button><a href="project-systems-wiki.md">Markdown 原稿</a></div><details class="details-body"><summary>分析依据与验证范围</summary><p>${escape(intro)}</p><p>本网页内嵌分析时的源码快照，点击函数可离线查看并定位定义行。后续源码改动需先更新分析与快照再生成网页；函数定位不代表行为已重新测试。</p></details></section><noscript><p class="no-script">正文、表格与流程图均可直接阅读；搜索、目录高亮和源码弹窗需要启用 JavaScript。</p></noscript>${body}<footer class="footer"><p>由固定分析源码快照与项目 Wiki 生成 · 完整正文可打印保存为 PDF · 未连接业务后端</p><a href="source-manifest.json">源码分析指纹</a><a href="https://www.w3.org/WAI/tutorials/page-structure/">页面结构参考 · W3C WAI</a><a href="https://www.w3.org/WAI/tutorials/tables/">表格语义参考 · W3C WAI</a></footer></main></div><dialog id="node-dialog" class="node-dialog" aria-labelledby="node-title"><div class="node-header"><div><p class="eyebrow" id="node-context"></p><h2 id="node-title">流程节点</h2></div><button id="node-close" class="quiet-button" autofocus>关闭</button></div><div class="node-body"><h3>在请求链路中的作用</h3><p id="node-role"></p><div class="table-wrap node-contract"><table><caption>此节点的数据输入与输出</caption><tbody><tr><th scope="row">输入</th><td id="node-input"></td></tr><tr><th scope="row">输出</th><td id="node-output"></td></tr></tbody></table></div><h3>对应源码函数</h3><p class="node-note">点击函数名可查看源码与高亮定义行；签名直接取自 Wiki 分析源码快照。</p><div id="node-functions" class="node-functions"></div><h3>相关系统章节</h3><div id="node-systems" class="node-systems"></div></div><div class="node-footer"><button type="button" id="node-previous" class="quiet-button">← 上一步</button><span id="node-position"></span><button type="button" id="node-next" class="quiet-button">下一步 →</button></div></dialog><dialog id="source-dialog" aria-labelledby="source-title"><div class="source-header"><b id="source-title">源码</b><button id="source-close" class="quiet-button">关闭</button></div><div class="source-info" id="source-location"></div><div class="source-code" id="source-code"></div></dialog><script id="node-data" type="application/json">${nodeJson}</script><script id="source-data" type="application/json">${json}</script><script>${read('wiki.js')}</script></body></html>`;
fs.writeFileSync(path.join(here,'index.html'),html);
console.log(JSON.stringify({output:path.join(here,'index.html'),chapters:sections.length,systemFlows:Object.keys(views).length,sourceFiles:Object.keys(sources).length,interactiveNodes:renderedNodes.size,bytes:Buffer.byteLength(html)}));
