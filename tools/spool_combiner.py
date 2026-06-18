"""
同直径多段长工字轮合盘引擎 — First Fit Decreasing (FFD) 装箱算法

场景: 同一规格（同直径）的不同段长可以合到同一个工字轮上，减少工字轮浪费。

策略（通过 combine_spools 的 strategy 参数选择）:
  - "prefer_630": 优先填 630mm 大轮，尾轮利用率 < 50% 才降级用 500mm
  - "500mm":     只使用 500mm 工字轮
  - "630mm":     只使用 630mm 工字轮
  - "auto":      对比 prefer_630 vs 纯 630mm，选最省工字轮的方案
"""

import math
from tools.spec_parser import (
    SPOOLS,
    TAKEUP_EXTRA_RATIO,
    single_wire_weight_per_m,
    single_wire_takeup_length,
    single_wire_takeup_weight,
)


def _expand_items(segments: list, w_per_m: float) -> list:
    """
    将段长列表（含 quantity）展开为独立的单件列表，每件包含收线重量。
    segments: [{"length_m": 4000, "quantity": 15}, ...]
    返回: [{"length_m": 4000, "weight_kg": 143.2, "qty_left": 1}, ...]
    """
    items = []
    for seg in segments:
        length = seg["length_m"]
        qty = seg["quantity"]
        takeup_len = length * TAKEUP_EXTRA_RATIO
        weight = takeup_len * w_per_m
        for _ in range(qty):
            items.append({
                "length_m": length,
                "weight_kg": round(weight, 2),
                "qty_left": 1,
            })
    return items


def _ffd_pack(items: list, capacities: list) -> dict:
    """
    Standard First Fit Decreasing 装箱（从小容量优先，用于纯单一规格方案）。

    返回: {"spools": [...]}
    """
    caps_sorted = sorted(capacities, key=lambda c: c["max_kg"])

    spools = []

    for item in items:
        placed = False

        # 1) 尝试放入已有工字轮的空隙
        for spool in spools:
            if spool["remaining_kg"] >= item["weight_kg"]:
                spool["items"].append(item)
                spool["remaining_kg"] -= item["weight_kg"]
                placed = True
                break

        if placed:
            continue

        # 2) 开新工字轮（从小到大）
        for cap in caps_sorted:
            if cap["max_kg"] >= item["weight_kg"]:
                spools.append({
                    "size": cap["name"],
                    "capacity_kg": cap["max_kg"],
                    "items": [item],
                    "remaining_kg": round(cap["max_kg"] - item["weight_kg"], 2),
                })
                placed = True
                break

        if placed:
            continue

        # 3) 最大工字轮也装不下 — 拆分
        biggest = max(capacities, key=lambda c: c["max_kg"])
        remaining = item["weight_kg"]

        while remaining > 0.01:
            part = min(remaining, biggest["max_kg"])
            part_item = {
                "length_m": item["length_m"],
                "weight_kg": round(part, 2),
                "qty_left": 1,
            }
            spools.append({
                "size": biggest["name"],
                "capacity_kg": biggest["max_kg"],
                "items": [part_item],
                "remaining_kg": round(biggest["max_kg"] - part, 2),
            })
            remaining -= part

    result_spools = []
    for s in spools:
        total_w = round(sum(it["weight_kg"] for it in s["items"]), 2)
        total_l = round(sum(it["length_m"] for it in s["items"]), 1)
        result_spools.append({
            "size": s["size"],
            "capacity_kg": s["capacity_kg"],
            "items": s["items"],
            "total_weight_kg": total_w,
            "total_length_m": total_l,
            "usage_pct": round(total_w / s["capacity_kg"] * 100, 1),
        })

    return {"spools": result_spools}


