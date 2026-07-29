"""一次性回填脚本：为存量产品的事实卡补「主体名称」与「主体数量」结构化字段。

背景：新增的阶梯降级重试（第三、四档数量强调）与数量硬校验都依赖事实卡里的
结构化「主体数量」。存量产品的事实卡是在新字段上线前生成的，没有这两个字段。

策略（用户明确要求）：
- 尽力从现有文本（关键结构.数量、保真锁、整体特征、主体定义）里解析出数量，
  解析不到就留空，绝不编造。
- 凡是写入了「主体数量」的，一律同时置「主体数量待确认 = true」。
  降级逻辑与硬校验见到该标志即视为「无数量」跳过，直到你在产品详情页逐个
  核对后取消勾选，值才真正生效。这样回填值不会静默生效。
- 不会改写已有的人工填写值（若 fact_card 已存在 主体数量 且非空，跳过）。
- 幂等：可重复运行，只补缺失字段。

运行方式（手动执行，不会自动跑）：
    python scripts/backfill_subject_count.py
    # 或指定 storage_root：
    python scripts/backfill_subject_count.py --storage-root D:/weige/storage

运行后会打印一份清单，列出每个产品的解析结果、来源文本、是否写入，便于核对。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 让脚本能 import app 包（项目根 = 脚本上一级目录）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.services.fact_card import save_metadata_json  # noqa: E402

_CN_NUM = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "两": 2,
}

# 阿拉伯数字 + 量词；中文数字 + 量词
_COUNT_PATTERNS = [
    re.compile(r"(\d+)\s*(个|匹|只|尊|件|对|串|组|套|头|条|棵|株|瓶|盏|座|枚|碟|盏)"),
    re.compile(r"([一二三四五六七八九十两]+)\s*(个|匹|只|尊|件|对|串|组|套|头|条|棵|株|瓶|盏|座|枚|碟|盏)"),
]

_GENERIC_NAMES = {"可见商品主体", "商品主体", "主体", "商品"}


def _cn_to_int(s: str) -> int | None:
    """中文/阿拉伯数字字符串转 int；不支持的不返回。"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    if s in _CN_NUM:
        return _CN_NUM[s]
    if "十" in s:
        parts = s.split("十")
        tens = _CN_NUM[parts[0]] if parts[0] else 1
        ones = _CN_NUM[parts[1]] if len(parts) > 1 and parts[1] else 0
        if tens and (ones is not None):
            return tens * 10 + ones
    return None


def _parse_count_field(raw) -> int | None:
    """从关键结构的「数量」字段提取整数，如 '5' / '5匹' / '五' / '无法确认'。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw) if raw > 0 else None
    s = str(raw).strip()
    if not s or s in ("无法确认", "不确定", "不详", "未知", "无", "-"):
        return None
    m = re.search(r"\d+", s)
    if m:
        return int(m.group(0))
    return _cn_to_int(s)


def _extract_count(fact_card: dict) -> tuple[int | None, str]:
    """尽力从现有文本解析主体数量。返回 (数量, 来源说明)。"""
    # 1. 关键结构的「数量」字段（最可靠）
    for ks in fact_card.get("关键结构", []) or []:
        if not isinstance(ks, dict):
            continue
        name = ks.get("名称", "")
        cnt = _parse_count_field(ks.get("数量", ""))
        if cnt:
            return cnt, f"关键结构「{name}」.数量={ks.get('数量')}"

    # 2. 保真锁 / 整体特征 / 主体定义 文本里的「数字+量词」线索
    for field in ("保真锁", "整体特征", "主体定义"):
        val = fact_card.get(field, "")
        if isinstance(val, list):
            text = "；".join(str(x) for x in val)
        else:
            text = str(val or "")
        for pat in _COUNT_PATTERNS:
            m = pat.search(text)
            if m:
                n = _cn_to_int(m.group(1))
                if n and n > 0:
                    return n, f"{field}文本命中「{m.group(0)}」"
    return None, ""


def _extract_name(fact_card: dict) -> str | None:
    """尽力提取主体统称。"""
    for ks in fact_card.get("关键结构", []) or []:
        if not isinstance(ks, dict):
            continue
        name = str(ks.get("名称", "")).strip()
        if name and name not in _GENERIC_NAMES:
            return name
    for key in ("主体定义", "商品名称"):
        val = str(fact_card.get(key, "") or "").strip()
        if val:
            return val
    return None


def backfill(storage_root: Path | None = None, dry_run: bool = False) -> int:
    settings = Settings()
    if storage_root:
        settings = Settings(storage_root=storage_root)

    metadata_dir = settings.storage_root / "metadata"
    if not metadata_dir.exists():
        print(f"未找到 metadata 目录：{metadata_dir}")
        return 0

    written = 0
    skipped_existing = 0
    no_count = 0
    total = 0

    print(f"{'产品ID':<38} {'名称':<16} {'数量':<6} {'写入':<6} 来源")
    print("-" * 110)

    for json_path in sorted(metadata_dir.glob("product-*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  SKIP {json_path.name}: {exc}")
            continue

        total += 1
        product_id = data.get("product_id", json_path.stem)
        fact_card = data.get("fact_card")
        if not isinstance(fact_card, dict):
            print(f"{product_id:<38} {'(无事实卡)':<16} {'-':<6} 跳过   无事实卡")
            continue

        # 幂等：已有有效主体数量（非空、非待确认）则跳过
        existing_count = fact_card.get("主体数量")
        existing_unconfirmed = fact_card.get("主体数量待确认")
        if existing_count is not None and not existing_unconfirmed:
            skipped_existing += 1
            name = str(fact_card.get("商品名称", ""))[:14]
            print(f"{product_id:<38} {name:<16} {existing_count:<6} 跳过   已有有效数量")
            continue

        name = _extract_name(fact_card)
        count, source = _extract_count(fact_card)
        display_name = (str(fact_card.get("商品名称", "")) or name or "")[:14]

        if count is None:
            no_count += 1
            print(f"{product_id:<38} {display_name:<16} {'空':<6} 未写入 未能解析出数量")
            # 仍可写入主体名称（若有），方便后续人工填数量
            if name and not fact_card.get("主体名称"):
                fact_card["主体名称"] = name
                if not dry_run:
                    save_metadata_json(json_path, data)
            continue

        # 写回：数量 + 待确认标志 + 主体名称
        fact_card["主体数量"] = count
        fact_card["主体数量待确认"] = True
        if name:
            fact_card["主体名称"] = name
        written += 1
        print(f"{product_id:<38} {display_name:<16} {count:<6} 写入   {source}")

        if not dry_run:
            save_metadata_json(json_path, data)

    print("-" * 110)
    print(
        f"完成：共 {total} 个产品，写入数量 {written}，"
        f"已有有效数量跳过 {skipped_existing}，未能解析 {no_count}。"
    )
    print("注意：所有写入的数量均标记为「待确认」，需在产品详情页逐个核对后取消勾选才生效。")
    if dry_run:
        print("（本次为 dry-run，未实际写盘）")
    return written


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回填存量产品事实卡的主体数量字段（标记为待确认）")
    parser.add_argument("--storage-root", type=str, default=None, help="storage 根目录，默认用 .env 配置")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写盘")
    args = parser.parse_args()
    backfill(
        storage_root=Path(args.storage_root) if args.storage_root else None,
        dry_run=args.dry_run,
    )
