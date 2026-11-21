// Disposable Chromium test of the actual call page, AudioWorklet and phone
// capture path. The caller provides a synthetic WAV and a pinned test identity.
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

async function run() {
  const [origin, passwordFile, wav, spki] = process.argv.slice(2);
  const browser = await chromium.launch({headless:true,args:[
    '--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream',
    '--mute-audio','--use-file-for-fake-audio-capture='+wav,'--ignore-certificate-errors-spki-list='+spki
  ]});
  try {
    const context = await browser.newContext({permissions:['microphone']});
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    page.on('pageerror',e=>console.error('Browser error:',e.message));
    page.on('response',r=>{if(r.status()>=400)console.error('HTTP',r.status(),new URL(r.url()).pathname);});
    page.on('request',async r=>{if(r.method()==='POST' && new URL(r.url()).pathname==='/login'){
      const h=await r.allHeaders(); console.log('Login sends exact origin:',h.origin===origin, 'opaque:',h.origin==='null');
    }});
    await page.goto(origin);
    await page.getByLabel('Room passphrase').fill(fs.readFileSync(passwordFile,'utf8').trim());
    await page.getByRole('button',{name:'Sign in'}).click();
    await page.getByRole('button',{name:'Call room'}).click();
    try {
      await page.waitForFunction(()=>document.querySelector('#intercom-status').textContent==='Call connected');
      console.log('Browser call connected');
      await page.waitForFunction(()=>/voice confirmations [1-9]/.test(document.querySelector('#levels').textContent),null,{timeout:10000});
    } catch(error) {
      const message=await page.locator('#intercom-status').innerText();
      const room=await page.evaluate(async()=>await api('/intercom'));
      throw Error(`${error.message}; page: ${message}; room metrics: ${JSON.stringify(room.metrics)}`);
    }
    await page.getByRole('button',{name:'Mute my microphone'}).click();
    if(!await page.getByRole('button',{name:'Unmute my microphone'}).isVisible())
      throw Error('Microphone mute did not take effect');
    await page.getByRole('button',{name:'End call'}).click();
    await page.waitForFunction(()=>document.querySelector('#intercom-status').textContent==='Call ended');
    console.log('Mobile call page, AudioWorklet, capture, duplex confirmation, mute and end passed in Chromium.');
  } finally {
    await browser.close();
  }
}
run().catch(error=>{console.error(error.message);process.exitCode=1;});
