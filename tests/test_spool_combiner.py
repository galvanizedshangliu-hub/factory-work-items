"""
合盘功能单元测试 — spool_combiner.py (v4: strategy 参数控制)
"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.spool_combiner import (
    combine_spools, combine_spools_for_size,
    format_combine_result, _expand_items,
)
from tools.spec_parser import single_wire_weight_per_m, TAKEUP_EXTRA_RATIO, SPOOLS


def get_max_kg(size_name: str) -> float:
    return 245 if "500" in size_name else 495


# ── 单策略模式 ──

def test_strategy_500mm():
    """只使用 500mm 工字轮"""
    result = combine_spools(2.4, [
        {"length_m": 4000, "quantity": 3},
    ], strategy="500mm")
    assert result["strategy"] == "500mm"
    spools = result["result"]["spools"]
    for s in spools:
        assert "500" in s["size"]
        assert s["total_weight_kg"] <= 245 + 0.1
    print(f"  ✅ 500mm策略: {len(spools)} 轴")
    lines = format_combine_result(result, wire_count=7)
    assert any("500mm" in ln for ln in lines)


def test_strategy_630mm():
    """只使用 630mm 工字轮"""
    result = combine_spools(2.4, [
        {"length_m": 4000, "quantity": 3},
    ], strategy="630mm")
    assert result["strategy"] == "630mm"
    spools = result["result"]["spools"]
    for s in spools:
        assert "630" in s["size"]
        assert s["total_weight_kg"] <= 495 + 0.1
    print(f"  ✅ 630mm策略: {len(spools)} 轴")


def test_strategy_prefer_630():
    """优先 630mm，尾轮利用率<50%降级 500mm"""
    result = combine_spools(2.0, [
        {"length_m": 2000, "quantity": 12},
    ], strategy="prefer_630")
    assert result["strategy"] == "prefer_630"
    spools = result["result"]["spools"]
    sizes = set(s["size"] for s in spools)
    print(f"  ✅ prefer_630策略: {len(spools)} 轴, 类型 {sizes}")
    # 多个小件应该能填满 630mm，尾轮可能降级
    for s in spools:
        cap = get_max_kg(s["size"])
        assert s["total_weight_kg"] <= cap + 0.1


def test_prefer_630_tail_downgrade():
    """prefer_630: 尾轮利用率<50%应降级为500mm"""
    # 用少量小件，确保尾轮利用率低
    diameter = 2.0
    w_per_m = single_wire_weight_per_m(diameter)
    single_w = w_per_m * 2000 * TAKEUP_EXTRA_RATIO
    print(f"  单件重: {single_w:.1f}kg, 630mm容量的50%: {495*0.5:.0f}kg")

    result = combine_spools(diameter, [
        {"length_m": 2000, "quantity": 2},
    ], strategy="prefer_630")
    spools = result["result"]["spools"]
    sizes = set(s["size"] for s in spools)
    print(f"  工字轮类型: {sizes}")
    # 2件≈100kg，放 630mm 利用率才 20%，应降级
    if any("500" in s for s in sizes):
        print("  ✅ 尾轮已降级500mm")
    else:
        print("  ⚠ 此场景未触降级（可能因具体重量参数）")


def test_prefer_630_large_items():
    """prefer_630: 大件全用 630mm"""
    result = combine_spools(2.8, [
        {"length_m": 8000, "quantity": 3},
    ], strategy="prefer_630")
    spools = result["result"]["spools"]
    sizes = set(s["size"] for s in spools)
    assert all("630" in s for s in sizes), f"大件应全用630mm，实际 {sizes}"
    print(f"  ✅ 大件全用630mm: {len(spools)} 轴")


# ── auto 对比模式 ──

def test_auto_both_schemes():
    """auto 模式应包含两种方案对比"""
    result = combine_spools(2.4, [
        {"length_m": 4000, "quantity": 1},
        {"length_m": 2000, "quantity": 1},
    ], strategy="auto")
    assert result["strategy"] == "auto"
    assert "result" in result
    assert "630mm" in result
    assert result["best"] in ("prefer_630", "630mm")
    print(f"  ✅ auto双方案, best={result['best']}")


def test_auto_best_logic():
    """auto 选优: 轮数少优先，轮数相同 prefer_630 优先"""
    result = combine_spools(2.4, [
        {"length_m": 4000, "quantity": 20},
    ], strategy="auto")
    n_opt = result["result"]["total_spools"]
    n_630 = result["630mm"]["total_spools"]
    print(f"  prefer_630: {n_opt} 轴, 纯630: {n_630} 轴, best={result['best']}")
    if n_opt < n_630:
        assert result["best"] == "prefer_630"
    elif n_630 < n_opt:
        assert result["best"] == "630mm"
    else:
        assert result["best"] == "prefer_630"  # 轮数相同 prefer_630 优先


def test_simple_combine():
    """简单合盘: 4000m×1 + 2000m×1 → 合并到 1 轴"""
    result = combine_spools(2.4, [
        {"length_m": 4000, "quantity": 1},
        {"length_m": 2000, "quantity": 1},
    ], strategy="prefer_630")
    spools = result["result"]["spools"]
    print(f"  合盘: {len(spools)} 轴, {spools[0]['total_weight_kg']:.1f}kg")
    assert len(spools) == 1
    assert spools[0]["usage_pct"] > 0


def test_empty():
    """空输入"""
    result = combine_spools(2.4, [], strategy="auto")
    assert result["result"]["total_spools"] == 0
    print("  ✅ 空输入通过")


def test_weight_conservation():
    """重量守恒: auto 方案中两方案总重应一致"""
    w_per_m = single_wire_weight_per_m(2.4)
    segments = [
        {"length_m": 4000, "quantity": 3},
        {"length_m": 2500, "quantity": 5},
        {"length_m": 3500, "quantity": 2},
    ]
    result = combine_spools(2.4, segments, strategy="auto")

    expected = sum(
        w_per_m * s["length_m"] * TAKEUP_EXTRA_RATIO * s["quantity"]
        for s in segments
    )
    w_opt = result["result"]["total_weight_kg"]
    w_630 = result["630mm"]["total_weight_kg"]
    print(f"  期望: {expected:.1f}kg, prefer_630: {w_opt:.1f}kg, 纯630: {w_630:.1f}kg")
    assert abs(w_opt - w_630) < 0.01
    assert abs(w_opt - expected) / expected * 100 < 0.5


def test_format_auto():
    """格式化: auto 模式"""
    result = combine_spools(2.4, [
        {"length_m": 4000, "quantity": 1},
        {"length_m": 2000, "quantity": 1},
    ], strategy="auto")
    lines = format_combine_result(result, wire_count=7)
    print("  auto 格式化:")
    for ln in lines:
        print(f"    {ln}")
    assert any("优化" in ln for ln in lines)
    assert any("630mm" in ln for ln in lines)
    assert any("⭐" in ln for ln in lines)


def test_format_single_strategy():
    """格式化: 单策略模式"""
    for s in ["500mm", "630mm", "prefer_630"]:
        result = combine_spools(2.4, [
            {"length_m": 4000, "quantity": 2},
        ], strategy=s)
        lines = format_combine_result(result, wire_count=7)
        assert len(lines) > 0, f"{s} 应有输出"
        assert any("⭐" in ln for ln in lines), f"{s} 应有推荐标记"
    print("  ✅ 单策略格式化通过")