def _ffd_pack_optimized(items: list, capacities: list) -> dict:
    """
    优化 FFD 装箱：优先 630mm 大轮，装完后尾轮利用率 < 50% 降级 500mm。

    规则:
      - 开新工字轮时从大到小试（630mm 优先），不做单件降级
      - 全部装完后，检查每个 630mm 轮：
        若最终利用率 < 50% 且总重 ≤ 500mm 容量 → 降级为 500mm
      - 已有工字轮的填空不受限制（剩余空间照填，不同米段自然组合）
    """
    caps_sorted = sorted(capacities, key=lambda c: c["max_kg"], reverse=True)

    cap_500 = next((c for c in caps_sorted if "500" in c["name"]), None)
    cap_630 = next((c for c in caps_sorted if "630" in c["name"]), None)
    max_500 = cap_500["max_kg"] if cap_500 else 245
    threshold_630 = cap_630["max_kg"] * 0.5 if cap_630 else 0

    spools = []

    for item in items:
        placed = False

        # 1) 尝试放入已有工字轮（填空优先，不同米段自然组合）
        for spool in spools:
            if spool["remaining_kg"] >= item["weight_kg"]:
                spool["items"].append(item)
                spool["remaining_kg"] -= item["weight_kg"]
                placed = True
                break

        if placed:
            continue

        # 2) 开新工字轮：从大到小试（630mm 优先），不做单件降级
        for cap in caps_sorted:
            if cap["max_kg"] >= item["weight_kg"]:
                spools.append({
                    "size": cap["name"],
                    "capacity_kg": cap["max_kg"],
                    "items": [item],
                    "remaining_kg": round(cap["max_kg"] - item["weight_kg"], 2),
                })
                placed = True
                break

        if placed:
            continue

        # 3) 最大工字轮也装不下 — 拆分
        biggest = max(capacities, key=lambda c: c["max_kg"])
        remaining = item["weight_kg"]

        while remaining > 0.01:
            part = min(remaining, biggest["max_kg"])
            part_item = {
                "length_m": item["length_m"],
                "weight_kg": round(part, 2),
                "qty_left": 1,
            }
            spools.append({
                "size": biggest["name"],
                "capacity_kg": biggest["max_kg"],
                "items": [part_item],
                "remaining_kg": round(biggest["max_kg"] - part, 2),
            })
            remaining -= part

    # ── 后处理：630mm 尾轮利用率 < 50% 且能放入 500mm → 降级 ──
    for s in spools:
        if "630" not in s["size"]:
            continue
        total_w = round(sum(it["weight_kg"] for it in s["items"]), 2)
        utilization = total_w / s["capacity_kg"]
        if utilization < 0.5 and total_w <= max_500:
            s["size"] = cap_500["name"]
            s["capacity_kg"] = max_500

    result_spools = []
    for s in spools:
        total_w = round(sum(it["weight_kg"] for it in s["items"]), 2)
        total_l = round(sum(it["length_m"] for it in s["items"]), 1)
        result_spools.append({
            "size": s["size"],
            "capacity_kg": s["capacity_kg"],
            "items": s["items"],
            "total_weight_kg": total_w,
            "total_length_m": total_l,
            "usage_pct": round(total_w / s["capacity_kg"] * 100, 1),
        })

    return {"spools": result_spools}


