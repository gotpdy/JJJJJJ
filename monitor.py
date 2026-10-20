import os
import re
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

URL = 'https://www.mensa.jp/exam/'
API = f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/issues"
GH = {
    'Authorization': f"Bearer {os.environ['GITHUB_TOKEN']}",
    'Accept': 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
}
TITLE = '[monitor-state] JAPAN MENSA'
PREFECTURES = ('東京都', '神奈川県')


def clean(text):
    return re.sub(r'\s+', ' ', text).strip()


def get_sessions():
    response = requests.get(URL, headers={'User-Agent': 'Mozilla/5.0 (PersonalAvailabilityMonitor/1.1)'}, timeout=25)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'svg']):
        tag.decompose()

    # Only inspect the Kanto section, never other regions or site navigation.
    heading = next((h for h in soup.find_all(re.compile('^h[1-6]$')) if '関東地方' in h.get_text(' ', strip=True)), None)
    if heading is None:
        raise RuntimeError('Kanto heading not found; baseline left untouched')

    section_nodes = []
    for node in heading.next_elements:
        if getattr(node, 'name', None) and re.fullmatch(r'h[1-6]', node.name):
            break
        if getattr(node, 'name', None) == 'li':
            # Avoid duplicate nested LI entries.
            if not node.find_parent('li'):
                section_nodes.append(node)

    sessions = {}
    for li in section_nodes:
        raw = clean(li.get_text(' ', strip=True))
        match = re.search(r'場所\s*[：:]\s*(東京都|神奈川県)\S*', raw)
        if not match:
            continue
        date = re.search(r'20\d{2}/\d{1,2}/\d{1,2}', raw)
        time = re.search(r'\d{1,2}:\d{2}\s*[~〜～-]\s*\d{1,2}:\d{2}', raw)
        if not date or not time:
            continue
        location = re.search(r'場所\s*[：:]\s*(.+?)(?:このテスト|$)', raw)
        place = clean(location.group(1)) if location else match.group(1)
        if not place.startswith(PREFECTURES):
            continue
        full = any(('満員' in (img.get('alt') or '')) for img in li.find_all('img')) or '満員' in raw
        links = [urljoin(URL, a['href']) for a in li.find_all('a', href=True) if a.get('href') and not a['href'].startswith('#')]
        # Keep only exam signup links, not generic navigation.
        signup = next((link for link in links if link.rstrip('/') != URL.rstrip('/')), '')
        status = '마감' if full else ('신청 링크 있음' if signup else '마감 표시 없음 (신청 가능 여부 미확인)')
        key = '|'.join([date.group(), clean(time.group()), place])
        sessions[key] = {'date': date.group(), 'time': clean(time.group()), 'place': place, 'status': status, 'url': signup}

    if not sessions:
        raise RuntimeError('No Tokyo/Kanagawa sessions parsed; site layout may have changed. Baseline left untouched.')
    return sessions


def telegram(message):
    token = os.environ['TELEGRAM_BOT_TOKEN']
    response = requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data={'chat_id': os.environ['TELEGRAM_CHAT_ID'], 'text': message, 'disable_web_page_preview': 'true'},
        timeout=25,
    )
    response.raise_for_status()
    if not response.json().get('ok'):
        raise RuntimeError('Telegram rejected message')


def main():
    current = get_sessions()
    response = requests.get(API, headers=GH, params={'state': 'all', 'per_page': 100}, timeout=25)
    response.raise_for_status()
    issue = next((item for item in response.json() if item['title'] == TITLE), None)
    state = json.dumps({'version': 2, 'sessions': current}, ensure_ascii=False, sort_keys=True)

    if issue is None:
        created = requests.post(API, headers=GH, json={'title': TITLE, 'body': state}, timeout=25)
        created.raise_for_status()
        print(f'Baseline initialized: {len(current)} Tokyo/Kanagawa sessions; no alert.')
        return

    old_body = (issue.get('body') or '').strip()
    try:
        old_data = json.loads(old_body)
        if old_data.get('version') != 2 or not isinstance(old_data.get('sessions'), dict):
            raise ValueError('Old format')
        old = old_data['sessions']
    except (ValueError, TypeError, AttributeError):
        # Migration from the old SHA-256 hash: never alert on the first upgraded run.
        patched = requests.patch(issue['url'], headers=GH, json={'body': state}, timeout=25)
        patched.raise_for_status()
        print(f'Upgraded baseline to Tokyo/Kanagawa: {len(current)} sessions; no alert.')
        return

    notices = []
    for key, session in current.items():
        previous = old.get(key)
        if previous is None:
            notices.append(('🆕 새로운 시험 일정', session))
        elif previous.get('status') != session['status'] or previous.get('url') != session['url']:
            notices.append(('🔄 신청 상태/링크 변경', session))

    if notices:
        for title, session in notices:
            message = (f'{title}\n📍 {session["place"]}\n📅 {session["date"]} {session["time"]}\n'
                       f'상태: {session["status"]}\n{session["url"] or URL}\n\n'
                       '※ 접수 가능 여부는 공식 홈페이지에서 확인해.')
            telegram(message)
        print(f'Sent {len(notices)} change alert(s).')
    else:
        print(f'No Tokyo/Kanagawa changes ({len(current)} sessions).')

    patched = requests.patch(issue['url'], headers=GH, json={'body': state}, timeout=25)
    patched.raise_for_status()
    print('Baseline updated.')


if __name__ == '__main__':
    main()
