import os
import requests
import time
import threading
import subprocess
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================== 全局配置 ===========================
CSV_DIR = "D:\\fansign\\ing\\ive_kms0403"          # CSV 存储目录
MONITOR_INTERVAL = 10                               # 监控间隔（秒），统一修改此处即可

# ================== Git 推送配置 ==================
GITHUB_REPO = "Juineii/ive_kms0403"        # 请替换为您的仓库名
GITHUB_BRANCH = "main"                          # 分支名（main 或 master）
# GitHub Personal Access Token 优先从环境变量 GITHUB_TOKEN 读取

# 确保目录存在
os.makedirs(CSV_DIR, exist_ok=True)

# 全局锁，保护 CSV 写入（避免多线程同时写同一文件）
csv_lock = threading.Lock()

# =========================== 旧app部分 ===========================
PRODUCT_URLS = [
    "https://kms.kmstation.net/prod/prodInfo?prodId=3978",
]
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "http://page.kmstation.net/",
    "Origin": "http://page.kmstation.net",
}

# 预定义成员名列表（可根据实际情况扩展）
MEMBER_NAMES = ["REI", "GAEUL", "ANYUJIN", "JANGWONYOUNG","LEESEO","LIZ"]

# 记录商品库存状态，键为 (商品名称, SKU名称)，值为最新库存
previous_stocks = {}


# ================== Git 推送函数 ==================
def git_push_update(file_path):
    """
    将指定的 CSV 文件提交并推送到 GitHub
    """
    try:
        # 获取 GitHub Token（优先从环境变量读取）
        token = os.environ.get('GITHUB_TOKEN')
        if not token:
            print("⚠️ 环境变量 GITHUB_TOKEN 未设置，跳过 Git 推送")
            return

        # 构建带认证的远程仓库 URL
        remote_url = f"https://{token}@github.com/{GITHUB_REPO}.git"

        # 添加指定 CSV 文件到暂存区
        subprocess.run(['git', 'add', file_path], check=True, capture_output=True)

        # 检查是否有文件变化（避免空提交）
        result = subprocess.run(['git', 'diff', '--cached', '--quiet'], capture_output=True)
        if result.returncode != 0:
            # 有变化，提交
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            commit_msg = f"自动更新数据 {timestamp}"
            subprocess.run(['git', 'commit', '-m', commit_msg], check=True, capture_output=True)

            # 推送到 GitHub（指定分支）
            subprocess.run(
                ['git', 'push', remote_url, f'HEAD:{GITHUB_BRANCH}'],
                check=True,
                capture_output=True,
                text=True
            )
            # 可选：打印成功信息，为避免刷屏可注释
            print(f"✅ 已推送到 GitHub: {commit_msg}")
    except subprocess.CalledProcessError:
        pass  # 静默失败，不影响主流程
    except Exception:
        pass


def sanitize_filename(name):
    """清理非法字符用于文件名"""
    return "".join(c for c in name if c.isalnum() or c in (' ', '_', '-')).rstrip()


def extract_member_name(sku_name):
    """
    从SKU名称中提取成员名（用于文件名）。
    优先匹配预定义的成员列表，若匹配成功则返回成员名。
    若未匹配，则返回原 sku_name（清理后）。
    """
    for member in MEMBER_NAMES:
        if member in sku_name:
            return member
    return sanitize_filename(sku_name)


def transform_sku_name(sku_name):
    """
    转换规格名称：去掉末尾成员名，前面加上"旧"。
    例如："【线上签售】 KARINA" -> "旧【线上签售】"
         "【线上签售】前300张 KARINA" -> "旧【线上签售】前300张"
    """
    parts = sku_name.rsplit(' ', 1)
    if len(parts) == 2:
        base = parts[0]
        if parts[1] in MEMBER_NAMES:
            return f"旧{base}"
    for member in MEMBER_NAMES:
        if member in sku_name:
            base = sku_name.replace(member, "").strip()
            return f"旧{base}"
    return f"旧{sku_name}"


