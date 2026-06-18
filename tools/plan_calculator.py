"""
生产计划单计算引擎 — 从计划单数据提取 → 成品重量计算 → 镀锌收线方案
支持多规格
"""
from tools.plan_extractor import extract_plan_data
from tools.spec_parser import (
    parse_spec,
    calc_weight_kg_per_km,
    calc_segment_weight_kg,
    single_wire_weight_per_m,
)
from tools.spool_combiner import combine_spools, format_combine_result


def process_plan(parsed: dict, filename: str, strategy: str = "auto") -> dict:
    """
    完整的计划单处理流程。
    输入: 解析后的文件数据
    strategy: "auto" | "prefer_630" | "500mm" | "630mm"
    输出: {"success": bool, "message": str}
    """
    # 如果有 OCR 段长，传给提取器补充"见附件"的规格
    ocr_data = parsed.get("ocr_segments", [])
    plan = extract_plan_data(parsed, ocr_segments=ocr_data if ocr_data else None)

    specs = plan.get("specs", [])
    if not specs:
        return {"success": False, "message": "❌ 未能从文件中提取到型号规格（如 G1A,7/2.4），请检查文件格式。"}

    # 检查有没有有效的规格
    valid_specs = []
    for s in specs:
        sr = parse_spec(s["spec"])
        if sr and s.get("segments"):
            valid_specs.append({**s, "parsed": sr})
    if not valid_specs:
        return {"success": False, "message": f"❌ 无法解析文件中的规格型号，请确认格式是否正确。"}

    # ── 构建回复 ──
    lines = [f"📋 生产计划单 — {filename}", f""]

    grand_total_kg = 0
    grand_takeup_kg = 0

    for si, spec_info in enumerate(valid_specs, 1):
        sr = spec_info["parsed"]
        wire_count = sr["wire_count"]
        diameter = sr["diameter_mm"]
        uw_km = calc_weight_kg_per_km(wire_count, diameter)

        lines.append(f"━━ 规格{si}: {spec_info['spec']} ━━")
        lines.append(f"结构: 1×{wire_count}，单根钢丝 φ{diameter}mm")
        lines.append(f"成品理论重量: {uw_km:.1f} kg/km")
        lines.append(f"")

        spec_total_kg = 0
        for seg in spec_info["segments"]:
            sw = calc_segment_weight_kg(wire_count, diameter, length_m=seg["length_m"], quantity=seg["quantity"])
            sw_per_m = single_wire_weight_per_m(diameter)
            spec_total_kg += sw
            lines.append(
                f"  {seg['length_m']:.0f}m × {seg['quantity']}件"
                f"  →  {sw:.1f} kg（单丝段重 {sw_per_m * seg['length_m']:.2f} kg）"
            )

        lines.append(f"  成品绞合: {spec_total_kg:.1f} kg = {spec_total_kg/1000:.3f} 吨")

        grand_total_kg += spec_total_kg

        # ── 镀锌收线 ──
        lines.append(f"")
        lines.append(f"  【镀锌半成品】单丝 φ{diameter}mm，每米 {single_wire_weight_per_m(diameter):.6f} kg/m")

        combine_result = combine_spools(diameter, spec_info["segments"], strategy=strategy)
        # 总重从 result 取
        spec_takeup_kg = combine_result["result"]["total_weight_kg"] * wire_count

        lines.extend(format_combine_result(combine_result, wire_count))

        grand_takeup_kg += spec_takeup_kg
        lines.append(f"")

    # ── 合计 ──
    lines.append(f"━━ 合计 ━━")
    lines.append(f"成品总重: {grand_total_kg:.1f} kg = {grand_total_kg/1000:.3f} 吨")
    lines.append(f"镀锌总重: {grand_takeup_kg:.1f} kg = {grand_takeup_kg/1000:.3f} 吨")

    if plan.get("length_tolerance"):
        lines.append(f"长度偏差: {plan['length_tolerance']}")

    return {"success": True, "message": "\n".join(lines)}
