const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');
const {TextEncoder} = require('node:util');
let socket, listener, openedUrl, creates=0, removals=0, clock=0, clicked=false, confirmed=true, missing=false;
let detailPolls=0, siteRejection=null, chatRedirect=false, clickedTabId=null, stallInspect=false;
let searchBatches=[['a']], searchIndex=0, siteEnd=false, scrollDelay=0;
const scrollTabs = [];
const openedTabs = new Map();
const innerList = {scrollTop:0, scrollHeight:800, clientHeight:400};
const tabUrls = new Map([[1, 'https://www.zhipin.com/web/geek/jobs']]);
class Socket {
  constructor() { this.readyState=1; this.handlers={}; this.sent=[]; socket=this; }
  addEventListener(name,fn){this.handlers[name]=fn;}
  send(raw){this.sent.push(JSON.parse(raw));} close(){ this.readyState=3; }
}
Socket.OPEN=1;Socket.CONNECTING=0;
const search=()=>{
  const ids = searchBatches[searchIndex];
  return {state:'authenticated',jobCards:ids.length,
    jobs:ids.map(job_id=>({job_id,title:'Job'})),
    privateJobUrls:ids.map(job_id=>({job_id,url:`https://www.zhipin.com/job_detail/${job_id}.html?securityId=secret`})),
    search:{keyword:'Python',city:'北京',site_end:siteEnd}};
};
const detail=(jobId='a')=>({state:'authenticated',site_rejection:siteRejection,detail:{job_id:jobId,title:'Job',company:'Company',description:'Description',contact_status:clicked&&confirmed?'already_contacted':'not_contacted'}});
const chrome={runtime:{getManifest:()=>({version:'1.3.1'}),onMessage:{addListener:fn=>listener=fn}},storage:{local:{get:async()=>({}),set:async()=>{}},session:{data:{},async get(k){return{[k]:this.data[k]};},async set(obj){Object.assign(this.data,obj);}}},alarms:{create(){},onAlarm:{addListener(){}}},
 tabs:{query:async()=>missing?[]:[{id:1,windowId:12,url:'https://www.zhipin.com/web/geek/jobs'}],create:async options=>{creates++;const id=creates+1;openedUrl=options.url;openedTabs.set(id,options);tabUrls.set(id,options.url);return{id};},get:async()=>({status:'complete'}),remove:async id=>{removals++;tabUrls.delete(id);}},
 scripting:{executeScript:async({target,args,func})=>{
   if(stallInspect) return new Promise(()=>{});
   if(func?.name==='scrollPageToBottom'){
     scrollTabs.push(target.tabId);
     context.document = {querySelectorAll:()=>[{parentElement:innerList}], scrollingElement:{scrollTop:0,scrollHeight:900}};
     context.getComputedStyle = () => ({overflowY:'auto'});
     vm.runInNewContext(`(${func.toString()})()`, context);
     clock += scrollDelay;
     if(openedTabs.get(target.tabId).active && context.document.scrollingElement.scrollTop===900 &&
        searchIndex+1<searchBatches.length) searchIndex++;
     return [{result:undefined}];
   }
   if(args?.[0]){if(args[2]<=clock)return[{result:{outcome:'not_clicked',reason:'DEADLINE_EXCEEDED'}}];clicked=true;clickedTabId=target.tabId;return[{result:{outcome:'clicked'}}];}
   if(chatRedirect && clicked && target.tabId===clickedTabId) return [{result:{state:'authenticated',detail:null}}];
   detailPolls++;
   const tabUrl = tabUrls.get(target.tabId) || '';
   const jobMatch = tabUrl.match(/\/job_detail\/([^/.]+)\.html/);
   const jobId = jobMatch ? jobMatch[1] : 'a';
   if(jobMatch && detailPolls===1) return [{result:{state:'unrecognized',detail:{job_id:jobId,title:'Job',company:'Company',description:'Description',contact_status:'not_contacted'}}}];
   return[{result:tabUrl.includes('/web/geek/jobs')?search():detail(jobId)}];
 }}};
const context={chrome,WebSocket:Socket,importScripts(){},inspectPage(){},
 setTimeout:(fn,ms)=>setImmediate(()=>{clock+=ms;fn();}), clearTimeout:clearImmediate,
 Date:{now:()=>clock},JSON,Map,Set,Promise,URL,TextEncoder,crypto:crypto.webcrypto};