def write_old_csv_record(sku_name, timestamp, stock_change, sales_volume):
    """旧app写入记录，根据成员名分文件，使用 pandas concat"""
    member = extract_member_name(sku_name)
    safe_name = sanitize_filename(member).replace(" ", "_")
    csv_file = os.path.join(CSV_DIR, f"{safe_name}.csv")
    display_name = transform_sku_name(sku_name)

    columns = ['时间', '商品名称', '库存变化', '单笔销量']
    new_row = pd.DataFrame([[timestamp, display_name, stock_change, sales_volume]], columns=columns)

    with csv_lock:
        try:
            # 读取现有数据或创建空 DataFrame
            if os.path.exists(csv_file):
                df_existing = pd.read_csv(csv_file, encoding='utf-8-sig')
            else:
                df_existing = pd.DataFrame(columns=columns)

            df_updated = pd.concat([df_existing, new_row], ignore_index=True)
            df_updated.to_csv(csv_file, index=False, encoding='utf-8-sig')

            # 打印格式：时间,成员 - 商品名称,库存变化,单笔销量
            print(f"{timestamp},{member} - {display_name},{stock_change},{sales_volume}")

            # 触发 Git 推送
            git_push_update(csv_file)
        except Exception as e:
            print(f"写入CSV文件失败: {csv_file}, 错误: {e}")


def fetch_stock_data(url):
    try:
        response = requests.get(url, headers=HEADERS)
        if response.status_code == 200:
            data = response.json()
            product_name = data.get("prodName", "未知商品")
            sku_list = data.get("skuList", [])
            stock_info = {}
            for sku in sku_list:
                sku_name = sku.get("skuName", "未知商品名称")
                stocks = sku.get("stocks", 0)
                stock_info[sku_name] = stocks
            return product_name, stock_info
        else:
            print(f"请求失败: {url}，状态码: {response.status_code}")
    except Exception as e:
        print(f"请求出错: {url}，错误信息: {str(e)}")
    return None, None


def monitor_stocks():
    """旧app监控主循环"""
    global previous_stocks
    while True:
        for url in PRODUCT_URLS:
            product_name, stock_info = fetch_stock_data(url)
            if product_name and stock_info:
                for sku, current_stock in stock_info.items():
                    key = (product_name, sku)
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    if key not in previous_stocks:
                        # 初始记录
                        stock_change = f"初始库存：{current_stock}"
                        sales_volume = 0
                        write_old_csv_record(sku, timestamp, stock_change, sales_volume)
                        previous_stocks[key] = current_stock
                    else:
                        previous_stock = previous_stocks[key]
                        if current_stock != previous_stock:
                            stock_diff = previous_stock - current_stock
                            stock_change = f"{previous_stock} -> {current_stock}"
                            sales_volume = stock_diff
                            write_old_csv_record(sku, timestamp, stock_change, sales_volume)
                            previous_stocks[key] = current_stock
        time.sleep(MONITOR_INTERVAL)  # 使用统一间隔


# =========================== 新app部分 ===========================
BASE_URL = "https://api.kmstation.net"
EMAIL = "3514263454@qq.com"
PASSWORD = "20010216wwj...."
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 15; SM-S918B Build/AP3A.240905.015.A2; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/145.0.7632.120 "
    "Mobile Safari/537.36 uni-app Html5Plus/1.0"
)

# 手动指定成员名（与旧app中的成员名一致）
ITEMS_TO_TRACK = [
    {"name": "新", "member": "JANGWONYOUNG", "skuId": 5833, "spuId": 2093},
    {"name": "新", "member": "ANYUJIN", "skuId": 5832, "spuId": 2093},
    # 如有其他成员，按相同格式添加
]

STOCK_INSUFFICIENT_CODE = 1008006004
ENDPOINT = f"{BASE_URL}/app-api/trade/order/settlement"

token = None
token_lock = threading.Lock()


def init_new_csv(member):
    """确保成员对应的CSV文件存在（空文件或已有标题），但改用 pandas 后无需预先创建，写入时会自动处理"""
    safe_name = sanitize_filename(member).replace(" ", "_")
    csv_file = os.path.join(CSV_DIR, f"{safe_name}.csv")
    # 不主动创建，write_new_csv_row 会处理


def write_new_csv_row(member, name, timestamp, change_desc, sales):
    """新app写入一条记录到成员对应的CSV文件，使用 pandas concat"""
    safe_name = sanitize_filename(member).replace(" ", "_")
    csv_file = os.path.join(CSV_DIR, f"{safe_name}.csv")
    columns = ['时间', '商品名称', '库存变化', '单笔销量']
    new_row = pd.DataFrame([[timestamp, name, change_desc, sales]], columns=columns)

    with csv_lock:
        try:
            if os.path.exists(csv_file):
                df_existing = pd.read_csv(csv_file, encoding='utf-8-sig')
            else:
                df_existing = pd.DataFrame(columns=columns)

            df_updated = pd.concat([df_existing, new_row], ignore_index=True)
            df_updated.to_csv(csv_file, index=False, encoding='utf-8-sig')

            # 打印格式与旧app一致
            print(f"{timestamp},{member} - {name},{change_desc},{sales}")

            # 触发 Git 推送
            git_push_update(csv_file)
        except Exception as e:
            print(f"写入CSV文件失败: {csv_file}, 错误: {e}")


