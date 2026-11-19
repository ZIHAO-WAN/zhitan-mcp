"""Exercise the extension's DOM parser against local HTML, with no site access."""
import html
import json
import os
import re
import subprocess
from pathlib import Path

import pytest


def inspect_fixture(tmp_path, name, page_url):
    edge = Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
    if not edge.is_file():
        pytest.skip('Microsoft Edge is required for local DOM verification')
    source = Path('browser-extension/inspect.js').read_text(encoding='utf-8')
    content = Path(f'tests/fixtures/{name}.html').read_text(encoding='utf-8')
    if name == 'detail':
        content = content.replace('<div class="company-name">甲公司</div>', '<div class="similar-job-company"><span class="company-name">其他公司</span></div><div class="sider-company"><div class="company-info"><a><img></a><a>甲公司</a></div></div>')
    fake = {'protocol': 'https:', 'host': 'www.zhipin.com', 'href': page_url, 'pathname': '/web/geek/jobs' if name == 'search' else '/job_detail/jobA.html'}
    script = "<base href='https://www.zhipin.com/'>\n" + content + '<script>\n' + (
        'const inspect = new Function("location", ' + json.dumps(source + '; return inspectPage();') + ');'
        'const result = inspect(' + json.dumps(fake) + ');'
        'const output = document.createElement("pre"); output.id="test-output";'
        'output.textContent=JSON.stringify(result); document.body.appendChild(output);'
    ) + '</script>'
    path = tmp_path / 'fixture.html'
    path.write_text(script, encoding='utf-8')
    result = subprocess.run([str(edge), '--headless=new', '--disable-gpu', '--disable-background-networking',
                             '--disable-extensions', '--no-first-run', f'--user-data-dir={tmp_path / "profile"}',
                             '--dump-dom', path.as_uri()], capture_output=True, timeout=40, check=True)
    dom = result.stdout.decode('utf-8', errors='replace')
    match = re.search(r'<pre id="test-output">(.*?)</pre>', dom, re.S)
    assert match, 'DOM parser did not produce a result'
    return json.loads(html.unescape(match.group(1)))


def test_search_extracts_identity_and_strips_tracking(tmp_path):
    result = inspect_fixture(tmp_path, 'search', 'https://www.zhipin.com/web/geek/jobs?query=Python')
    assert result['jobCards'] == 2
    assert result['jobs'][0]['job_id'] == 'jobA'
    assert result['jobs'][0]['company'] == '甲公司'
    assert result['jobs'][0]['url'] == 'https://www.zhipin.com/job_detail/jobA.html'
    assert result['search']['keyword'] == 'Python'
    assert result['search']['city'] == '北京'
    assert result['search']['site_end'] is False


def test_detail_ignores_other_company_and_reads_contact_state(tmp_path):
    result = inspect_fixture(tmp_path, 'detail', 'https://www.zhipin.com/job_detail/jobA.html')
    assert result['detail']['job_id'] == 'jobA'
    assert result['detail']['company'] == '甲公司'
    assert result['detail']['contact_status'] == 'not_contacted'
