// Optional browser acceptance: owns its loopback server and temporary database.
// Requires playwright and Chromium; set TG_CHROMIUM_PATH / TG_TEST_PYTHON if needed.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {spawn} = require('node:child_process');
const {randomBytes} = require('node:crypto');
const {once} = require('node:events');

const root = path.resolve(__dirname, '..');
const out = fs.mkdtempSync(path.join(os.tmpdir(), 'tg-audit-ui-'));
const token = randomBytes(32).toString('base64url');
const params = {width:64,height:64,tile_size:32,max_iter:1,x_min:-2.2,x_max:1,y_min:-1.2,y_max:1.2};
const pause = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, label, timeout=15000) {
  const deadline=Date.now()+timeout;
  while(Date.now()<deadline) {if(await fn())return; await pause(100);}
  throw Error('Timeout: '+label);
}
async function freePort() {
  const s=net.createServer();s.listen(0,'127.0.0.1');await once(s,'listening');
  const port=s.address().port;await new Promise(r=>s.close(r));return port;
}
(async()=>{
  let server,browser,fd;
  try {
    const port=await freePort(), base='http://127.0.0.1:'+port;
    const env={...process.env,TG_DB_PATH:path.join(out,'test.db'),TG_RESET_DB:'0',
      TG_SECURITY_AUDIT_TOKEN:token,PYTHON_DOTENV_DISABLED:'1'};
    delete env.OPENROUTER_API_KEY;delete env.ANTHROPIC_API_KEY;
    fd=fs.openSync(path.join(out,'server.log'),'w');
    const python=process.env.TG_TEST_PYTHON || path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
    server=spawn(python,['-m','uvicorn','main:app','--app-dir',path.join(root,'backend'),'--host','127.0.0.1','--port',String(port)],
      {cwd:root,env,stdio:['ignore',fd,fd]});
    server.on('error',()=>{});
    await until(async()=>{try{return (await fetch(base+'/healthz')).ok;}catch{return false;}},'test server');
    const executablePath=process.env.TG_CHROMIUM_PATH || (process.platform==='linux'?'/usr/bin/chromium':undefined);
    browser=await chromium.launch({executablePath,args:['--no-sandbox']});
    const context=await browser.newContext({viewport:{width:1440,height:1100}});
    const page=await context.newPage();page.setDefaultTimeout(7000);
    const errors=[],credentialPaths=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',async r=>{if((await r.allHeaders()).authorization)credentialPaths.push(new URL(r.url()).pathname);});
    await page.goto(base+'/');
    await page.waitForSelector('#audit-panel');
    assert.equal(await page.locator('#audit-allowed').innerText(),'—');
    assert.equal(await page.locator('#audit-events li').count(),0);
    assert.deepEqual(credentialPaths,[]);

    const good=await(await fetch(base+'/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({task_type:'mandelbrot_v1',params})})).json();
    const bad=await fetch(base+'/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({task_type:'arbitrary_python',params:{command:'<img src=x onerror=alert(1)>'}})});
    assert.equal(bad.status,422);
    await page.fill('#audit-token',token);await page.click('#audit-unlock');
    await page.waitForFunction(()=>document.querySelector('#audit-allowed').textContent==='1');
    assert.equal(await page.locator('#audit-blocked').innerText(),'1');
    assert.equal(await page.locator('#audit-events li').count(),2);
    assert.ok((await page.locator('#audit-events').innerText()).includes('UNAPPROVED_WORKLOAD'));
    assert.equal(await page.locator('#audit-token').inputValue(),'');
    assert.equal(await page.evaluate(t=>JSON.stringify(localStorage).includes(t)||JSON.stringify(sessionStorage).includes(t),token),false);
    assert.equal(await page.locator('#audit-events img').count(),0);
    console.log('PASS locked by default, real events/counters, credential kept out of storage and safe labels');

    // Exercise actual browser Worker code while the new audit panel polls.
    const contributor=await context.newPage();
    contributor.on('pageerror',e=>errors.push(e.message));
    await contributor.goto(base+'/contribute.html');
    await contributor.fill('#name','Audit browser');await contributor.fill('#owner','audit-ui-test');
    await contributor.uncheck('#auto-pause');await contributor.click('#start');
    await until(async()=>(await(await fetch(base+'/api/jobs/'+good.job_id+'?since=999999')).json()).status==='completed','actual browser computation');
    await contributor.click('#stop');await contributor.close();
    await page.waitForFunction(()=>document.querySelector('#c-done').textContent==='4');
    console.log('PASS real browser computes accepted job; Dashboard/credit polling continues');

    await page.route('**/api/security/events*',r=>r.fulfill({status:503,contentType:'application/json',body:'{"detail":"security_events_unavailable"}'}));
    await page.waitForFunction(()=>document.querySelector('#audit-status').textContent.includes('Security events unavailable'),null,{timeout:12000});
    assert.equal(await page.locator('#audit-allowed').innerText(),'—');
    assert.equal(await page.locator('#audit-events li').count(),0);
    await page.unroute('**/api/security/events*');
    await page.waitForFunction(()=>document.querySelector('#audit-allowed').textContent==='1',null,{timeout:12000});
    await page.screenshot({path:path.join(out,'dashboard-desktop.png'),fullPage:true});
    await page.setViewportSize({width:375,height:812});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.locator('#audit-panel').screenshot({path:path.join(out,'audit-mobile.png')});
    console.log('PASS unavailable/retry states and responsive rendering');

    await page.click('#audit-lock');
    assert.equal(await page.locator('#audit-events li').count(),0);
    assert.equal(await page.locator('#audit-allowed').innerText(),'—');
    await page.fill('#audit-token','wrong-credential');await page.click('#audit-unlock');
    await page.waitForFunction(()=>document.querySelector('#audit-status').textContent.includes('Access denied'));
    assert.equal(await page.locator('#audit-events li').count(),0);

    let release,arrived=false;
    const gate=new Promise(r=>release=r);
    await page.route('**/api/security/events*',async r=>{
      const response=await r.fetch();arrived=true;await gate;
      try{await r.fulfill({response});}catch{} // Lock aborts this in-flight fetch.
    });
    await page.fill('#audit-token',token);await page.click('#audit-unlock');
    await until(async()=>arrived,'delayed read');
    await page.click('#audit-lock');release();await pause(300);
    assert.equal(await page.locator('#audit-events li').count(),0);
    assert.equal(await page.locator('#audit-allowed').innerText(),'—');
    await page.unroute('**/api/security/events*');
    await page.fill('#audit-token',token);await page.click('#audit-unlock');
    await page.waitForFunction(()=>document.querySelector('#audit-allowed').textContent==='1');
    await page.reload();
    assert.equal(await page.locator('#audit-events li').count(),0);
    assert.equal(await page.locator('#audit-token').inputValue(),'');
    assert.ok(credentialPaths.every(p=>p==='/api/security/events'));
    console.log('PASS rejected credential, explicit lock, delayed-response isolation and reload forgets credential');

    await page.goto(base+'/?mock=1');
    assert.equal(await page.locator('#audit-unlock').isDisabled(),true);
    assert.ok((await page.locator('#audit-status').innerText()).includes('Mock data is not security evidence'));
    assert.deepEqual(errors,[]);
    console.log('PASS mock mode contains no fabricated security evidence; zero page errors');
    fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({passed:true,scope:'One cloud host; actual browser worker and isolated SQLite.',screenshots:['dashboard-desktop.png','audit-mobile.png'],errors},null,2));
    console.log('Evidence: '+out);
    await context.close();
  }finally{
    if(browser)await browser.close();
    if(server && server.exitCode===null){const done=once(server,'exit');server.kill('SIGTERM');await Promise.race([done,pause(5000)]);if(server.exitCode===null)server.kill('SIGKILL');}
    if(fd!==undefined)fs.closeSync(fd);
  }
})().catch(e=>{console.error(e.message);process.exitCode=1;});