vm.runInNewContext(fs.readFileSync('browser-extension/background.js','utf8'),context);
async function request(command,payload){await socket.handlers.message({data:JSON.stringify({id:'r',command,payload})});return socket.sent.at(-1);}
(async()=>{
 listener({type:'connect_bridge',token:'a'.repeat(43)},{},()=>{});await new Promise(r=>setImmediate(r));
 const nonce='b'.repeat(32),proof=crypto.createHmac('sha256','a'.repeat(43)).update(`server:${nonce}`).digest('hex');
 await socket.handlers.message({data:JSON.stringify({type:'challenge',nonce,proof})});
 await socket.handlers.message({data:JSON.stringify({type:'connected'})});
 assert.equal((await request('search_jobs',{keyword:'Python',city:'北京',limit:1})).ok,true);
 assert.doesNotMatch(JSON.stringify(socket.sent.at(-1)),/secret|securityId/);
 assert.equal((await request('search_jobs',{keyword:'Python',city:'北京',limit:151})).code,'FILTER_NOT_APPLIED');
 missing=true; // Source-bound detail/send must not depend on any active tab.
 detailPolls=0; // Simulates hydration race: first poll unrecognized, second authenticated.
 assert.equal((await request('read_detail',{job_id:'a'})).data.company,'Company');
 const before=creates;
 assert.equal((await request('send_once',{job_id:'a',title:'Job',company:'Company',send_contract:4,deadline:clock+45000})).data.outcome,'sent');
 assert.equal(creates-before,1);
 clicked=false;confirmed=false;
 const beforeUnknown=creates;
 assert.equal((await request('send_once',{job_id:'a',title:'Job',company:'Company',send_contract:4,deadline:clock+45000})).data.outcome,'unknown');
 assert.equal(creates-beforeUnknown,2); // Exactly one confirmation reload.
 clicked=false;confirmed=true;chatRedirect=true; // Chat redirect on tab 2 confirms via verification tab.
 const beforeRedirect=creates;
 assert.equal((await request('send_once',{job_id:'a',title:'Job',company:'Company',send_contract:4,deadline:clock+45000})).data.outcome,'sent');
 assert.equal(creates-beforeRedirect,2);
 chatRedirect=false;clicked=false;confirmed=false;siteRejection='DAILY_LIMIT_REACHED';
 const beforeReject=creates;
 const rejected=await request('send_once',{job_id:'a',title:'Job',company:'Company',send_contract:4,deadline:clock+45000});
 assert.equal(rejected.data.outcome,'rejected');
 assert.equal(rejected.data.reason,'DAILY_LIMIT_REACHED');
 assert.equal(creates-beforeReject,1); // Does not open verification tab when site modal rejects.
 siteRejection=null;
 clicked=false;
 assert.equal((await request('send_once',{job_id:'a',send_contract:4,deadline:clock-1})).data.reason,'DEADLINE_EXCEEDED');
 assert.equal(clicked,false);
 // Overlapping and duplicated cards, including a viewport that replaces earlier cards.
 missing=false;
 searchBatches=[['a','a','b'], ['b', ...Array.from({length:28},(_,i)=>`new${i}`)]];
 searchIndex=0;
 const beforeSearch=creates;
 const startClock=clock;
 const result=await request('search_jobs',{keyword:'Python',city:'北京',limit:30});
 assert.equal(result.data.search.stop_reason,'limit');
 assert.equal(result.data.jobs.length,30);
 assert.deepEqual(result.data.jobs.slice(0,3).map(job=>job.job_id),['a','b','new0']);
 assert.equal(creates-beforeSearch,1);
 assert.equal(new Set(scrollTabs).size,1);
 assert.equal(innerList.scrollTop,0);
 assert.equal(openedTabs.get(scrollTabs[0]).openerTabId,1);
 assert.equal(openedTabs.get(scrollTabs[0]).windowId,12);
 for (const job of result.data.jobs) assert.equal((await request('read_detail',{job_id:job.job_id})).ok,true);
 console.log(`Fixed search: 1 search tab, 30 details, virtual elapsed ${clock-startClock} ms`);

 searchBatches=[['a'], ['a','a']];
 searchIndex=0;
 const repeated=await request('search_jobs',{keyword:'Python',city:'北京',limit:60});
 assert.equal(context.document.scrollingElement.scrollTop,900);
 assert.equal(repeated.data.search.stop_reason,'no_new_ids');
 assert.equal(repeated.data.jobs.length,1);

 siteEnd=true;
 const ended=await request('search_jobs',{keyword:'Python',city:'北京',limit:60});
 assert.equal(ended.data.search.stop_reason,'site_end');
 siteEnd=false;

 searchBatches=Array.from({length:30},(_,i)=>[`step${i}`]);
 searchIndex=0;
 scrollDelay=2000;
 const timed=await request('search_jobs',{keyword:'Python',city:'北京',limit:60});
 assert.equal(timed.data.search.stop_reason,'deadline');
 assert.ok(timed.data.jobs.length>1);
 scrollDelay=0;
 // Hung executeScript must time out with PAGE_TIMEOUT without deadlocking subsequent commands.
 stallInspect=true;
 const stalled=await request('status');
 assert.equal(stalled.ok,false);
 assert.equal(stalled.code,'PAGE_TIMEOUT');
 stallInspect=false;
 assert.equal((await request('status')).ok,true);
 // Closed socket during failure must not throw unhandled rejection.
 socket.readyState=3;
 await socket.handlers.message({data:JSON.stringify({id:'r2',command:'read_detail',payload:{job_id:'missing'}})});
 assert.equal(creates,removals);
 console.log('Extension source binding and confirmation checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
