function inspectPage(expected = null, execute = false, deadline = 0) {
  const body = document.body?.innerText || '';
  const verification = /security|captcha|verify/i.test(location.pathname) || /请完成安全验证|请完成验证|访问异常|滑动验证/.test(body);
  const isElementVisible = element => {
    if (!element || element.getClientRects().length === 0) return false;
    if (typeof getComputedStyle === 'function') {
      const style = getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) return false;
    }
    return true;
  };
  const visible = selector => [...document.querySelectorAll(selector)].filter(isElementVisible);
  const cleanText = element => {
    const raw = element?.innerText ?? element?.textContent;
    return typeof raw === 'string' ? raw.replace(/\s+/g, ' ').trim() || null : null;
  };
  const hasPUA = value => /[\uE000-\uF8FF]/.test(value || '');
  const safeSalary = value => (value && !hasPUA(value) ? value : null);
  const loginDialog = visible('.login-dialog, .boss-login-dialog, .login-register-dialog').length > 0;
  const signedIn = visible('.nav-figure, .user-nav .user-name, .nav-user .user-name, [ka="header-username"]').length > 0;
  const jobLinks = [...document.querySelectorAll('a[href*="/job_detail/"]')].filter(link => {
    try { const url = new URL(link.href); return url.hostname === 'www.zhipin.com' && /^\/job_detail\/[A-Za-z0-9_-]+\.html$/.test(url.pathname); }
    catch (_) { return false; }
  }).length;
  const cards = [...document.querySelectorAll('.job-card-wrapper, .job-card-box')];
  const rows = cards.map(card => {
    const anchor = card.querySelector('a[href*="/job_detail/"]');
    if (!anchor) return null;
    let url;
    try { url = new URL(anchor.href); } catch (_) { return null; }
    const match = url.origin === 'https://www.zhipin.com' && url.pathname.match(/^\/job_detail\/([A-Za-z0-9_-]{1,128})\.html$/);
    if (!match) return null;
    const text = selector => cleanText(card.querySelector(selector));
    const title = text('.job-name') || text('.job-title');
    const company = text('.company-name, .company-title, .brand-name');
    const salary = text('.job-salary, .salary, .salary-text');
    if (!title) return null;
    const tags = [...card.querySelectorAll('.tag-list li')].map(item => cleanText(item)).filter(Boolean);
    const experience = text('.job-limit .text-desc') || tags.find(item => /经验|应届|在校|不限|\d+-\d+年|\d+年以上/.test(item)) || null;
    const education = text('.job-limit .edu, .job-degree') || tags.find(item => /博士|硕士|本科|大专|中专|高中|学历不限/.test(item)) || null;
    const hrActive = text('.boss-online-tag, .boss-active-time, .info-public em, .active-status');
    const companyInfo = [...card.querySelectorAll('.company-tag-list li, .company-info .tag-list li')].map(item => cleanText(item)).filter(Boolean);
    const job = {job_id: match[1], url: `${url.origin}${url.pathname}`, title, company,
      salary_text: safeSalary(salary),
      location: text('.company-location, .job-area, .job-location'),
      experience,
      education,
      hr_active: hrActive,
      company_info: companyInfo,
      tags};
    return {job, source: {job_id: job.job_id, url: url.href}};
  }).filter(Boolean);
  const jobs = rows.map(row => row.job);
  const privateJobUrls = rows.map(row => row.source);
  const pick = selector => cleanText(visible(selector)[0]);
  const companyName = visible('.sider-company .company-info a')
    .map(element => cleanText(element)).find(text => text && text.length <= 80) || null;
  const detailId = location.protocol === 'https:' && location.host === 'www.zhipin.com' ? location.pathname.match(/^\/job_detail\/([A-Za-z0-9_-]+)\.html$/)?.[1] || null : null;
  const contactControls = visible('.job-banner .btn-container .btn-startchat, .job-primary .btn-container .btn-startchat');
  const contactButtons = contactControls.map(element => cleanText(element) || '');
  const detailCompanyInfo = visible('.sider-company .company-tag-list li, .sider-company [class*="tag"] li').map(el => cleanText(el)).filter(Boolean);
  const detail = detailId ? {job_id: detailId, url: `https://www.zhipin.com/job_detail/${detailId}.html`,
    title: pick('.job-banner .name h1, .job-primary .name h1'), company: companyName,
    salary_text: safeSalary(pick('.job-banner .salary, .job-primary .salary')),
    location: pick('.job-banner .job-address, .job-primary .job-address, .job-location'),
    hr_active: pick('.boss-active-time, .job-boss-info .active-time, .boss-online-tag'),
    company_info: detailCompanyInfo,
    description: pick('.job-sec-text, .job-detail-section .text, .job-detail .text'),
    contact_status: contactButtons.length === 1 ? (contactButtons[0] === '继续沟通' ? 'already_contacted' : contactButtons[0] === '立即沟通' ? 'not_contacted' : 'unknown') : 'unknown'} : null;
  const queryInput = visible('input[name="query"], input[placeholder*="搜索"], input.search-input, .search-input input, input.ipt-search')[0];
  const params = new URL(location.href).searchParams;
  const filterLabels = visible('.filter-select-box, .filter-select, .search-filter .selected')
    .map(element => cleanText(element)).filter(Boolean);
  const filterQuery = {};
  for (const key of ['experience', 'salary', 'jobType', 'degree', 'industry', 'scale', 'stage']) {
    const values = params.getAll(key);
    if (values.length) filterQuery[key] = values;
  }

  const knownEmpty = cards.length === 0 && /没有找到相关职位|暂无相关职位|没有相关职位|未找到相关职位|暂无符合条件的职位/.test(body);
  const endMessages = visible('.search-job-result .load-more, .job-list-container .load-more, .job-list-box .load-more, .job-list-container .no-more, .job-list-box .no-more');
  const siteEnd = knownEmpty || endMessages.some(el => /^(没有更多(?:职位|岗位)?了|没有更多职位|暂无更多职位|全部职位已加载)[。！]?$/.test(cleanText(el)));
  const search = {keyword: queryInput?.value?.trim() || null,
    city: pick('.city-label, .search-city, .city-switch, .city-select, .city-label-box'),
    filters: filterLabels, filter_query: filterQuery, site_end: siteEnd};
  const modalText = visible('.dialog-wrap, .boss-popup__wrapper, .el-dialog, .zp-dialog, .toast, [role="dialog"], .dialog-container')
    .map(el => cleanText(el)).filter(Boolean).join(' ');
  const bannerStatus = pick('.job-banner .job-status, .job-primary .job-status') || '';
  const siteRejection = /今日沟通.*上限|沟通次数已用完|今日主动沟通.*达上限|明日再来/.test(modalText) ? 'DAILY_LIMIT_REACHED' :
    /完善.*简历|先添加.*简历|在线简历.*完整/.test(modalText) ? 'RESUME_INCOMPLETE' :
    /职位已关闭|停止招聘|该职位已下线|岗位已失效/.test(`${modalText} ${bannerStatus}`) ? 'JOB_CLOSED' : null;
  if (expected) {
    const fail = reason => ({outcome: 'not_clicked', reason});
    if (verification) return fail('VERIFICATION_REQUIRED');
    if (loginDialog || !signedIn) return fail('AUTH_REQUIRED');
    if (siteRejection === 'JOB_CLOSED') return fail('JOB_CLOSED');
    const normalize = value => typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : '';
    if (!detail || detail.job_id !== expected.job_id || !normalize(expected.title) || !normalize(expected.company) ||
        normalize(detail.title) !== normalize(expected.title) || normalize(detail.company) !== normalize(expected.company)) return fail('JOB_MISMATCH');
    if (contactControls.length !== 1) return fail('BUTTON_NOT_UNIQUE');
    const button = contactControls[0];
    if (button.disabled || button.getAttribute('aria-disabled') === 'true' || button.classList.contains('disabled')) return fail('BUTTON_UNAVAILABLE');
    if (detail.contact_status === 'already_contacted') return {outcome: 'already_contacted'};
    if (detail.contact_status !== 'not_contacted') return fail('BUTTON_UNAVAILABLE');
    if (!execute) return {outcome: 'ready'};
    if (!Number.isFinite(deadline) || deadline <= Date.now()) return fail('DEADLINE_EXCEEDED');
    button.click();
    return {outcome: 'clicked'};
  }
  return {state: verification ? 'verification_required' : loginDialog ? 'login_required' : signedIn ? 'authenticated' : 'unrecognized',
    jobLinks, jobCards: cards.length, jobs, privateJobUrls, search, detail,
    hasDescription: !!detail?.description, site_rejection: siteRejection};
}
