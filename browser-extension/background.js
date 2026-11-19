importScripts('inspect.js');
let socket = null;
let socketToken = null;
let socketAuthenticated = false;
const MAX_JOB_SOURCES = 2000;
const BASE_RECONNECT_MS = 2000;
const MAX_RECONNECT_MS = 60000;
const SEARCH_TIMEOUT_MS = 45000;
const SCROLL_TIMEOUT_MS = 3000;
const SEND_CONTRACT = 4;
const jobSources = new Map();
let reconnectTimer = null;
let reconnectDelayMs = BASE_RECONNECT_MS;
let jobSourcesReady = false;
let commandQueue = Promise.resolve();

function withTimeout(promise, ms, code = 'PAGE_TIMEOUT') {
  let timer = null;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(code)), ms);
    }),
  ]).finally(() => { if (timer) clearTimeout(timer); });
}

function safeSend(ws, payload) {
  if (!ws || (typeof ws.readyState === 'number' && ws.readyState !== WebSocket.OPEN)) return false;
  try {
    ws.send(JSON.stringify(payload));
    return true;
  } catch (_) {
    return false;
  }
}

function setJobSource(jobId, source) {
  if (jobSources.has(jobId)) jobSources.delete(jobId);
  jobSources.set(jobId, source);
}

function getJobSource(jobId) {
  const source = jobSources.get(jobId);
  if (source !== undefined) {
    jobSources.delete(jobId);
    jobSources.set(jobId, source);
  }
  return source;
}

async function restoreJobSources() {
  if (jobSourcesReady) return;
  const saved = await chrome.storage.session?.get('jobSources');
  for (const [jobId, source] of Object.entries(saved?.jobSources || {})) setJobSource(jobId, source);
  jobSourcesReady = true;
}

async function saveJobSources() {
  if (!chrome.storage.session) return;
  const entries = [...jobSources.entries()].slice(-MAX_JOB_SOURCES);
  jobSources.clear();
  for (const [jobId, source] of entries) jobSources.set(jobId, source);
  await chrome.storage.session.set({jobSources: Object.fromEntries(entries)});
}

async function waitForTab(tabId, deadline = Date.now() + 10000) {
  while (Date.now() < deadline) {
    const tab = await withTimeout(chrome.tabs.get(tabId), deadline - Date.now());
    if (tab.status === 'complete') return;
    await new Promise(resolve => setTimeout(resolve, Math.min(250, Math.max(0, deadline - Date.now()))));
  }
  throw new Error('PAGE_TIMEOUT');
}

