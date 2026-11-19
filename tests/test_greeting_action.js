const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
let clicks=0, authenticated=true, bodyText='';
const element=textContent=>({textContent,getClientRects:()=>[1]});
const button={...element('立即沟通'),getAttribute:()=>null,classList:{contains:()=>false},click:()=>clicks++};
const context={URL,Date,location:{protocol:'https:',host:'www.zhipin.com',pathname:'/job_detail/a.html',href:'https://www.zhipin.com/job_detail/a.html'},document:{
 get body(){return{innerText:bodyText};},querySelector:()=>null,querySelectorAll:selector=>{
  if(selector.includes('btn-startchat'))return[button];
  if(selector.includes('company-info'))return[element('Company')];
  if(selector.includes('.name h1'))return[element('Job')];
  if(selector.includes('.nav-figure'))return authenticated?[element('User')]:[];
  return[];
 }}};
vm.runInNewContext(fs.readFileSync('browser-extension/inspect.js','utf8')+';this.inspect=inspectPage;',context);
const target={job_id:'a',title:'Job',company:'Company'},deadline=Date.now()+10000;
assert.equal(context.inspect(target).outcome,'ready');
authenticated=false;assert.equal(context.inspect(target,true,deadline).reason,'AUTH_REQUIRED');authenticated=true;
bodyText='请完成安全验证';assert.equal(context.inspect(target,true,deadline).reason,'VERIFICATION_REQUIRED');bodyText='';
context.location.host='evil.test';assert.equal(context.inspect(target,true,deadline).reason,'JOB_MISMATCH');context.location.host='www.zhipin.com';
assert.equal(context.inspect(target,true,Date.now()-1).reason,'DEADLINE_EXCEEDED');
assert.equal(clicks,0);
assert.equal(context.inspect(target,true,deadline).outcome,'clicked');assert.equal(clicks,1);
console.log('Detail action origin/authentication/deadline checks passed');
