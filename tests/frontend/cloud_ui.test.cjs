const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto, randomUUID } = require('node:crypto');
const root = path.join(__dirname, '..', '..');
const html = fs.readFileSync(path.join(root,'frontend','cloud.html'),'utf8');
const source = fs.readFileSync(path.join(root,'frontend','cloud.js'),'utf8');
function element() {
  const classes = new Set();
  return {textContent:'',disabled:false,hidden:false,className:'',classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)},classes,click(){},remove(){}};
}
async function fixture(options={}) {
  const nodes=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(x=>[x[1],element()]));
  for(const id of ['seed','recover','download'])nodes[id].disabled=true;
  nodes['before-count'].textContent='— / — / —'; nodes['after-count'].textContent='— / — / —';nodes['release-state'].textContent='NOT RUN';
  const pipeline=element(); const timers=[];const calls=[];const storage=new Map();const redirects=[];
  if(options.callback)storage.set('rgx-pkce',JSON.stringify({verifier:'test-verifier',state:'expected-state'}));
  const fetch=async(url,init={})=>{
    calls.push({url,init});
    if(url.endsWith('/oauth2/token'))return {ok:true,json:async()=>({id_token:'synthetic-test-token',refresh_token:'synthetic-refresh-token',expires_in:900})};
    if(url==='/v1/me')return {ok:true,json:async()=>({operator:options.operator!==false})};
    if(url==='/v1/lab/seed')return {ok:!options.seedFails,status:options.seedFails?503:200,json:async()=>options.seedFails?{detail:'held'}:{seeded:true,index_hits:1}};
    if(url==='/v1/lab/recovery')return {ok:true,json:async()=>({run_id:'test-run',status:'RUNNING'})};
    if(url==='/v1/runs/test-run')return {ok:true,json:async()=>options.held?{status:'FAILED'}:{status:'SUCCEEDED',result:{raw_before:{profiles:1,documents:1,index_chunks:1},raw_after:{profiles:0,documents:0,index_chunks:0},evidence_signed:true,release:{state:'RELEASED'}}}};
    throw new Error('Unexpected request: '+url);
  };
  const context=vm.createContext({document:{getElementById:id=>{assert.ok(nodes[id],'Missing HTML binding '+id);return nodes[id]},querySelector:selector=>{assert.equal(selector,'.pipeline');return pipeline},createElement:()=>element(),body:{appendChild(){}}},fetch,
    AUTH_CONFIG:{clientId:'local-test',authDomain:'https://auth.example.invalid',redirectUri:'http://localhost/callback'},
    sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    location:{search:options.callback?'?code=test-code&state='+(options.badState?'wrong-state':'expected-state'):'',assign:url=>redirects.push(String(url))},
    history:{replaceState(){}},crypto:{getRandomValues:a=>webcrypto.getRandomValues(a),subtle:webcrypto.subtle,randomUUID},URL,URLSearchParams,TextEncoder,Uint8Array,Blob,
    btoa:text=>Buffer.from(text,'binary').toString('base64'),setTimeout:fn=>{timers.push(fn);return timers.length},clearTimeout(){}});
  vm.runInContext(source,context,{filename:'cloud.js'});
  for(let i=0;i<15;i++)await Promise.resolve();
  return {nodes,pipeline,timers,calls,storage,redirects,context};
}
test('signed-out page never invents successful evidence',async()=>{
  const f=await fixture();assert.equal(f.calls.length,0);assert.equal(f.nodes.seed.disabled,true);assert.equal(f.nodes.recover.disabled,true);assert.equal(f.nodes.download.disabled,true);assert.equal(f.nodes['release-state'].textContent,'NOT RUN');
});
test('operator can seed, run, and view real returned counts',async()=>{
  const f=await fixture({callback:true});assert.equal(f.nodes.seed.disabled,false);assert.equal(f.storage.has('rgx-pkce'),false);assert.equal(f.nodes.signin.hidden,true);
  await f.nodes.seed.onclick();assert.equal(f.nodes.recover.disabled,false);assert.equal(f.nodes['release-state'].textContent,'NOT RUN');
  await f.nodes.recover.onclick();assert.equal(f.nodes['release-state'].textContent,'CHECKING');assert.equal(f.nodes.download.disabled,true);
  await f.timers.shift()();assert.equal(f.nodes['before-count'].textContent,'1 / 1 / 1');assert.equal(f.nodes['after-count'].textContent,'0 / 0 / 0');assert.equal(f.nodes['release-state'].textContent,'RELEASED');assert.equal(f.nodes.download.disabled,false);assert.equal(f.pipeline.classes.has('is-complete'),true);
  assert.ok(!f.nodes['signature-state'].textContent.toLowerCase().includes('verified'));
});
test('non-operators cannot create synthetic experiments',async()=>{
  const f=await fixture({callback:true,operator:false});assert.equal(f.nodes.seed.disabled,true);assert.equal(f.nodes.recover.disabled,true);
});
test('PKCE state mismatch never exchanges a code or enables controls',async()=>{
  const f=await fixture({callback:true,badState:true});assert.equal(f.calls.length,0);assert.equal(f.nodes.seed.disabled,true);assert.match(f.nodes.status.textContent,/state mismatch/);
});
test('paused database failure leaves a retryable seed and no fake results',async()=>{
  const f=await fixture({callback:true,seedFails:true});await f.nodes.seed.onclick();assert.equal(f.nodes.seed.disabled,false);assert.equal(f.nodes.recover.disabled,true);assert.match(f.nodes.status.textContent,/database is paused/);assert.equal(f.nodes['release-state'].textContent,'NOT RUN');
});
test('failed recovery is held, never presented as released',async()=>{
  const f=await fixture({callback:true,held:true});await f.nodes.seed.onclick();await f.nodes.recover.onclick();await f.timers.shift()();assert.equal(f.nodes['release-state'].textContent,'HELD');assert.equal(f.pipeline.classes.has('is-complete'),false);assert.equal(f.nodes.download.disabled,false);
});

test('sign-out clears in-memory tokens and uses the configured logout endpoint',async()=>{
 const f=await fixture({callback:true}); assert.equal(f.nodes.signout.hidden,false); f.nodes.signout.onclick();
 assert.equal(vm.runInContext('bearer === null && refreshToken === null',f.context),true);
 assert.equal(f.nodes.seed.disabled,true); const url=new URL(f.redirects[0]);
 assert.equal(url.origin,'https://auth.example.invalid'); assert.equal(url.pathname,'/logout');
 assert.equal(url.searchParams.get('logout_uri'),'http://localhost/ui');
});