def combine_spools_for_size(diameter_mm: float, segments: list, spool_name: str) -> dict:
    """
    用单一工字轮规格跑 FFD 合盘。
    返回与 combine_spools 相同的结构。
    """
    if not segments or all(s.get("quantity", 0) == 0 for s in segments):
        return {
            "spools": [],
            "total_spools": 0,
            "total_weight_kg": 0.0,
            "w_per_m": single_wire_weight_per_m(diameter_mm),
            "spool_name": spool_name,
        }

    w_per_m = single_wire_weight_per_m(diameter_mm)
    items = _expand_items(segments, w_per_m)
    if not items:
        return {
            "spools": [],
            "total_spools": 0,
            "total_weight_kg": 0.0,
            "w_per_m": w_per_m,
            "spool_name": spool_name,
        }

    items.sort(key=lambda it: it["weight_kg"], reverse=True)

    cap = None
    for size, info in SPOOLS.items():
        if info["name"] == spool_name:
            cap = {"name": info["name"], "max_kg": info["max_weight_kg"]}
            break
    if cap is None:
        cap = {"name": spool_name, "max_kg": 245}
    capacities = [cap]

    result = _ffd_pack(items, capacities)

    for spool in result["spools"]:
        merged = {}
        for it in spool["items"]:
            key = it["length_m"]
            if key not in merged:
                merged[key] = {"length_m": it["length_m"], "weight_kg": 0.0, "qty_left": 0}
            merged[key]["weight_kg"] += it["weight_kg"]
            merged[key]["weight_kg"] = round(merged[key]["weight_kg"], 2)
            merged[key]["qty_left"] += it.get("qty_left", 1)
        spool["items"] = list(merged.values())

    total_w = round(sum(s["total_weight_kg"] for s in result["spools"]), 2)

    return {
        "spools": result["spools"],
        "total_spools": len(result["spools"]),
        "total_weight_kg": total_w,
        "w_per_m": w_per_m,
        "spool_name": spool_name,
    }


def combine_spools(diameter_mm: float, segments: list, strategy: str = "auto") -> dict:
    """
    合盘主函数。

    strategy 可选:
      - "prefer_630": 优先填 630mm 大轮，尾轮利用率 < 50% 降级 500mm
      - "500mm":      只使用 500mm 工字轮
      - "630mm":      只使用 630mm 工字轮
      - "auto":       对比 prefer_630 vs 纯 630mm，选最省工字轮的方案

    返回:
        {"result": {...}, "strategy": str}
        auto 模式额外有: {"630mm": {...}, "best": "prefer_630"|"630mm"}
    """
    if not segments or all(s.get("quantity", 0) == 0 for s in segments):
        empty = {
            "spools": [],
            "total_spools": 0,
            "total_weight_kg": 0.0,
            "w_per_m": single_wire_weight_per_m(diameter_mm),
        }
        return {"result": empty, "strategy": strategy}

    w_per_m = single_wire_weight_per_m(diameter_mm)
    items = _expand_items(segments, w_per_m)
    if not items:
        empty = {
            "spools": [],
            "total_spools": 0,
            "total_weight_kg": 0.0,
            "w_per_m": w_per_m,
        }
        return {"result": empty, "strategy": strategy}

    items.sort(key=lambda it: it["weight_kg"], reverse=True)

    # ── 所有容量选项 ──
    caps_500 = [{"name": SPOOLS[500]["name"], "max_kg": SPOOLS[500]["max_weight_kg"]}]
    caps_630 = [{"name": SPOOLS[630]["name"], "max_kg": SPOOLS[630]["max_weight_kg"]}]
    caps_all = [
        {"name": info["name"], "max_kg": info["max_weight_kg"]}
        for _, info in sorted(SPOOLS.items(), key=lambda kv: kv[1]["max_weight_kg"], reverse=True)
    ]

    # ── 合并显示 ──
    def _merge(spools):
        for spool in spools:
            merged = {}
            for it in spool["items"]:
                key = it["length_m"]
                if key not in merged:
                    merged[key] = {"length_m": it["length_m"], "weight_kg": 0.0, "qty_left": 0}
                merged[key]["weight_kg"] += it["weight_kg"]
                merged[key]["weight_kg"] = round(merged[key]["weight_kg"], 2)
                merged[key]["qty_left"] += it.get("qty_left", 1)
            spool["items"] = list(merged.values())
        return spools

    def _build_result(raw_spools):
        spools = _merge(raw_spools["spools"])
        total_w = round(sum(s["total_weight_kg"] for s in spools), 2)
        return {
            "spools": spools,
            "total_spools": len(spools),
            "total_weight_kg": total_w,
            "w_per_m": w_per_m,
        }

    if strategy == "500mm":
        raw = _ffd_pack(items, caps_500)
        return {"result": _build_result(raw), "strategy": strategy}

    elif strategy == "630mm":
        raw = _ffd_pack(items, caps_630)
        return {"result": _build_result(raw), "strategy": strategy}

    elif strategy == "prefer_630":
        raw = _ffd_pack_optimized(items, caps_all)
        return {"result": _build_result(raw), "strategy": strategy}

    else:  # "auto" — 对比 prefer_630 vs 纯 630mm
        raw_opt = _ffd_pack_optimized(items, caps_all)
        raw_630 = _ffd_pack(items, caps_630)

        opt_result = _build_result(raw_opt)
        s630_result = _build_result(raw_630)

        n_opt = opt_result["total_spools"]
        n_630 = s630_result["total_spools"]
        best = "prefer_630" if n_opt <= n_630 else "630mm"

        return {
            "result": opt_result,
            "630mm": s630_result,
            "best": best,
            "strategy": strategy,
            "w_per_m": w_per_m,
        }


