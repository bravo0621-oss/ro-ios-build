#!/usr/bin/env python3
# r54_apk4（11/21）：下一服 iPhone 雲端編譯用的 GitHub 小工具（新主機＋雲端 Mac 共用，只用 Python 標準庫）。
#   權杖：雲端 Mac 用環境變數 GH_TOKEN（GitHub 自動遮罩）；新主機用 RO_IOS_TOKEN_FILE 指的檔（要權限 600、自己的檔）。
#   ★權杖值、任何網址（下載點、簽名網址、網頁連結）一律不印；錯誤只印狀態碼＋GitHub 回的 message。
#   儲存庫：RO_IOS_REPO（預設 bravo0621-oss/ro-ios-build，公開儲存庫）；素材包與成品只放「草稿 Release」（不公開）。
# 用法：
#   gh_rel.py check                         權杖能不能用、儲存庫預設分支、有沒有寫入權、有哪些 workflow
#   gh_rel.py create TAG                    建草稿 Release（已有同標籤草稿就沿用）→ DRAFT_OK
#   gh_rel.py upload TAG FILE...            上傳附件（同名先刪再傳）→ UPLOAD_OK
#   gh_rel.py assets TAG                    列附件名與大小
#   gh_rel.py download TAG OUTDIR [樣式]     下載附件（fnmatch 樣式，預設 *）→ DOWNLOAD_OK
#   gh_rel.py delete TAG                    刪草稿 Release（不是草稿不刪）→ DELETE_OK
#   gh_rel.py push LOCAL REPO_PATH MSG      把檔案放進儲存庫預設分支（新增或更新；workflow 檔要 Workflows 權限）→ PUSH_OK
#   gh_rel.py dispatch WF TAG [IOS_MIN]     觸發 workflow_dispatch（WF＝workflow 檔名，例 build-ios.yml）→ DISPATCH_OK
#   gh_rel.py wait WF TAG [分鐘]             等名稱含 TAG 的那次執行結束 → RUN_OK／RUN_FAIL <結論>／RUN_TIMEOUT
#   gh_rel.py logs RUN_ID OUTFILE           下載那次執行的紀錄壓縮檔（公開儲存庫的紀錄本來就公開）
import fnmatch, http.client, json, os, stat, sys, time, urllib.parse

API = 'api.github.com'; UP = 'uploads.github.com'
REPO = os.environ.get('RO_IOS_REPO', 'bravo0621-oss/ro-ios-build')

def die(msg):
    print('GH_FAIL ' + msg); sys.exit(1)

def token():
    t = os.environ.get('GH_TOKEN', '').strip()
    if t:
        return t
    f = os.environ.get('RO_IOS_TOKEN_FILE', '')
    if not f:
        die('沒有權杖：雲端用 GH_TOKEN，新主機要設 RO_IOS_TOKEN_FILE＝權杖檔路徑')
    st = os.stat(f)
    if st.st_uid != os.getuid():
        die('權杖檔不是自己的')
    if stat.S_IMODE(st.st_mode) & 0o077:
        die('權杖檔權限要 600（現在 %o）' % stat.S_IMODE(st.st_mode))
    t = open(f, encoding='utf-8').read().strip()
    if not t or '\n' in t or ' ' in t:
        die('權杖檔內容格式不對（要只有一行權杖）')
    return t

