"""
飞书多维表格客户端
负责读取和写入飞书 Bitable 数据
"""
import time
import json
import requests
from typing import Optional
from config import (
    FEISHU_APP_ID, FEISHU_APP_SECRET,
    FEISHU_BASE_ID, FEISHU_TABLE2_ID, FEISHU_FIELDS
)
from logger import get_logger

log = get_logger("feishu_client")

FEISHU_BASE_URL = "https://open.feishu.cn/open-apis"


def _extract_text(val) -> str:
    """从飞书字段值提取纯文本（兼容字符串/数字/数组/对象）"""
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, (int, float)):
        # 整数型工号去掉小数
        return str(int(val)) if float(val).is_integer() else str(val)
    if isinstance(val, list):
        parts = []
        for seg in val:
            if isinstance(seg, dict):
                parts.append(seg.get("text", "") or seg.get("name", ""))
            else:
                parts.append(str(seg))
        return "".join(parts).strip()
    if isinstance(val, dict):
        return str(val.get("text", val.get("name", ""))).strip()
    return str(val).strip()


def _normalize_date(val) -> str:
    """把飞书日期字段（毫秒时间戳）或字符串归一化为 YYYY-MM-DD"""
    from datetime import datetime
    if val is None or val == "":
        return ""
    # 时间戳（毫秒）
    try:
        ts = int(val)
        if ts > 10_000_000_000:  # 毫秒级
            ts = ts / 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass
    # 字符串
    s = _extract_text(val)
    return s.replace("/", "-")[:10]


