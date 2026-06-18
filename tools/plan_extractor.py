"""
从解析后的计划单文档中提取关键数据。
Word 合并单元格 → 数据重复多列 → 按列索引配对规格和段长。
"""
import re


def _find_spec(spec_text: str) -> str:
    """从文本中匹配规格型号"""
    clean = spec_text.replace(" ", "")
    # 1) G1A-1*7/2.40, G1A,7/2.4, G1A-1*7-2.66 等 — G 系列含直径
    #    匹配 G[123][AB] 开头的完整规格，直径用 / 或 - 分隔
    m = re.search(r'G[123][AB].*?(?:\d+\*)?\d+[-/]\d+\.?\d*', clean)
    if m:
        return m.group(0)
    # 2) G2A-1*3.371, G2A-1*2.672 — G 系列 * 直径（无 - 或 /）
    m = re.search(r'G[123][AB].*?\*\d+\.?\d+', clean)
    if m:
        return m.group(0)
    # 3) 1*7-2.4 或 1*7/2.66 格式（独立出现，不在 G 系列中）
    m = re.search(r'1\*\d+[-/]\d+\.?\d*', clean)
    if m:
        return m.group(0)
    # 4) 裸直径（单丝）
    m = re.search(r'(?:φ|直径\s*)?(\d+\.?\d+)\s*(?:mm)?', spec_text)
    if m:
        ctx = re.search(r'(?:镀锌钢丝|钢丝|单丝|镀锌).*?(\d+\.?\d+)', spec_text)
        if ctx:
            return f"单丝 φ{float(ctx.group(1))}mm"
        return f"单丝 φ{float(m.group(1))}mm"
    return ""


def parse_segment_text(text: str) -> list:
    """解析段长文本，支持 '2500*1'、'4000m*15+4805m*1+...'、中文逗号分隔、小数长度"""
    segments = []
    clean = text.replace(" ", "").replace("\n", "+").replace("，", "+").replace(",", "+")
    for m in re.finditer(r'(\d+\.?\d*)m?\*(\d+)', clean):
        seg = {"length_m": float(m.group(1)), "quantity": int(m.group(2))}
        if seg not in segments:
            segments.append(seg)
    return segments


def _get_label(row: list) -> str:
    if not row:
        return ""
    return row[0].replace(" ", "").replace("\n", "")


def _is_unit_or_label(value: str, label: str) -> bool:
    """跳过单位、标签和空值"""
    c = value.replace(" ", "").replace("\n", "")
    if not c:
        return True
    if c == label:
        return True
    if re.match(r'^(m?\*?件|mm|kg/km|倍|%|MPa|g/m²|min浸|次/360°|/$|正|负)$', c):
        return True
    return False


def _col_map_compact(row: list, label: str) -> dict:
    """提取 {col_index: text}，相邻相同值合并（Word 合并单元格副作用）"""
    cmap = {}
    prev_val = None
    for j, cell in enumerate(row):
        v = cell.strip()
        if _is_unit_or_label(v, label):
            prev_val = None
            continue
        c = v.replace(" ", "").replace("\n", "")
        if c == prev_val:
            continue
        cmap[j] = v
        prev_val = c
    return cmap


def _extract_specs(parsed: dict) -> list:
    """
    按段长列的索引匹配规格，返回规格列表。
    逻辑：段长行去重后每个有值列，找左侧最近的规格列配对。
    """
    all_specs = {}  # {normalized_spec: {"segments": set}}

    for sheet_name, rows in parsed.get("sheets", {}).items():
        # ── 找到规格行和段长行 ──
        spec_row = None
        seg_row = None

        for row in rows:
            label = _get_label(row)
            if "型号规格" in label and spec_row is None:
                spec_row = row
            elif "段长" in label and "数量" in label and seg_row is None:
                seg_row = row
            if spec_row and seg_row:
                break

        if not spec_row or not seg_row:
            continue

        # ── 规格列映射（相邻去重） ──
        spec_label = _get_label(spec_row)
        spec_cols = _col_map_compact(spec_row, spec_label)
        # {col: spec_text}

        # ── 段长列映射（相邻去重） ──
        seg_label = _get_label(seg_row)
        seg_cols = _col_map_compact(seg_row, seg_label)
        # {col: seg_text}

        # ── 配对：每个段长列找左侧最近的规格 ──
        sorted_spec_cols = sorted(spec_cols.keys())
        if not sorted_spec_cols:
            continue

        for seg_col, seg_text in sorted(seg_cols.items()):
            # 找 ≤ seg_col 的最近规格列
            spec_col = None
            for sc in sorted_spec_cols:
                if sc <= seg_col:
                    spec_col = sc
                else:
                    break
            if spec_col is None:
                spec_col = sorted_spec_cols[0]  # 兜底取第一个

            raw_spec = spec_cols[spec_col]
            norm = _find_spec(raw_spec) or raw_spec
            if not norm:
                continue

            if norm not in all_specs:
                all_specs[norm] = {"segments": set()}

            # 解析段长（跳过 "见附件" 等非数字文本）
            segs = parse_segment_text(seg_text) if seg_text else []
            for s in segs:
                all_specs[norm]["segments"].add((s["length_m"], s["quantity"]))

    # ── 组装结果 ──
    result = []
    for norm, info in all_specs.items():
        segs = [{"length_m": l, "quantity": q} for l, q in sorted(info["segments"])]
        result.append({
            "spec": norm,
            "segments": segs,
        })
    return result


def extract_plan_data(parsed: dict, ocr_segments: list = None) -> dict:
    """从 Word/Excel 解析结果中提取生产计划单关键字段。"""
    specs = _extract_specs(parsed)

    # ── 追加 OCR 段长 ──
    if ocr_segments:
        from tools.image_ocr import match_ocr_to_specs
        specs = match_ocr_to_specs(specs, ocr_segments)

    result = {
        "specs": specs,
        "length_tolerance": "",
        "note": "",
        "order_no": "",
        "preparer": "",
        "date": "",
    }

    for sheet_name, rows in parsed.get("sheets", {}).items():
        for row in rows:
            if len(row) < 2:
                continue
            label = _get_label(row)
            if not label:
                continue

            if "长度偏差" in label:
                for v in row:
                    if re.search(r'[+\-]\d+', v):
                        result["length_tolerance"] = v
                        break
            elif label == "备注":
                for v in row:
                    if len(v) > 10:
                        result["note"] = v
                        break
            elif "订单编号" in label:
                for v in row:
                    if v != "/":
                        result["order_no"] = v
                        break
            elif "编制" in label:
                for v in row:
                    if not any(kw in v.replace(" ", "") for kw in ("审核", "批准", "日期", "编制")):
                        result["preparer"] = v
                        break
            if "日期" in label:
                for v in row:
                    m = re.search(r'(\d{4}\.\d{1,2}\.\d{1,2}|\d{1,2}\.\d{1,2})', v)
                    if m:
                        result["date"] = m.group(1)
                        break

    return result
