import os, re, hashlib, requests
from bs4 import BeautifulSoup

URL = 'https://www.mensa.jp/exam/'
API = f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/issues"
GH = {'Authorization': f"Bearer {os.environ['GITHUB_TOKEN']}", 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version':'2022-11-28'}
TITLE = '[monitor-state] JAPAN MENSA'


def get_page():
    r = requests.get(URL, headers={'User-Agent': 'Mozilla/5.0 (compatible; PersonalAvailabilityMonitor/1.0)'}, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.content, 'html.parser')
    for t in soup(['script','style','noscript','svg']): t.decompose()
    main = soup.find('main') or soup.find(id='content') or soup.body or soup
    # Capture visible text and destination links so both status and application-link changes are detected.
    lines = [re.sub(r'\s+', ' ', s).strip() for s in main.stripped_strings]
    links = [f"{a.get_text(' ', strip=True)} => {a.get('href','')}" for a in main.find_all('a', href=True)]
    normalized = '\n'.join(lines + links)
    if len(normalized) < 100: raise RuntimeError('Page content unexpectedly short; refusing to overwrite baseline')
    return normalized


def telegram(msg):
    token = os.environ['TELEGRAM_BOT_TOKEN']
    chat_id = os.environ['TELEGRAM_CHAT_ID']
    r = requests.post(f'https://api.telegram.org/bot{token}/sendMessage', data={'chat_id': chat_id, 'text': msg, 'disable_web_page_preview':'true'}, timeout=25)
    r.raise_for_status()


def main():
    text = get_page()
    digest = hashlib.sha256(text.encode()).hexdigest()
    r = requests.get(API, headers=GH, params={'state':'open','per_page':100}, timeout=25)
    r.raise_for_status()
    issue = next((i for i in r.json() if i['title'] == TITLE), None)
    if issue is None:
        r = requests.post(API, headers=GH, json={'title':TITLE,'body':digest}, timeout=25)
        r.raise_for_status()
        print('Baseline saved; no alert on initial run.')
        return
    old = (issue.get('body') or '').strip()
    if old == digest:
        print('No changes.')
        return
    telegram('🔔 JAPAN MENSA 시험 일정 페이지가 변경됐어!\n새 시험 또는 접수 가능 여부를 바로 확인해:\n' + URL + '\n\n※ 페이지 변경 알림이며 접수 가능 확정은 아님.')
    r = requests.patch(issue['url'], headers=GH, json={'body':digest}, timeout=25)
    r.raise_for_status()
    print('Change alert sent and baseline updated.')

if __name__ == '__main__': main()