class FeishuClient:
    """飞书多维表格操作客户端"""

    def __init__(self):
        self._token: Optional[str] = None
        self._token_expire: float = 0

    # ─────────────────────────────────────────────
    # 认证
    # ─────────────────────────────────────────────
    def _get_token(self) -> str:
        """获取 tenant_access_token（带缓存，过期自动刷新）"""
        if self._token and time.time() < self._token_expire - 60:
            return self._token

        resp = requests.post(
            f"{FEISHU_BASE_URL}/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise Exception(f"飞书获取 Token 失败: {data}")

        self._token = data["tenant_access_token"]
        self._token_expire = time.time() + data.get("expire", 7200)
        log.info("飞书 Token 已刷新")
        return self._token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    # ─────────────────────────────────────────────
    # 读取现有记录
    # ─────────────────────────────────────────────
    def get_all_records(self, table_id: str) -> list[dict]:
        """
        读取多维表格某 Sheet 的所有记录
        自动处理分页（飞书每页最多 500 条）
        返回: [{"record_id": "xxx", "fields": {...}}, ...]
        """
        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{FEISHU_BASE_ID}/tables/{table_id}/records"
        all_records = []
        page_token = None

        while True:
            params = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token

            resp = requests.get(url, headers=self._headers(), params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                raise Exception(f"飞书读取记录失败: {data}")

            items = data.get("data", {}).get("items", [])
            all_records.extend(items or [])

            # 判断是否还有下一页
            has_more = data.get("data", {}).get("has_more", False)
            page_token = data.get("data", {}).get("page_token")
            if not has_more or not page_token:
                break

        log.info(f"从飞书读取 {len(all_records)} 条记录（表: {table_id}）")
        return all_records

    def get_latest_record_per_employee(self, table_id: str) -> dict[str, dict]:
        """
        获取每个员工的最新一条薪资记录
        返回: {"工号": {"薪资包": ..., "固定工资": ..., ...}}
        """
        all_records = self.get_all_records(table_id)

        latest: dict[str, dict] = {}
        for rec in all_records:
            fields = rec.get("fields", {})
            emp_no = str(fields.get(FEISHU_FIELDS["工号"], "")).strip()
            if not emp_no:
                continue

            # 用生效日期比较，取最新的
            eff_date = str(fields.get(FEISHU_FIELDS["生效日期"], ""))
            if emp_no not in latest:
                latest[emp_no] = fields
            else:
                existing_date = str(latest[emp_no].get(FEISHU_FIELDS["生效日期"], ""))
                if eff_date > existing_date:
                    latest[emp_no] = fields

        log.info(f"飞书中已有 {len(latest)} 名员工的薪资记录")
        return latest

    def get_existing_emp_date_keys(self, table_id: str) -> set:
        """
        返回飞书已有记录的 (工号, 生效日期YYYY-MM-DD) 集合
        用于「全部薪资档案」增量同步：同一员工有多条历史记录，
        靠 工号+生效日期 唯一区分
        """
        all_records = self.get_all_records(table_id)
        keys = set()
        for rec in all_records:
            fields = rec.get("fields", {})
            emp_no = _extract_text(fields.get(FEISHU_FIELDS["工号"], ""))
            if not emp_no:
                continue
            eff_date = _normalize_date(fields.get(FEISHU_FIELDS["生效日期"], ""))
            keys.add((emp_no, eff_date))
        log.info(f"飞书中已有 {len(keys)} 条 (工号+生效日期) 记录")
        return keys

    # ─────────────────────────────────────────────
    # 写入记录
    # ─────────────────────────────────────────────
    def create_record(self, table_id: str, fields: dict) -> bool:
        """
        在多维表格中新增一条记录
        fields: 飞书字段名 -> 值 的字典
        """
        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{FEISHU_BASE_ID}/tables/{table_id}/records"

        resp = requests.post(
            url,
            headers=self._headers(),
            json={"fields": fields},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            log.error(f"飞书新增记录失败: {data}")
            return False

        record_id = data.get("data", {}).get("record", {}).get("record_id", "")
        log.info(f"飞书新增记录成功: {record_id}")
        return True

    def batch_create_records(self, table_id: str, records: list[dict]) -> int:
        """
        批量新增记录（每批最多 500 条）
        返回成功新增的数量
        """
        if not records:
            return 0

        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{FEISHU_BASE_ID}/tables/{table_id}/records/batch_create"
        success_count = 0

        # 每批 500 条
        batch_size = 500
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            resp = requests.post(
                url,
                headers=self._headers(),
                json={"records": [{"fields": r} for r in batch]},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                log.error(f"飞书批量新增失败（批次 {i // batch_size + 1}）: {data}")
                continue

            created = len(data.get("data", {}).get("records", []))
            success_count += created
            log.info(f"飞书批量新增第 {i // batch_size + 1} 批: {created} 条")

        return success_count

    def update_record(self, table_id: str, record_id: str, fields: dict) -> bool:
        """更新一条记录"""
        url = (f"{FEISHU_BASE_URL}/bitable/v1/apps/{FEISHU_BASE_ID}"
               f"/tables/{table_id}/records/{record_id}")
        resp = requests.put(
            url,
            headers=self._headers(),
            json={"fields": fields},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            log.error(f"飞书更新记录失败: {data}")
            return False
        return True

    def get_records_by_emp_no(self, table_id: str, emp_field: str = "工号") -> dict:
        """
        读取所有记录，按工号建索引。
        返回: {"008001": {"record_id": "xxx", "fields": {...}}, ...}
        """
        all_records = self.get_all_records(table_id)
        result = {}
        for rec in all_records:
            fields = rec.get("fields", {})
            emp_no = _extract_text(fields.get(emp_field, ""))
            if emp_no:
                result[emp_no] = {
                    "record_id": rec.get("record_id", ""),
                    "fields": fields,
                }
        log.info(f"飞书表按工号索引: {len(result)} 条（表: {table_id}）")
        return result

    # ─────────────────────────────────────────────
    # 消息通知
    # ─────────────────────────────────────────────
    def send_notification(self, user_ids: list[str], title: str, content_lines: list[str]):
        """
        向指定用户发送飞书消息通知（富文本格式）。
        user_ids: 飞书 user_id 列表
        title: 消息标题
        content_lines: 消息正文，每个元素为一行文本
        """
        # 构建富文本内容
        content_blocks = []
        for line in content_lines:
            content_blocks.append([{"tag": "text", "text": line}])

        post_body = {
            "zh_cn": {
                "title": title,
                "content": content_blocks,
            }
        }

        url = f"{FEISHU_BASE_URL}/im/v1/messages"
        success = 0
        for uid in user_ids:
            try:
                resp = requests.post(
                    url,
                    headers=self._headers(),
                    params={"receive_id_type": "user_id"},
                    json={
                        "receive_id": uid,
                        "msg_type": "post",
                        "content": json.dumps(post_body),
                    },
                    timeout=15,
                )
                data = resp.json()
                if data.get("code") == 0:
                    success += 1
                    log.info(f"飞书通知发送成功: user_id={uid}")
                else:
                    log.error(f"飞书通知发送失败: user_id={uid}, {data}")
            except Exception as e:
                log.error(f"飞书通知发送异常: user_id={uid}, {e}")

        log.info(f"飞书通知发送完成: {success}/{len(user_ids)} 人成功")
        return success

