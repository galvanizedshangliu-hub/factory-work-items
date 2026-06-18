"""
钢绞线规格解析 + 理论重量计算 + 镀锌工字轮收线计算

公式:
  7股钢绞线:  W(kg/m) = d² × π × 0.0078 × 1.0045 × 7 / 4
  19股钢绞线: W(kg/m) = d² × π × 0.0078 × 1.0045 × 19 / 4
  单根钢丝:   W(kg/m) = d² × π × 0.0078 × 1      × 1 / 4

  其中 k=1.0045 为捻入系数，仅绞合钢绞线(7股/19股)使用，单丝不用。
"""
import re
import math

DENSITY = 0.0078       # 钢材密度换算系数
TWIST_FACTOR = 1.0045  # 捻入系数（仅7股/19股绞合钢绞线）

# 不含捻入系数的单丝常数: π × 0.0078 / 4
SINGLE_WIRE_CONSTANT = math.pi * DENSITY / 4  # ≈ 0.006126

# 含捻入系数的绞线常数: π × 0.0078 × 1.0045 / 4
STRAND_CONSTANT = math.pi * DENSITY * TWIST_FACTOR / 4  # ≈ 0.006154

# 镀锌收线安全余量系数（检测、取样等额外长度，已包含捻入系数）
# 例：5000m成品 → 5060m → 系数 1.012
TAKEUP_EXTRA_RATIO = 1.012

# ── 工字轮参数 ──
# 容量以重量(kg)为阈值
SPOOLS = {
    500: {"name": "500mm", "max_weight_kg": 245},
    630: {"name": "630mm", "max_weight_kg": 495},
    # 830, 400 暂不参与
}


def parse_spec(spec: str) -> dict:
    """
    从钢绞线规格中提取钢丝根数和单丝直径(mm)。
    支持格式:
      G1A, 7/2.4      → 7根, 直径2.4mm
      G1A, 19/2.4     → 19根, 直径2.4mm
      G1A-1*7-2.4     → 7根, 直径2.4mm
      G1A-35-7*2.50   → 7根, 直径2.50mm
    单根钢丝规格按 n=1 处理。
    """
    # 1) 匹配 "/直径" 如 "7/2.4", "19/2.4", "*7/2.10"（来自 G1A*7/2.10）
    m = re.search(r'(\d+)/(\d+\.?\d*)', spec)
    if m:
        return {"wire_count": int(m.group(1)), "diameter_mm": float(m.group(2))}

    # 2) 匹配末尾 "根数*直径" 如 "7*2.50", "G1A-7*2.50", "G1A-1*7/2.81"
    m = re.search(r'(?:\b|[-_*])(\d+)\*(\d+\.?\d+)$', spec)
    if m:
        return {"wire_count": int(m.group(1)), "diameter_mm": float(m.group(2))}

    # 3) 匹配结尾 "-直径"，前面独立找 7 或 19
    m = re.search(r'-(\d+\.?\d+)$', spec)
    if m:
        diam = float(m.group(1))
        prefix = spec[:m.start()]
        count_m = re.search(r'(?:^|[^.\d])(7|19)(?:[^.\d]|$)', prefix)
        n = int(count_m.group(1)) if count_m else 7
        return {"wire_count": n, "diameter_mm": diam}

    # 4) 单独一根钢丝，如 "2.4mm"、"φ2.4"、"镀锌钢丝，3.8"、"钢丝3.8"、"钢丝，φ3.8"
    m = re.search(r'φ?(\d+\.?\d*)\s*mm', spec)
    if m:
        return {"wire_count": 1, "diameter_mm": float(m.group(1))}

    # 5) 中文单丝：镀锌钢丝3.8、钢丝，3.8、单丝φ3.8 等，只要找到逗号或中文后的裸数字
    m = re.search(r'(?:钢丝|单丝|镀锌钢丝|钢丝直径)\s*[，,]\s*φ?(\d+\.?\d+)$', spec)
    if m:
        return {"wire_count": 1, "diameter_mm": float(m.group(1))}

    # 6) 最后的兜底：提取任何末尾或开头的 φ2.4 / 2.4mm 格式
    m = re.search(r'(?:^|[，,、\s])φ?(\d+\.?\d+)\s*(?:mm)?(?:$|[，,、\s])', spec)
    if m:
        return {"wire_count": 1, "diameter_mm": float(m.group(1))}

    return None