TOK = None
def hdr(extra=None, auth=True):
    global TOK
    h = {'User-Agent': 'ro-ios-kit', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    if auth:
        if TOK is None:
            TOK = token()
        h['Authorization'] = 'Bearer ' + TOK
    if extra:
        h.update(extra)
    return h

def call(method, path, body=None, host=API, extra=None, ok=(200, 201, 204), raw=False):
    """回 (狀態碼, 內容)；不跟轉址（轉址時回 Location，由呼叫端處理且不印）"""
    data = None; ex = dict(extra or {})
    if body is not None and not hasattr(body, 'read'):
        data = json.dumps(body).encode('utf-8'); ex.setdefault('Content-Type', 'application/json')
    for attempt in range(4):
        c = http.client.HTTPSConnection(host, timeout=900)
        try:
            c.request(method, path, body=data, headers=hdr(ex))
            r = c.getresponse(); b = r.read()
            if r.status in (301, 302, 307):
                return r.status, r.getheader('Location')
            if r.status in ok:
                if raw:
                    return r.status, b
                return r.status, (json.loads(b.decode('utf-8')) if b else None)
            if r.status in (500, 502, 503, 504) and attempt < 3:
                time.sleep(5 * (attempt + 1)); continue
            msg = ''
            try:
                msg = json.loads(b.decode('utf-8')).get('message', '')
            except Exception:
                pass
            die('%s %s → %d %s' % (method, path.split('?')[0].replace('/repos/' + REPO, ''), r.status, msg[:200]))
        except (OSError, http.client.HTTPException) as e:
            if attempt < 3:
                time.sleep(5 * (attempt + 1)); continue
            die('%s 連線失敗：%s' % (method, type(e).__name__))
        finally:
            c.close()

def releases():
    out = []; page = 1
    while True:
        _, js = call('GET', '/repos/%s/releases?per_page=100&page=%d' % (REPO, page))
        out += js
        if len(js) < 100:
            return out
        page += 1

def find_draft(tag, must=True):
    for r in releases():
        if r.get('tag_name') == tag:
            if not r.get('draft'):
                die('標籤 %s 的 Release 不是草稿（公開的），不動它' % tag)
            return r
    if must:
        die('找不到草稿 Release %s' % tag)
    return None

def assets(rel):
    _, js = call('GET', '/repos/%s/releases/%d/assets?per_page=100' % (REPO, rel['id']))
    return js

class Prog:
    def __init__(self, f, size, name):
        self.f, self.size, self.name, self.n, self.last = f, size, name, 0, 0
    def read(self, k=-1):
        b = self.f.read(k if k and k > 0 else 1 << 20)
        self.n += len(b)
        if self.n - self.last >= 256 << 20:
            self.last = self.n; print('  %s %d／%d MB' % (self.name, self.n >> 20, self.size >> 20), flush=True)
        return b

def upload_one(rel, path):
    name = os.path.basename(path); size = os.path.getsize(path)
    if size > 2000 * 1000 * 1000:
        die('%s 太大（%d），附件上限約 2GB，先切分卷' % (name, size))
    for a in assets(rel):
        if a['name'] == name:
            call('DELETE', '/repos/%s/releases/assets/%d' % (REPO, a['id'])); print('  刪掉舊的同名附件', name)
    q = '/repos/%s/releases/%d/assets?name=%s' % (REPO, rel['id'], urllib.parse.quote(name))
    for attempt in range(3):
        with open(path, 'rb') as f:
            c = http.client.HTTPSConnection(UP, timeout=1800, blocksize=1 << 20)
            try:
                c.request('POST', q, body=Prog(f, size, name), headers=hdr({'Content-Type': 'application/octet-stream', 'Content-Length': str(size)}))
                r = c.getresponse(); b = r.read()
            except (OSError, http.client.HTTPException) as e:
                print('  上傳中斷（%s），重試' % type(e).__name__); time.sleep(10); continue
            finally:
                c.close()
        if r.status == 201:
            js = json.loads(b.decode('utf-8'))
            if js.get('size') != size:
                die('%s 傳上去大小 %s ≠ %d' % (name, js.get('size'), size))
            print('  上傳好', name, size); return
        msg = ''
        try:
            msg = json.loads(b.decode('utf-8')).get('message', '')
        except Exception:
            pass
        print('  上傳回 %d %s，重試' % (r.status, msg[:120])); time.sleep(10)
        for a in assets(rel):
            if a['name'] == name and a.get('state') != 'uploaded':
                call('DELETE', '/repos/%s/releases/assets/%d' % (REPO, a['id']))
    die('%s 上傳三次都失敗' % name)

def download_one(a, outdir):
    st, loc = call('GET', '/repos/%s/releases/assets/%d' % (REPO, a['id']), extra={'Accept': 'application/octet-stream'}, raw=True)
    dst = os.path.join(outdir, a['name']); tmp = dst + '.part_dl'
    if st in (301, 302, 307):
        u = urllib.parse.urlsplit(loc)                      # 簽名網址：不帶權杖、不印
        c = http.client.HTTPSConnection(u.netloc, timeout=1800)
        c.request('GET', u.path + ('?' + u.query if u.query else ''), headers={'User-Agent': 'ro-ios-kit'})
        r = c.getresponse()
        if r.status != 200:
            die('%s 下載回 %d' % (a['name'], r.status))
        n = 0; last = 0
        with open(tmp, 'wb') as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b); n += len(b)
                if n - last >= 256 << 20:
                    last = n; print('  %s %d／%d MB' % (a['name'], n >> 20, a['size'] >> 20), flush=True)
        c.close()
    else:
        with open(tmp, 'wb') as f:
            f.write(loc)
    if os.path.getsize(tmp) != a['size']:
        die('%s 下載大小 %d ≠ %d' % (a['name'], os.path.getsize(tmp), a['size']))
    os.replace(tmp, dst); print('  下載好', a['name'], a['size'])