def login() -> bool:
    global token
    try:
        resp = requests.post(
            f"{BASE_URL}/app-api/member/auth/email-login",
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            json={"email": EMAIL, "password": PASSWORD},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            with token_lock:
                token = data["data"]["accessToken"]
            return True
        print(f"❌ 登录失败: {data.get('msg')}")
        return False
    except Exception as e:
        print(f"❌ 登录错误: {e}")
        return False


def get_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
    }


def settlement_request(sku_id: int, spu_id: int, count: int) -> dict:
    payload = {
        "_isPass": True,
        "items": [{"skuId": sku_id, "count": count, "spuId": spu_id, "cartId": None}],
        "pointStatus": False,
        "deliveryType": 1,
    }
    try:
        resp = requests.post(ENDPOINT, headers=get_headers(), json=payload, timeout=10)
        data = resp.json()
        if data.get("code") == 401:
            with token_lock:
                if not login():
                    return {}
            resp = requests.post(ENDPOINT, headers=get_headers(), json=payload, timeout=10)
            data = resp.json()
        return data
    except Exception as e:
        print(f"  Request error: {e}")
        return {}


def is_insufficient(resp: dict) -> bool:
    return resp.get("code") == STOCK_INSUFFICIENT_CODE


def binary_search_stock(sku_id: int, spu_id: int) -> int:
    if is_insufficient(settlement_request(sku_id, spu_id, 1)):
        return 0
    low, high = 1, 131072
    while not is_insufficient(settlement_request(sku_id, spu_id, high)):
        low = high
        high *= 2
    result = low
    while low <= high:
        mid = (low + high) // 2
        if is_insufficient(settlement_request(sku_id, spu_id, mid)):
            high = mid - 1
        else:
            result = mid
            low = mid + 1
    return result


def poll_item(item: dict) -> tuple[int, int, str]:
    """返回 (skuId, 当前库存, member)"""
    sku_id = item["skuId"]
    stock = binary_search_stock(sku_id, item["spuId"])
    return sku_id, stock, item["member"]


def track_stock():
    """新app监控主循环"""
    # 不再需要预先创建文件，write_new_csv_row 会处理
    if not login():
        print("❌ 新app无法启动 — 登录失败")
        return

    last_stocks: dict[int, int | None] = {item["skuId"]: None for item in ITEMS_TO_TRACK}
    item_map = {item["skuId"]: item for item in ITEMS_TO_TRACK}

    with ThreadPoolExecutor(max_workers=len(ITEMS_TO_TRACK)) as pool:
        while True:
            cycle_start = time.monotonic()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            futures = {pool.submit(poll_item, item): item for item in ITEMS_TO_TRACK}
            for fut in as_completed(futures):
                try:
                    sku_id, stock, member = fut.result()
                except Exception as e:
                    print(f"  Poll error: {e}")
                    continue

                item = item_map[sku_id]
                name = item["name"]
                last = last_stocks[sku_id]

                if last is None:
                    # 初始记录
                    write_new_csv_row(member, name, now, f"初始库存：{stock}", 0)
                elif stock != last:
                    change_desc = f"{last} -> {stock}"
                    sales = last - stock
                    write_new_csv_row(member, name, now, change_desc, sales)

                last_stocks[sku_id] = stock

            elapsed = time.monotonic() - cycle_start
            sleep_for = max(0.0, MONITOR_INTERVAL - elapsed)  # 使用统一间隔
            if sleep_for:
                time.sleep(sleep_for)


# =========================== 主函数 ===========================
if __name__ == "__main__":
    # 启动旧app监控线程
    old_thread = threading.Thread(target=monitor_stocks, daemon=True)
    old_thread.start()

    # 启动新app监控线程
    new_thread = threading.Thread(target=track_stock, daemon=True)
    new_thread.start()

    # 主线程保持运行
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("程序已终止")