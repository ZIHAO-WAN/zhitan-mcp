const status = document.getElementById('status');
const connectionStatus = document.getElementById('connection-status');
if (chrome.runtime?.sendMessage && connectionStatus) {
  chrome.runtime.sendMessage({type: 'get_bridge_status'}).then(state => {
    if (!state) return;
    connectionStatus.textContent = state.connected ? '桥接状态：已连接到本地 MCP' :
      state.connecting ? '桥接状态：正在连接本地 MCP…' :
      state.paired ? '桥接状态：已保存密钥，等待 MCP 服务端启动' : '桥接状态：尚未配对';
  }).catch(() => {});
}
document.getElementById('check').addEventListener('click', async () => {
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id || !tab.url?.startsWith('https://www.zhipin.com/')) {
      status.textContent = '请先切换到 Boss 官网标签页。';
      return;
    }
    const [{result}] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: inspectPage});
    const state = ({authenticated: '已识别到登录状态。', login_required: '页面要求登录。',
      verification_required: '页面要求安全验证，请自行完成。', unrecognized: '无法确认登录状态。'})[result.state] || '无法确认页面状态。';
    const detail = result.detail ? `详情：职位${result.detail.title ? '已识别' : '未识别'}、公司${result.detail.company ? '已识别' : '未识别'}、正文${result.detail.description ? '已识别' : '未识别'}、沟通状态${result.detail.contact_status}。` : '';
    const identity = result.detail?.title && result.detail?.company ? `\n职位：${result.detail.title}\n公司：${result.detail.company}` : '';
    const search = result.jobCards ? `\n关键词${result.search?.keyword ? '已识别' : '未识别'}，城市${result.search?.city ? '已识别' : '未识别'}。` : '';
    status.style.whiteSpace = 'pre-wrap';
    status.textContent = `${state} 岗位链接：${result.jobLinks}，岗位卡片：${result.jobCards}，可提取职位：${result.jobs.length}，其中有公司：${result.jobs.filter(job => job.company).length}。${detail}${identity}${search}`;
  } catch (error) {
    const reason = String(error?.message || '');
    if (!chrome.scripting?.executeScript) status.textContent = '诊断 A：扩展脚本权限不可用，请在扩展管理页重新加载扩展。';
    else if (/permission|cannot access|access denied|not allowed/i.test(reason)) status.textContent = '诊断 B：浏览器未允许扩展读取此页面，请检查扩展的站点访问权限。';
    else status.textContent = '诊断 C：页面执行失败，请确认当前是 Boss 搜索结果页。';
  }
});
document.getElementById('connect').addEventListener('click', async () => {
  const input = document.getElementById('pairing');
  const output = document.getElementById('connection-status');
  const token = input.value.trim();
  if (!/^[A-Za-z0-9_-]{40,100}$/.test(token)) { output.textContent = '请输入本地配对窗口中的密钥。'; return; }
  try {
    await chrome.runtime.sendMessage({type: 'connect_bridge', token});
    input.value = '';
    output.textContent = '已发起连接；请在 Codex 中检查连接状态。';
  } catch (_) { output.textContent = '连接请求失败，请重新加载扩展。'; }
});
