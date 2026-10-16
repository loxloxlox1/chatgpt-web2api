// Node + jsdom runner for sentinel SDK — proper DOM environment, no browser.
const { JSDOM } = require('jsdom');
const fs = require('fs');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
  url: 'https://chatgpt.com/',
  referrer: 'https://chatgpt.com/',
  userAgent: 'Mozilla/5.0 (X11; Linux x86_64; rv:157.0) Gecko/20100101 Firefox/157.0',
  pretendToBeVisual: true,       // gives requestAnimationFrame
  runScripts: 'outside-only',
});

const { window } = dom;
global.window = window;
global.document = window.document;
global.navigator = window.navigator;
global.self = window;
global.screen = window.screen;

// jsdom lacks screen & co
window.screen = { width:1920, height:1080, availWidth:1920, availHeight:1080, colorDepth:24, pixelDepth:24, availLeft:0, availTop:0, orientation:{type:'landscape-primary', angle:0, onchange:null} };
window.performance.memory = { jsHeapSizeLimit: 4294705152, totalJSHeapSize: 10000000, usedJSHeapSize: 5000000 };
if (!window.navigator.hardwareConcurrency) Object.defineProperty(window.navigator, 'hardwareConcurrency', { value: 8 });
if (!window.navigator.deviceMemory) Object.defineProperty(window.navigator, 'deviceMemory', { value: 8 });
window.InstallTrigger = undefined;
window.solana = undefined;

// node shims
window.btoa = s => Buffer.from(s, 'binary').toString('base64');
window.atob = s => Buffer.from(s, 'base64').toString('binary');
if (!window.crypto) window.crypto = require('crypto').webcrypto;

// stub fetch (VM may POST collector data)
window.fetch = async (u, o) => {
  console.error('FETCH', u);
  return { ok: true, status: 200, json: async () => ({}), text: async () => '' };
};

// read input
const lines = fs.readFileSync(0, 'utf8').trim().split('\n').filter(x => x.trim());
const REQ = JSON.parse(lines[0]);
const POOF = lines[1];
const DID = lines[2];
window.__FP_UID__ = lines[3] || '';
if (DID) window.document.cookie = 'oai-did=' + DID + '; path=/';

// fake iframe with contentWindow that replies like the real sentinel frame
let lastCw = null;
const origCreate = window.document.createElement.bind(window.document);
window.document.createElement = (tag) => {
  const el = origCreate(tag);
  if (String(tag).toLowerCase() === 'iframe') {
    const cw = {
      postMessage(msg) {
        setTimeout(async () => {
          console.error('FRAME MSG:', msg.type);
          try {
            const body = { p: msg.p ?? POOF, flow: msg.flow ?? 'chat', id: DID || undefined };
            const rr = await fetch('https://sentinel.openai.com/backend-api/sentinel/req', {
              method: 'POST',
              headers: { 'content-type': 'application/json', 'accept': '*/*',
                         'user-agent': navigator.userAgent,
                         'origin': 'https://chatgpt.com', 'referer': 'https://chatgpt.com/',
                         ...(DID ? { 'cookie': 'oai-did=' + DID } : {}) },
              body: JSON.stringify(body),
            });
            const chatReq = await rr.json();
            console.error('FRAME fetch req status', rr.status, 'hasDx', !!(chatReq.turnstile||{}).dx);
            window.dispatchEvent(new window.MessageEvent('message', {
              data: { type: 'response', requestId: msg.requestId,
                      result: { p: msg.p ?? POOF, cachedChatReq: chatReq, cachedProof: msg.p ?? POOF } },
              origin: window.location.origin,
              source: cw,
            }));
          } catch (e) {
            console.error('FRAME fetch err', e.message);
          }
        }, 5);
      }
    };
    Object.defineProperty(el, 'contentWindow', { get: () => cw });
    setTimeout(() => el.dispatchEvent(new window.Event('load')), 10);
  }
  return el;
};

const POWDBG=1;
let src = fs.readFileSync(__dirname + '/sdk3_fp.js', 'utf8');
src = src.replace('buildGenerateFailMessage(){return this[A(25)]+N(String(t??"e"))}','buildGenerateFailMessage(){console.error("POWFAIL", t); return this[A(25)]+N(String(t??"e"))}');
// intercept fetch: sentinel/req returns the chat-requirements payload fed on stdin
const realFetch = global.fetch;
global.fetch = async (url, opts) => {
  if (String(url).includes('sentinel/req')) {
    return new Response(JSON.stringify(REQ), { status: 200,
      headers: { 'content-type': 'application/json' } });
  }
  return realFetch(url, opts);
};
const SDK = eval(src + '; SentinelSDK;');

(async () => {
  try {
    const tok = await Promise.race([
      SDK.token({ flow: 'chat' }),
      new Promise(r => setTimeout(() => r('__TIMEOUT__'), 15000)),
    ]);
    if (tok === '__TIMEOUT__') console.log('ERR:timeout');
    else console.log(typeof tok === 'string' ? tok : JSON.stringify(tok));
  } catch (e) {
    console.log('ERR:' + (e && e.message));
  }
  process.exit(0);
})();