def _format_one_scheme(spools: list, wire_count: int, label: str, is_best: bool) -> list:
    """格式化单个工字轮方案，相同组合自动合并"""
    lines = []
    marker = "⭐ 推荐" if is_best else ""
    lines.append(f"  📐 {label} {marker}（{len(spools)} 个工字轮）")

    if not spools:
        return lines

    # ── 合并相同组合 ──
    # key = (size, frozenset of (length_m, qty_left) tuples)
    groups = {}  # key -> {"spool": spool, "count": n}
    for spool in spools:
        items_key = tuple(sorted(
            (it["length_m"], it["qty_left"]) for it in spool["items"]
        ))
        key = (spool["size"], items_key)
        if key not in groups:
            groups[key] = {"spool": spool, "count": 0}
        groups[key]["count"] += 1

    for idx, (key, group) in enumerate(groups.items(), 1):
        spool = group["spool"]
        count = group["count"]
        size = spool["size"]

        # 收线米数 = 成品段长总和 × 余量系数
        takeup_len = round(spool["total_length_m"] * TAKEUP_EXTRA_RATIO, 0)
        usage = round(spool["total_weight_kg"] / spool["capacity_kg"] * 100, 0)

        item_desc = "+".join(
            f"{it['length_m']:.0f}m×{it['qty_left']}"
            for it in spool["items"]
        )

        set_label = f" ×{count}套" if count > 1 else ""
        lines.append(
            f"     {size}收线{takeup_len:.0f}m(装{usage:.0f}%)：{item_desc}"
            f"（钢丝总重{spool['total_weight_kg']:.1f}kg）{set_label}"
        )

    return lines


def format_combine_result(result: dict, wire_count: int = 1) -> list:
    """
    将合盘结果格式化为可读的输出行。

    单策略模式（500mm/630mm/prefer_630）：只展示一种方案。
    auto 模式：展示 prefer_630 vs 纯 630mm 对比。
    """
    lines = []

    strategy = result.get("strategy", "")

    if strategy == "auto":
        opt = result.get("result")
        scheme_630 = result.get("630mm")
        best = result.get("best", "")

        if not opt or not scheme_630:
            return lines

        lines.extend(_format_one_scheme(
            opt["spools"], wire_count,
            "优化方案（630mm优先，<50%降级500mm）", best == "prefer_630"
        ))
        lines.append(f"")

        lines.extend(_format_one_scheme(
            scheme_630["spools"], wire_count,
            "纯 630mm 工字轮方案", best == "630mm"
        ))

    elif strategy == "prefer_630":
        spools = result["result"]["spools"]
        lines.extend(_format_one_scheme(
            spools, wire_count,
            "优化方案（630mm优先，<50%降级500mm）", True
        ))

    elif strategy == "500mm":
        spools = result["result"]["spools"]
        lines.extend(_format_one_scheme(
            spools, wire_count,
            "纯 500mm 工字轮方案", True
        ))

    elif strategy == "630mm":
        spools = result["result"]["spools"]
        lines.extend(_format_one_scheme(
            spools, wire_count,
            "纯 630mm 工字轮方案", True
        ))

    return lines
