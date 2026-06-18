"""
从 Word 文档嵌入图片中 OCR 提取段长数据。
使用 easyocr（纯 Python，无需外部二进制）做 OCR + 正则解析。
"""
import os
import re


# ── OCR 噪声字符 ──────────────────────────────────────────
# 这些是 easyocr 在表格/印章/签名中常见的误识别字符，总会出现在段长数字后面
_NOISE_CHARS = set("盏立岳炙芒殳益佥击孟各名")


def ocr_segments_from_images(images: list) -> list:
    """
    从图片列表中 OCR 提取段长数据。
    images: [{"name": str, "data": bytes, "size": int}, ...]
    返回: [{"length_m": float, "quantity": int}, ...]
    """
    try:
        import easyocr
    except ImportError:
        print("[image_ocr] easyocr 未安装，跳过 OCR")
        return []

    segments = []
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")

    try:
        reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
    except Exception as e:
        print(f"[image_ocr] easyocr 初始化失败: {e}")
        return []

    for img in images:
        safe_name = img["name"].replace("/", "_").replace("\\", "_")
        img_path = os.path.join(data_dir, "ocr_" + safe_name)

        try:
            with open(img_path, "wb") as f:
                f.write(img["data"])
        except Exception:
            continue

        try:
            results = reader.readtext(img["data"], detail=0)
        except Exception:
            try:
                results = reader.readtext(img_path, detail=0)
            except Exception as e:
                print(f"[image_ocr] OCR 失败: {e}")
                continue

        text = "\n".join(results)
        print(f"[image_ocr] {img['name']}: {len(text)} 字符")
        print(f"[image_ocr]   {text[:300]}...")

        segs = _parse_ocr_text(text)
        print(f"[image_ocr]   提取段长: {segs}")
        segments.extend(segs)

    return segments


def _clean_ocr_line(line: str) -> str:
    """预处理 OCR 文本行，修复已知噪声。返回清理后的文本。"""
    s = line.replace(" ", "")

    # 统一 米 → m
    s = s.replace("米", "m")

    # 统一逗号
    s = s.replace("，", "+").replace(",", "+")

    # m- → m*（如 1080m-1 → 1080m*1）
    s = re.sub(r'm-(\d)', r'm*\1', s)

    # 十 在数字间 → 0（easyocr 把 2500 中的 0 读成十）
    s = re.sub(r'(?<=[\d.])十(?=[\dm\*\+]|$)', '0', s)

    # 删除噪声汉字
    for c in _NOISE_CHARS:
        s = s.replace(c, "")

    # 删除数字和 m 之间的 T（如 +2.76228T → +2.76228）
    s = re.sub(r'(?<=\d)T(?=m)', '', s)

    return s


def _fix_ocr_decimal(length: float) -> float:
    """
    修复 OCR 小数点偏移。
    easyocr 常将 4 位整数的小数点左移（2500→2.5, 2762.28→2.76228），
    实际段长范围 50m~20000m。
    """
    if length <= 0:
        return length
    while length < 50:
        length *= 1000
    return round(length, 2)


def _parse_ocr_text(text: str) -> list:
    """
    从 OCR 文本中提取段长数据。

    识别格式:
      - num m * num         (标准格式: 2500m*17, 198m*1)
      - *num m * num        (前导 *: *2596m*1)
      - +num m * num        (前导 +: +1992m*1)
      - *qty + length       (反向，图片表格格式: *3+1.79882 → 1798.82m×3)
      - +length m num       (无 * 但有前导: +2.76228m1 → 2762.28m×1)
    """
    segments = []

    for line in text.split("\n"):
        clean = _clean_ocr_line(line)

        # ── 模式 A: num m * num（标准格式，含前后导 +/*） ──
        for m in re.finditer(r'(\d+\.?\d*)\s*m\s*\*\s*(\d+)', clean):
            length = _fix_ocr_decimal(float(m.group(1)))
            qty = int(m.group(2))
            _add_if_valid(segments, length, qty)

        # ── 模式 B: *qty + length（反向：图像格式 *3+1.79882 → 1798.82m×3） ──
        for m in re.finditer(r'\*\s*(\d+)\s*[^+]*?\+\s*(\d+\.?\d*)', clean):
            qty = int(m.group(1))
            length = _fix_ocr_decimal(float(m.group(2)))
            _add_if_valid(segments, length, qty)

        # ── 模式 C: +/* length m qty（无 * 但有前导，如 +2762.28m1, *97m1） ──
        for m in re.finditer(r'[+*](\d+\.?\d*)m(\d+)(?![.\d])', clean):
            length = _fix_ocr_decimal(float(m.group(1)))
            qty = int(m.group(2))
            _add_if_valid(segments, length, qty)

    return segments


def _add_if_valid(segments: list, length: float, qty: int):
    """验证并去重添加段长"""
    if 50 < length < 20000 and 1 <= qty <= 200:
        seg = {"length_m": length, "quantity": qty}
        if seg not in segments:
            segments.append(seg)


# ── 将 OCR 段长匹配到规格 ───────────────────────────────────

def match_ocr_to_specs(specs: list, ocr_segments: list) -> list:
    """
    将 OCR 提取的段长数据匹配到缺少段长的规格。

    策略：
    1. 收集所有已有规格的段长集合
    2. OCR 段长中扣除已存在的（去重）
    3. 剩余的 OCR 段长分配给第一个空规格
    """
    if not ocr_segments:
        return specs

    empty_indices = [i for i, s in enumerate(specs) if not s["segments"]]

    if not empty_indices:
        return specs

    # 收集已有的段长（length_m, quantity）集合
    existing = set()
    for s in specs:
        for seg in s.get("segments", []):
            existing.add((seg["length_m"], seg["quantity"]))

    # 从未被覆盖的 OCR 段长
    new_segs = [seg for seg in ocr_segments
                if (seg["length_m"], seg["quantity"]) not in existing]

    if not new_segs:
        print(f"[image_ocr] OCR 段长全部已被其他规格覆盖，无需补充")
        return specs

    print(f"[image_ocr] OCR 共 {len(ocr_segments)} 条，其中 {len(new_segs)} 条为新段长")

    if len(empty_indices) == 1:
        idx = empty_indices[0]
        for seg in new_segs:
            if seg not in specs[idx]["segments"]:
                specs[idx]["segments"].append(seg)
        return specs

    print(f"[image_ocr] {len(empty_indices)} 个规格缺段长，{len(new_segs)} 条新 OCR 段长，跳过自动匹配")
    return specs
