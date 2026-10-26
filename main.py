import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import json
import os
import time
import re
import urllib.parse
from curl_cffi import requests as cffi_requests

app = FastAPI()
templates = Jinja2Templates(directory="templates")
CONFIG_FILE = "config.json"
ADMIN_PWD = "admin123"

def load_config():
    default_config = {
        "client_password": "123",
        "cookie": "",
        "save_dir": "/来自Bot",
        "openlist_url": "http://156.225.17.124:5244",
        "openlist_user": "admin",
        "openlist_pwd": "",
        "openlist_mount": "/百度",
        "retention_hours": 24,
        "push_mode": "只提取直链",
        "motrix_rpc_url": "",
        "motrix_rpc_secret": ""
    }
    if not os.path.exists(CONFIG_FILE):
        return default_config
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        default_config.update(data)
        return default_config

def save_config(data: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

class ParseReq(BaseModel):
    password: str
    link: str

class ConfigReq(BaseModel):
    admin_pwd: str
    config_data: dict

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    return templates.TemplateResponse("config.html", {"request": request})

@app.post("/api/config/get")
async def api_get_config(req: dict):
    if req.get("admin_pwd") != ADMIN_PWD:
        return {"success": False, "message": "管理员密码错误"}
    return {"success": True, "data": load_config()}

@app.post("/api/config/save")
async def api_save_config(req: ConfigReq):
    if req.admin_pwd != ADMIN_PWD:
        return {"success": False, "message": "管理员密码错误"}
    save_config(req.config_data)
    return {"success": True}

def extract_baidu_link(config: dict, link: str):
    try:
        # 1. 提取 surl 和 pwd
        surl, pwd = "", ""
        for p in [r"pan\.baidu\.com/s/([a-zA-Z0-9_-]+)", r"surl=([a-zA-Z0-9_-]+)"]:
            m = re.search(p, link)
            if m:
                s = m.group(1)
                surl = s[1:] if len(s) > 1 and s[0] == "1" else s
                break
        for p in [r"提取码[：:\s]*([a-zA-Z0-9]{4})", r"密码[：:\s]*([a-zA-Z0-9]{4})", r"pwd[=：:\s]*([a-zA-Z0-9]{4})"]:
            m = re.search(p, link, re.IGNORECASE)
            if m:
                pwd = m.group(1)
                break
        if not surl:
            return False, "无法解析出分享链接"

        with cffi_requests.Session(impersonate="chrome120") as s:
            cookie_str = config.get("cookie", "")
            for item in cookie_str.split(";"):
                if "=" in item:
                    k, v = item.split("=", 1)
                    s.cookies.set(k.strip(), v.strip())

            # 2. 验证密码
            share_url = f"https://pan.baidu.com/s/1{surl}"
            if pwd:
                s.post(
                    "https://pan.baidu.com/share/verify",
                    params={"surl": surl, "t": str(int(time.time() * 1000)), "channel": "chunlei", "web": "1", "app_id": "250528"},
                    data={"pwd": pwd},
                    headers={"Referer": f"https://pan.baidu.com/share/init?surl={surl}"},
                    timeout=15
                )

            # 3. 解析分享页面
            resp = s.get(share_url, timeout=15)
            html = resp.text
            match = re.search(r"(?:yunData\.setData|locals\.mset)\(", html)
            if not match:
                return False, "无法解析页面，请检查Cookie是否过期"
            start = match.end()
            brace_count, in_string, escaped = 0, False, False
            end = start
            for i, c in enumerate(html[start:], start):
                if in_string:
                    if escaped: escaped = False
                    elif c == '\\': escaped = True
                    elif c == '"': in_string = False
                elif c == '"': in_string = True
                elif c == '{': brace_count += 1
                elif c == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end = i + 1
                        break
            shared_data = json.loads(html[start:end])
            uk = shared_data.get("share_uk") or shared_data.get("uk")
            share_id = shared_data.get("shareid")
            bdstoken = shared_data.get("bdstoken", "")

            file_list_raw = shared_data.get("file_list", {})
            root_files = file_list_raw.get("list", []) if isinstance(file_list_raw, dict) else file_list_raw
            if not root_files: return False, "分享中没有文件"

            fsids = [f["fs_id"] for f in root_files]
            if not fsids: return False, "分享中没有可提取的内容"

            # 4. 转存
            save_dir = config.get("save_dir", "/来自Bot")
            s.post(
                "https://pan.baidu.com/api/create",
                params={"a": "mkdir", "channel": "chunlei", "web": "1", "app_id": "250528", "clienttype": "0"},
                data={"path": save_dir, "isdir": "1"},
                timeout=10
            )
            trans_resp = s.post(
                "https://pan.baidu.com/share/transfer",
                params={"shareid": str(share_id), "from": str(uk), "bdstoken": bdstoken, "app_id": "250528", "channel": "chunlei", "web": "1"},
                data={"fsidlist": json.dumps(fsids), "path": save_dir},
                headers={"Referer": share_url},
                timeout=30
            )
            trans_res = trans_resp.json()
            if trans_res.get("errno") not in (0, 12, 4, -33):
                return False, f"转存失败 (errno={trans_res.get('errno')})"

            # 5. OpenList 获取 AccessToken
            ol_url = config.get("openlist_url", "").rstrip("/")
            login_resp = s.post(
                f"{ol_url}/api/auth/login",
                json={"username": config.get("openlist_user"), "password": config.get("openlist_pwd")},
                timeout=15
            )
            admin_token = login_resp.json().get("data", {}).get("token", "")
            if not admin_token: return False, "OpenList登录失败"

            storages = s.get(f"{ol_url}/api/admin/storage/list", headers={"Authorization": admin_token}, timeout=15)
            at = ""
            for st in storages.json().get("data", {}).get("content", []):
                if "Baidu" in st.get("driver", ""):
                    at = json.loads(st.get("addition", "{}")).get("AccessToken", "")
                    break
            if not at: return False, "未找到AccessToken"

            # 6. 递归获取目标目录所有文件
            def get_all_files(dir_path, depth=0):
                if depth > 3: return []
                encoded = urllib.parse.quote(dir_path, safe="/")
                resp = s.get(f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir={encoded}&dlink=1&web=1&app_id=250528&access_token={at}", timeout=15)
                items = resp.json().get("list", [])
                res = []
                for item in items:
                    if item.get("isdir"):
                        res.extend(get_all_files(item.get("path"), depth+1))
                    else:
                        res.append(item)
                return res

            all_files = get_all_files(save_dir)
            current_time = time.time()
            # 过滤出近 5 分钟内转存的文件，排除旧文件
            recent_files = [f for f in all_files if current_time - f.get("server_mtime", 0) < 300]
            if not recent_files:
                return False, "转存成功，但找不到新文件"

            # 7. 批量获取直链（每100个一组分批，防止超长报错）
            fsid_list = [f["fs_id"] for f in recent_files]
            dlink_map = {}
            for i in range(0, len(fsid_list), 100):
                batch = fsid_list[i:i+100]
                meta_resp = s.get(
                    f"https://pan.baidu.com/rest/2.0/xpan/multimedia?method=filemetas&dlink=1&fsids={json.dumps(batch)}&access_token={at}",
                    timeout=15
                )
                for item in meta_resp.json().get("list", []):
                    if item.get("dlink"):
                        dlink_map[item["fs_id"]] = f"{item['dlink']}&access_token={at}"

            # 8. 构造包含相对路径的文件树扁平数据
            result_tree_data = []
            for f in recent_files:
                if f["fs_id"] in dlink_map:
                    # 算出相对路径，比如 /来自Bot/Phudu/1.zip 变成 Phudu/1.zip
                    rel_path = f["path"]
                    if rel_path.startswith(save_dir):
                        rel_path = rel_path[len(save_dir):]
                    rel_path = rel_path.strip("/")
                    
                    result_tree_data.append({
                        "name": f.get("server_filename"),
                        "path": rel_path,
                        "size": f.get("size", 0),
                        "dlink": dlink_map[f["fs_id"]]
                    })

            if not result_tree_data: return False, "请求直链全部失败"
            
            return True, result_tree_data

    except Exception as e:
        return False, f"核心逻辑异常: {str(e)}"

@app.post("/api/parse")
async def parse_link(req: ParseReq):
    config = load_config()
    if req.password != config.get("client_password"):
        return {"success": False, "message": "使用密码错误，请联系管理员！"}
    
    success, result = extract_baidu_link(config, req.link)
    if success:
        return {"success": True, "files": result}
    else:
        return {"success": False, "message": result}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)