def _get_constant(wire_count: int) -> float:
    """根据股数选择正确的计算公式常数"""
    if wire_count in (7, 19):
        return STRAND_CONSTANT   # 绞线：含捻入系数
    elif wire_count == 1:
        return SINGLE_WIRE_CONSTANT  # 单丝：不含捻入系数
    else:
        return STRAND_CONSTANT  # 默认按绞线处理


def calc_weight_kg_per_m(wire_count: int, diameter_mm: float) -> float:
    """每米重量 (kg/m) = d² × n × 常数"""
    return diameter_mm ** 2 * wire_count * _get_constant(wire_count)


def calc_weight_kg_per_km(wire_count: int, diameter_mm: float) -> float:
    """理论重量 (kg/km)"""
    return calc_weight_kg_per_m(wire_count, diameter_mm) * 1000


def calc_segment_weight_kg(wire_count: int, diameter_mm: float, length_m: float, quantity: int = 1) -> float:
    """单段重量(kg): 每米重量 × 长度 × 数量"""
    return calc_weight_kg_per_m(wire_count, diameter_mm) * length_m * quantity


def single_wire_weight_per_m(diameter_mm: float) -> float:
    """单根钢丝每米重量(kg/m)，不含捻入系数"""
    return diameter_mm ** 2 * SINGLE_WIRE_CONSTANT


def single_wire_takeup_length(diameter_mm: float, finished_length_m: float) -> float:
    """
    单根钢丝在镀锌工序的实际收线长度(m)。
    安全余量系数 1.012 已包含捻入系数。
    """
    return finished_length_m * TAKEUP_EXTRA_RATIO


def single_wire_takeup_weight(diameter_mm: float, finished_length_m: float) -> float:
    """
    单轴（一根钢丝）在镀锌工序的实际收线重量(kg)。
    = 单丝每米重量 × 收线长度
    """
    return single_wire_weight_per_m(diameter_mm) * single_wire_takeup_length(diameter_mm, finished_length_m)


def find_best_spool(diameter_mm: float, finished_length_m: float) -> dict:
    """
    根据单丝直径和成品段长，找到能装下的最小工字轮。
    返回 {"spool": str, "axle_weight_kg": float, "fits": bool, "alternatives": [...]}
    """
    axle_w = single_wire_takeup_weight(diameter_mm, finished_length_m)
    compatible = []

    # 按容量从小到大排序
    for size in sorted(SPOOLS.keys()):
        info = SPOOLS[size]
        fits = axle_w <= info["max_weight_kg"]
        compatible.append({
            "size": info["name"],
            "max_kg": info["max_weight_kg"],
            "fits": fits,
            "usage_pct": round(axle_w / info["max_weight_kg"] * 100, 1),
        })

    # 找能装下的最小工字轮
    best = None
    for c in compatible:
        if c["fits"]:
            best = c
            break

    return {
        "axle_weight_kg": round(axle_w, 1),
        "axle_length_m": round(single_wire_takeup_length(diameter_mm, finished_length_m), 1),
        "best_spool": best,
        "all_spools": compatible,
    }


def calc_spool_allocation(diameter_mm: float, finished_length_m: float, spool_size: int = 500) -> dict:
    """
    计算一个成品段长需要的工字轮分配方案。
    如果一轴装不下，自动拆分成多轴。

    返回:
        {"num_spools": int, "per_spool_length_m": float, "per_spool_weight_kg": float,
         "spool_size": str, "spool_capacity_kg": float, "usage_pct": float}
    """
    import math

    w_per_m = single_wire_weight_per_m(diameter_mm)
    takeup_length = single_wire_takeup_length(diameter_mm, finished_length_m)
    takeup_weight = takeup_length * w_per_m

    if spool_size not in SPOOLS:
        spool_size = 500
    capacity = SPOOLS[spool_size]["max_weight_kg"]

    num = math.ceil(takeup_weight / capacity)
    per_weight = takeup_weight / num
    per_length = takeup_length / num

    return {
        "num_spools": num,
        "per_spool_length_m": round(per_length, 1),
        "per_spool_weight_kg": round(per_weight, 1),
        "total_length_m": round(takeup_length, 1),
        "total_weight_kg": round(takeup_weight, 1),
        "spool_size": SPOOLS[spool_size]["name"],
        "spool_capacity_kg": capacity,
        "usage_pct": round(per_weight / capacity * 100, 1),
    }