async function sendAndConfirm(tabId, expected, source) {
  if (Date.now() >= expected.deadline) return {outcome: 'not_clicked', reason: 'DEADLINE_EXCEEDED'};
  let outcome = {outcome: 'unknown'};
  try {
    const [execution] = await withTimeout(chrome.scripting.executeScript({target: {tabId}, func: inspectPage,
      args: [expected, true, expected.deadline]}), expected.deadline - Date.now());
    outcome = execution?.result || outcome;
  } catch (_) { /* Navigation may destroy the context after clicking. */ }
  if (['not_clicked', 'already_contacted'].includes(outcome.outcome)) return outcome;
  const confirmedResult = (page, allowRejection = false) => {
    const job = page?.detail;
    const norm = value => typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : '';
    if (page?.state !== 'authenticated' || job?.job_id !== expected.job_id ||
        norm(job.title) !== norm(expected.title) || norm(job.company) !== norm(expected.company)) return null;
    if (job.contact_status === 'already_contacted') return {outcome: 'sent'};
    if (allowRejection && job.contact_status === 'not_contacted' && page.site_rejection) {
      return {outcome: 'rejected', reason: page.site_rejection};
    }
    return null;
  };
  const until = Math.min(expected.deadline, Date.now() + 3000);
  while (Date.now() < until) {
    try {
      const [{result}] = await withTimeout(chrome.scripting.executeScript({target: {tabId}, func: inspectPage}), until - Date.now());
      const confirmed = confirmedResult(result, outcome.outcome === 'clicked');
      if (confirmed) return confirmed;
      if (['verification_required', 'login_required'].includes(result?.state)) return {outcome: 'unknown'};
    } catch (_) {}
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  if (Date.now() >= expected.deadline) return {outcome: 'unknown'};
  let verificationTab;
  try {
    verificationTab = await withTimeout(chrome.tabs.create({url: source.url, active: false}),
      Math.min(5000, expected.deadline - Date.now()));
    await waitForTab(verificationTab.id, Math.min(Date.now() + 10000, expected.deadline));
    const until = Math.min(expected.deadline, Date.now() + 5000);
    while (Date.now() < until) {
      const [{result}] = await withTimeout(chrome.scripting.executeScript({target: {tabId: verificationTab.id}, func: inspectPage}), until - Date.now());
      const confirmed = confirmedResult(result);
      if (confirmed) return confirmed;
      if (['verification_required', 'login_required'].includes(result?.state)) break;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
  } catch (_) {} finally {
    if (verificationTab?.id) await chrome.tabs.remove(verificationTab.id).catch(() => {});
  }
  return {outcome: 'unknown'};
}

async function scrollTabToBottom(tabId) {
  await chrome.scripting.executeScript({
    target: {tabId},
    func: function scrollPageToBottom() {
      const scrollbar = document.scrollingElement;
      scrollbar.scrollTop = scrollbar.scrollHeight;
    },
  });
}

const normalizeCity = city => city?.trim().replace(/市$/, '').trim();
const filters = page => JSON.stringify({labels: [...(page.search?.filters || [])].sort(),
  query: Object.fromEntries(Object.entries(page.search?.filter_query || {}).sort().map(([key, values]) => [key, [...values].sort()]))});

function requireSignedIn(page) {
  if (page.state !== 'authenticated') {
    throw new Error(page.state === 'verification_required' ? 'VERIFICATION_REQUIRED' : 'AUTH_REQUIRED');
  }
}

async function collectSearch(tabId, inspect, initial, payload, expectedFilters, deadline) {
  const jobs = new Map();
  let page = initial;
  const collect = () => {
    requireSignedIn(page);
    if (page.search?.keyword !== payload.keyword ||
        normalizeCity(page.search.city) !== normalizeCity(payload.city) || filters(page) !== expectedFilters) {
      throw new Error('FILTER_NOT_APPLIED');
    }
    for (const [index, job] of page.jobs.entries()) {
      if (!jobs.has(job.job_id) && jobs.size < payload.limit) {
        jobs.set(job.job_id, job);
        setJobSource(job.job_id, {url: page.privateJobUrls[index].url});
      }
    }
  };
  collect();
  if (!jobs.size && !page.search.site_end) throw new Error('SEARCH_NOT_RECOGNIZED');

  let stopReason = 'deadline';
  while (Date.now() < deadline) {
    if (jobs.size === payload.limit || page.search.site_end) {
      stopReason = jobs.size === payload.limit ? 'limit' : 'site_end';
      break;
    }
    const count = jobs.size;
    const until = Math.min(Date.now() + SCROLL_TIMEOUT_MS, deadline);
    try {
      await withTimeout(scrollTabToBottom(tabId), Math.max(1, until - Date.now()));
      while (Date.now() < until) {
        await new Promise(resolve => setTimeout(resolve, Math.min(300, until - Date.now())));
        if (Date.now() >= until) break;
        page = await inspect(until);
        collect();
        if (jobs.size > count || page.search.site_end) break;
      }
    } catch (error) {
      if (error.message !== 'PAGE_TIMEOUT') throw error;
    }
    if (jobs.size === count && !page.search.site_end) {
      stopReason = Date.now() >= deadline ? 'deadline' : 'no_new_ids';
      break;
    }
  }
  await saveJobSources();
  return {jobs: [...jobs.values()], search: {...page.search, stop_reason: stopReason}};
}

async function connect(token) {
  if (socket && socketToken === token && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
  if (socket) socket.close();
  socket = new WebSocket('ws://127.0.0.1:43286/');
  socketToken = token;
  socketAuthenticated = false;
  const current = socket;
  let authenticated = false;
  current.addEventListener('message', async event => {
    let request;
    try { request = JSON.parse(event.data); } catch (_) { return; }
    if (request.type === 'challenge') {
      try {
        if (typeof request.nonce !== 'string' || !/^[A-Za-z0-9_-]{20,100}$/.test(request.nonce) || !/^[0-9a-f]{64}$/.test(request.proof)) throw new Error('INVALID_CHALLENGE');
        const encoder = new TextEncoder();
        const key = await crypto.subtle.importKey('raw', encoder.encode(token), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign', 'verify']);
        const signature = new Uint8Array(request.proof.match(/../g).map(value => parseInt(value, 16)));
        if (!await crypto.subtle.verify('HMAC', key, signature, encoder.encode(`server:${request.nonce}`))) throw new Error('INVALID_PROOF');
        const proof = await crypto.subtle.sign('HMAC', key, encoder.encode(`client:${request.nonce}`));
        safeSend(current, {type: 'authenticate', proof: [...new Uint8Array(proof)].map(byte => byte.toString(16).padStart(2, '0')).join('')});
      } catch (_) { current.close(); }
      return;
    }
    if (request.type === 'connected') {
      authenticated = true;
      socketAuthenticated = true;
      reconnectDelayMs = BASE_RECONNECT_MS;
      return;
    }
    if (!authenticated) return;
    if (!request.id || !['status', 'search_jobs', 'read_detail', 'send_once'].includes(request.command)) return;
    const searching = request.command === 'search_jobs';
    const sending = request.command === 'send_once';
    const searchDeadline = searching ? Date.now() + SEARCH_TIMEOUT_MS : Infinity;
    let temporaryTab = null, releaseCommand = null, actionStarted = false;
    try {
      const previousCommand = commandQueue;
      commandQueue = new Promise(resolve => { releaseCommand = resolve; });
      await previousCommand;
      await restoreJobSources();
      const payload = request.payload || {};
      const source = getJobSource(payload.job_id);
      let tab, expectedFilters;
      const inspect = async (deadline = searchDeadline) => {
        const remaining = Math.min(5000, deadline - Date.now());
        if (remaining <= 0) throw new Error('PAGE_TIMEOUT');
        const res = await withTimeout(chrome.scripting.executeScript({target: {tabId: tab.id}, func: inspectPage}), remaining);
        if (!res?.[0]?.result) throw new Error('BROWSER_ERROR');
        return res[0].result;
      };
      if (searching && Date.now() >= searchDeadline) throw new Error('PAGE_TIMEOUT');
      if (sending && payload.send_contract !== SEND_CONTRACT) throw new Error('EXTENSION_OUTDATED');
      if (['read_detail', 'send_once'].includes(request.command)) {
        if (typeof payload.job_id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(payload.job_id) || !source?.url) throw new Error('JOB_NOT_IN_SEARCH');
        const url = new URL(source.url);
        if (url.protocol !== 'https:' || url.host !== 'www.zhipin.com' || url.pathname !== `/job_detail/${payload.job_id}.html`) throw new Error('JOB_MISMATCH');
        if (sending && (!Number.isFinite(payload.deadline) || Date.now() >= payload.deadline)) throw new Error('DEADLINE_EXCEEDED');
        temporaryTab = await withTimeout(chrome.tabs.create({url: url.href, active: false}), 5000, 'PAGE_TIMEOUT');
        await waitForTab(temporaryTab.id, Math.min(Date.now() + 10000, sending ? payload.deadline : Infinity));
        tab = temporaryTab;
      } else {
        const tabs = await withTimeout(chrome.tabs.query({url: 'https://www.zhipin.com/*'}), 5000, 'NO_TAB');
        const activeTabs = tabs.filter(item => !item.discarded);
        const pool = activeTabs.length ? activeTabs : tabs;
        tab = pool.find(item => item.active && item.url?.includes('/web/geek/jobs')) || pool.find(item => item.url?.includes('/web/geek/jobs')) || pool[0];
        if (!tab?.id) throw new Error('NO_TAB');
      }
      if (searching) {
        const {keyword, city, limit} = payload;
        if (limit === undefined) throw new Error('EXTENSION_OUTDATED');
        if (typeof keyword !== 'string' || !keyword.trim() || keyword.length > 100 ||
            typeof city !== 'string' || !city.trim() || city.length > 100 ||
            !Number.isInteger(limit) || limit < 1 || limit > 150) throw new Error('FILTER_NOT_APPLIED');
        const url = new URL(tab.url);
        if (url.pathname !== '/web/geek/jobs') throw new Error('SEARCH_NOT_RECOGNIZED');
        const prior = await inspect();
        requireSignedIn(prior);
        if (normalizeCity(prior.search?.city) !== normalizeCity(city)) throw new Error('CITY_NOT_MATCHED');
        expectedFilters = filters(prior);
        url.searchParams.set('query', keyword);
        url.searchParams.delete('page');
        temporaryTab = await withTimeout(chrome.tabs.create({
          url: url.href, active: true, windowId: tab.windowId, openerTabId: tab.id,
        }),
          Math.max(1, Math.min(5000, searchDeadline - Date.now())));
        await waitForTab(temporaryTab.id, Math.min(Date.now() + 10000, searchDeadline));
        tab = temporaryTab;
      }
      let result = await inspect(sending ? payload.deadline : searchDeadline);
      const ready = page => searching ?
        ((page.jobs.length || page.search?.site_end) && page.search?.keyword === payload.keyword) :
        (page.detail?.title && page.detail?.company && (sending ? page.detail.contact_status !== 'unknown' || page.site_rejection : page.detail.description));
      if (request.command !== 'status') {
        const limit = Math.min(Date.now() + 5000, sending ? payload.deadline : searchDeadline);
        while (Date.now() < limit && !['verification_required', 'login_required'].includes(result.state) &&
               !(result.state === 'authenticated' && ready(result))) {
          await new Promise(resolve => setTimeout(resolve, 250));
          if (Date.now() >= limit) break;
          result = await inspect(limit);
        }
        requireSignedIn(result);
      }
      let data;
      if (searching) {
        data = await collectSearch(tab.id, inspect, result, payload, expectedFilters, searchDeadline);
      } else if (request.command === 'status') {
        data = {state: result.state, bridge_protocol: 1, send_contract: SEND_CONTRACT,
          extension_version: chrome.runtime.getManifest().version, job_cards: result.jobCards, search: result.search};
      } else if (sending) {
        actionStarted = true;
        data = await sendAndConfirm(tab.id, payload, source);
      } else {
        if (!ready(result) || result.detail.job_id !== payload.job_id) throw new Error('DETAIL_NOT_RECOGNIZED');
        data = result.detail;
      }
      safeSend(current, {id: request.id, ok: true, data});
    } catch (error) {
      const known = ['NO_TAB', 'PAGE_TIMEOUT', 'JOB_NOT_IN_SEARCH', 'VERIFICATION_REQUIRED', 'AUTH_REQUIRED',
        'SEARCH_NOT_RECOGNIZED', 'DETAIL_NOT_RECOGNIZED', 'JOB_MISMATCH', 'CITY_NOT_MATCHED',
        'FILTER_NOT_APPLIED', 'EXTENSION_OUTDATED', 'DEADLINE_EXCEEDED', 'JOB_CLOSED'];
      const reason = known.includes(error?.message) ? error.message : 'BROWSER_ERROR';
      if (request.command === 'send_once') {
        safeSend(current, {id: request.id, ok: true, data: actionStarted ? {outcome: 'unknown'} : {outcome: 'not_clicked', reason}});
      } else {
        safeSend(current, {id: request.id, ok: false, code: reason});
      }
    } finally {
      if (temporaryTab?.id) await chrome.tabs.remove(temporaryTab.id).catch(() => {});
      releaseCommand?.();
    }
  });
  current.addEventListener('close', () => {
    if (socket === current) {
      socket = null;
      socketToken = null;
      socketAuthenticated = false;
    }
    if (reconnectTimer) clearTimeout(reconnectTimer);
    const delay = reconnectDelayMs;
    reconnectDelayMs = Math.min(MAX_RECONNECT_MS, reconnectDelayMs * 2);
    reconnectTimer = setTimeout(reconnect, delay);
  });
  current.addEventListener('error', () => { try { current.close(); } catch (_) {} });
}

async function reconnect() {
  const {bridgeToken} = await chrome.storage.local.get('bridgeToken');
  if (typeof bridgeToken === 'string' && /^[A-Za-z0-9_-]{40,100}$/.test(bridgeToken)) await connect(bridgeToken);
}
chrome.alarms.create('boss-bridge', {periodInMinutes: 1});
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'boss-bridge') reconnect(); });
reconnect();

chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  if (message?.type === 'get_bridge_status') {
    chrome.storage.local.get('bridgeToken').then(({bridgeToken}) => {
      respond({
        connected: socket?.readyState === WebSocket.OPEN && socketAuthenticated,
        connecting: socket?.readyState === WebSocket.CONNECTING,
        paired: typeof bridgeToken === 'string' && /^[A-Za-z0-9_-]{40,100}$/.test(bridgeToken),
      });
    });
    return true;
  }
  if (message?.type !== 'connect_bridge' || typeof message.token !== 'string') return;
  reconnectDelayMs = BASE_RECONNECT_MS;
  chrome.storage.local.set({bridgeToken: message.token}).then(() => connect(message.token));
  respond({started: true});
});