def main(argv):
    if not argv:
        print(__doc__ or ''); sys.exit(2)
    cmd, args = argv[0], argv[1:]
    if cmd == 'check':
        _, r = call('GET', '/repos/%s' % REPO)
        print('儲存庫', r['full_name'], '公開' if not r['private'] else '私人', '預設分支', r['default_branch'], '寫入權', r.get('permissions', {}).get('push'))
        _, w = call('GET', '/repos/%s/actions/workflows' % REPO)
        print('workflow：', ', '.join('%s(%s)' % (x['path'].split('/')[-1], x['state']) for x in w.get('workflows', [])) or '沒有')
        print('GH_CHECK_OK')
    elif cmd == 'create':
        tag = args[0]; r = find_draft(tag, must=False)
        if r is None:
            _, rp = call('GET', '/repos/%s' % REPO)
            _, r = call('POST', '/repos/%s/releases' % REPO, {'tag_name': tag, 'name': tag, 'draft': True, 'target_commitish': rp['default_branch'], 'body': '下一服 iPhone 雲端編譯暫存（草稿，不公開；用完刪）'})
            print('建好草稿', tag)
        else:
            print('沿用既有草稿', tag)
        print('DRAFT_OK', tag)
    elif cmd == 'upload':
        rel = find_draft(args[0])
        for p in args[1:]:
            upload_one(rel, p)
        print('UPLOAD_OK', len(args) - 1)
    elif cmd == 'assets':
        rel = find_draft(args[0])
        for a in assets(rel):
            print(a['name'], a['size'], a.get('state'))
        print('ASSETS_OK')
    elif cmd == 'download':
        rel = find_draft(args[0]); out = args[1]; pat = args[2] if len(args) > 2 else '*'
        os.makedirs(out, exist_ok=True); n = 0
        for a in assets(rel):
            if fnmatch.fnmatch(a['name'], pat):
                download_one(a, out); n += 1
        if n == 0:
            die('草稿 %s 沒有符合 %s 的附件' % (args[0], pat))
        print('DOWNLOAD_OK', n)
    elif cmd == 'delete':
        rel = find_draft(args[0])
        call('DELETE', '/repos/%s/releases/%d' % (REPO, rel['id']))
        if find_draft(args[0], must=False) is not None:
            die('刪了還在')
        print('DELETE_OK', args[0])
    elif cmd == 'push':
        local, rpath, msg = args[0], args[1], args[2]
        import base64
        _, rp = call('GET', '/repos/%s' % REPO); br = rp['default_branch']
        body = {'message': msg, 'content': base64.b64encode(open(local, 'rb').read()).decode('ascii'), 'branch': br}
        st, cur = call('GET', '/repos/%s/contents/%s?ref=%s' % (REPO, urllib.parse.quote(rpath), br), ok=(200, 404))
        if isinstance(cur, dict) and cur.get('sha'):
            if base64.b64decode(cur.get('content', '')) == open(local, 'rb').read():
                print('內容相同，不用更新', rpath); print('PUSH_OK', rpath); return
            body['sha'] = cur['sha']
        call('PUT', '/repos/%s/contents/%s' % (REPO, urllib.parse.quote(rpath)), body)
        _, chk = call('GET', '/repos/%s/contents/%s?ref=%s' % (REPO, urllib.parse.quote(rpath), br))
        if base64.b64decode(chk['content']) != open(local, 'rb').read():
            die('讀回內容不同 %s' % rpath)
        print('讀回逐位元組相同', rpath); print('PUSH_OK', rpath)
    elif cmd == 'dispatch':
        wf, tag = args[0], args[1]; iosmin = args[2] if len(args) > 2 else '15.0'
        find_draft(tag)
        _, rp = call('GET', '/repos/%s' % REPO)
        call('POST', '/repos/%s/actions/workflows/%s/dispatches' % (REPO, wf), {'ref': rp['default_branch'], 'inputs': {'tag': tag, 'ios_min': iosmin}})
        print('DISPATCH_OK', wf, tag)
    elif cmd == 'wait':
        wf, tag = args[0], args[1]; lim = float(args[2]) if len(args) > 2 else 330
        t0 = time.time(); seen = None; last = None
        while time.time() - t0 < lim * 60:
            _, js = call('GET', '/repos/%s/actions/workflows/%s/runs?event=workflow_dispatch&per_page=10' % (REPO, wf))
            runs = [x for x in js.get('workflow_runs', []) if tag in (x.get('display_title') or '')]
            if runs:
                x = runs[0]; seen = x['id']; s = (x['status'], x.get('conclusion'))
                if s != last:
                    print(time.strftime('%H:%M:%S'), '執行', x['id'], x['status'], x.get('conclusion') or '', flush=True); last = s
                if x['status'] == 'completed':
                    if x.get('conclusion') == 'success':
                        print('RUN_OK', x['id']); return
                    print('RUN_FAIL', x.get('conclusion'), x['id']); sys.exit(1)
            time.sleep(60)
        print('RUN_TIMEOUT', seen or ''); sys.exit(1)
    elif cmd == 'logs':
        st, loc = call('GET', '/repos/%s/actions/runs/%s/logs' % (REPO, args[0]), raw=True)
        if st in (301, 302, 307):
            u = urllib.parse.urlsplit(loc)
            c = http.client.HTTPSConnection(u.netloc, timeout=600)
            c.request('GET', u.path + ('?' + u.query if u.query else ''), headers={'User-Agent': 'ro-ios-kit'})
            r = c.getresponse(); b = r.read(); c.close()
        else:
            b = loc
        open(args[1], 'wb').write(b); print('LOGS_OK', len(b))
    else:
        die('不認得的指令 ' + cmd)

if __name__ == '__main__':
    main(sys.argv[1:])
