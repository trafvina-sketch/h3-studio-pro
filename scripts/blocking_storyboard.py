# -*- coding: utf-8 -*-
"""
Production Blocking Storyboard & Reference Entity Recognition Engine
(Willow Creek Farm Standard: N scenes x 15s, 4 successive moments, Ref_*.png binding)
"""

from __future__ import annotations

import base64
import html as html_lib
import json
import math
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).resolve().parent.parent
STORYBOARD_DIR = BASE_DIR / "output" / "storyboards"
STORYBOARD_REFS_DIR = BASE_DIR / "output" / "storyboard_refs"
STORYBOARD_PLACEHOLDERS_DIR = BASE_DIR / "output" / "storyboard_placeholders"
SAMPLE_WILLOW_HTML = BASE_DIR / "assets" / "sample_storyboards" / "Willow_Creek_Farm_Ep1_56_Scenes.html"

STORYBOARD_DIR.mkdir(parents=True, exist_ok=True)
STORYBOARD_REFS_DIR.mkdir(parents=True, exist_ok=True)
STORYBOARD_PLACEHOLDERS_DIR.mkdir(parents=True, exist_ok=True)


def _migrate_placeholder_cards_out_of_refs_dir() -> None:
    """
    Di chuyển các thẻ sơ đồ tạm (768x768 màu nền schematic) ra khỏi STORYBOARD_REFS_DIR
    để STORYBOARD_REFS_DIR chỉ chứa ảnh tham chiếu thật do người dùng tải lên hoặc AI vẽ.
    """
    schematic_bgs = {(22, 38, 44), (26, 42, 34), (40, 34, 26), (20, 34, 48)}
    for p in list(STORYBOARD_REFS_DIR.glob("*.png")):
        try:
            with Image.open(p) as im:
                if im.size == (768, 768) and im.getpixel((2, 2))[:3] in schematic_bgs and im.getpixel((12, 12))[:3] != im.getpixel((2, 2))[:3]:
                    dest = STORYBOARD_PLACEHOLDERS_DIR / p.name
                    im.save(dest, "PNG")
            if (STORYBOARD_PLACEHOLDERS_DIR / p.name).is_file():
                with Image.open(p) as im_chk:
                    if im_chk.size == (768, 768) and im_chk.getpixel((2, 2))[:3] in schematic_bgs:
                        p.unlink(missing_ok=True)
        except Exception:
            pass


_migrate_placeholder_cards_out_of_refs_dir()

# Distinct blocking symbol colors (Hex + RGB) for characters, animals, props, locations
ENTITY_PALETTE = [
    ("#ff6b6b", (255, 107, 107)),  # Coral Red (Daisy)
    ("#4ecdc4", (78, 205, 196)),   # Mint Teal (Avery)
    ("#ffe66d", (255, 230, 109)),  # Warm Yellow (Ethan)
    ("#a78bfa", (167, 139, 250)),  # Lavender Purple (Claire)
    ("#60a5fa", (96, 165, 250)),   # Sky Blue (Owen)
    ("#f97316", (249, 115, 22)),   # Orange (Rusty / Animal)
    ("#34d399", (52, 211, 153)),   # Emerald (Clover / Goat)
    ("#f472b6", (244, 114, 182)),  # Pink (Hazel)
    ("#fbbf24", (251, 191, 36)),   # Amber (Bramble)
    ("#c084fc", (192, 132, 252)),  # Violet (Maple Cow)
    ("#38bdf8", (56, 189, 248)),   # Cyan
    ("#a3e635", (163, 230, 53)),   # Lime
    ("#fb7185", (251, 113, 133)),  # Rose
    ("#2dd4bf", (45, 212, 191)),   # Teal
]

ANIMAL_KEYWORDS = {
    "goat", "clover", "hazel", "bramble", "dog", "rusty", "cow", "maple",
    "horse", "sheep", "lamb", "chicken", "hen", "rooster", "duck", "goose",
    "pig", "cat", "kitten", "puppy", "bird", "rabbit", "deer", "fox", "wolf",
    "animal", "pet", "beast", "dragon",
}

LOCATION_KEYWORDS = {
    "entrance", "doorway", "path", "garden", "enclosure", "orchard", "route",
    "barn_doorway", "pasture_entrance", "greenhouse_path", "lettuce_garden",
    "orchard_enclosure", "courtyard", "kitchen", "porch", "yard", "hallway",
    "bedroom", "living_room", "office", "street", "forest", "cliff", "temple",
    "palace", "room", "interior", "exterior", "location", "background", "scene",
    "shed", "pen", "lane", "farmhouse", "paddock", "farmyard", "coop",
}

PROP_KEYWORDS = {
    "gate", "latch", "tub", "hair_tie", "tie", "gloves", "apple", "bandana",
    "bucket", "collar", "bell", "toolbox", "wrench", "bolt", "screw", "nut",
    "hammer", "saw", "pliers", "basket", "cloth", "towel", "jar", "bottle",
    "cup", "mug", "plate", "bowl", "spoon", "fork", "knife", "tray", "pitcher",
    "lantern", "lamp", "flashlight", "rope", "lead", "halter", "fence", "post",
    "hinge", "water_tub", "feed_bucket", "hay", "straw", "hose", "watering_can",
    "seed", "shovel", "rake", "broom", "wheelbarrow", "stool", "bench", "table",
    "chair", "box", "crate", "sack", "bag", "book", "note", "letter", "map",
    "key", "lock", "sword", "blade", "shield", "staff", "orb", "phone", "car",
    "hat", "apron", "coat", "boots", "scarf", "pot", "terracotta", "trowel",
    "seedling", "watering", "can", "checklist", "clipboard", "log", "stack",
}


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibrib.ttf" if bold else r"C:\Windows\Fonts\calibri.ttf",
    ]
    for fp in candidates:
        if os.path.isfile(fp):
            try:
                return ImageFont.truetype(fp, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def clean_ref_display_name(ref_name: str) -> str:
    stem = Path(ref_name.strip()).stem
    if stem.lower().startswith("ref_"):
        stem = stem[4:]
    # Handle _s_ possessive (e.g. Daisy_s_Hair_Tie -> Daisy's Hair Tie)
    stem = re.sub(r"_s_", "'s ", stem)
    stem = stem.replace("_", " ").strip()
    return stem or ref_name


def short_ref_token(ref_name: str) -> str:
    disp = clean_ref_display_name(ref_name)
    if "'s " in disp:
        return disp.split("'s ", 1)[1].strip()
    parts = disp.split()
    if not parts:
        return disp
    # For people like "Daisy Morgan" -> "Daisy", for props like "Pasture Gate" -> "Pasture Gate"
    if len(parts) == 2 and parts[1].lower() in ("morgan", "smith", "chen", "lee", "goat", "cow", "dog"):
        return parts[0]
    return disp


DEFAULT_VISUAL_STYLE = (
    "Original high-end cinematic family farm drama, photorealistic live-action / 3D feature aesthetic, "
    "natural human faces and age-appropriate proportions, believable goat and chicken anatomy, "
    "authentic rural American farm architecture, weathered wooden fences, realistic metal gate hardware, "
    "tactile cotton clothing and natural hair, soft balanced daylight, restrained warm highlights and neutral whites, "
    "physically plausible shadows, filmic color grading, cinematic 35mm and 50mm lenses, natural depth of field, "
    "stable screen geography and grounded movement, 16:9 widescreen. Consistent exact identities, clothing, "
    "animal markings, prop materials, and clean full-bleed camera framing across all shots."
)


def _strip_negative_style_clauses(style_text: str) -> str:
    """
    Theo hướng dẫn chính thức ComfyUI MiniMax H3 (Tip #8 - 'Write bans as positives'):
    H3 chạy qua BasicGuider với CFG=1 (không có nhánh negative conditioning).
    Các câu cấm như 'No cartoon, no anime, no schematic, no text overlay...' sẽ vô tình đưa chính các từ khóa đó
    vào bộ mã hóa văn bản dương. Hàm này tự động lọc bỏ các mệnh đề phủ định đó.
    """
    if not style_text:
        return ""
    cleaned = re.sub(
        r"\bNo\s+cartoon,\s*no\s+anime[^.\n]*\.?",
        "Clean full-bleed cinematic camera framing with organic natural textures.",
        style_text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r";?\s*never\s+render\s+character\s+turnaround\s+sheets[^.\n]*\.?",
        ".",
        cleaned,
        flags=re.IGNORECASE,
    )
    return " ".join(cleaned.split()).strip()


def build_ref_auto_prompt(disp: str, category: str, visual_style: str = "") -> str:
    style_str = _strip_negative_style_clauses((visual_style or DEFAULT_VISUAL_STYLE).strip())
    if category == "character":
        return (
            f"[Visual Style: {style_str}] Single full-body standing portrait of {disp} in natural warm daylight, "
            f"clear facial features, consistent everyday farm outfit, clean unobstructed studio backdrop."
        )
    if category == "animal":
        return (
            f"[Visual Style: {style_str}] Single full-body view of {disp} standing on four grounded legs, "
            f"clear head and natural coat markings, friendly expressive design, warm natural daylight."
        )
    if category == "location":
        return (
            f"[Visual Style: {style_str}] Wide establishing 16:9 outdoor/indoor environment shot of {disp}, "
            f"clear spatial layout, weathered fences, paths and ground plane visible in warm natural daylight."
        )
    return (
        f"[Visual Style: {style_str}] Single clean physical prop view of {disp}, clear hardware and material details, "
        f"consistent scale and authentic farm craftsmanship."
    )


def classify_ref_asset(ref_name: str, color_idx: int = 0, visual_style: str = "") -> Dict[str, Any]:
    raw_clean = ref_name.strip()
    stem_low = Path(raw_clean).stem.lower()
    if stem_low.startswith("ref_"):
        stem_low = stem_low[4:]

    tokens = set(re.split(r"[_\s]+", stem_low))
    disp = clean_ref_display_name(raw_clean)
    short_tok = short_ref_token(raw_clean)

    # Possessive items like Ref_Daisy_s_Hair_Tie.png or Ref_Maple_s_Bell.png are always props/wearables
    if "_s_" in stem_low:
        category = "prop"
    elif stem_low in LOCATION_KEYWORDS or bool(tokens & LOCATION_KEYWORDS):
        category = "location"
    elif bool(tokens & PROP_KEYWORDS):
        category = "prop"
    elif bool(tokens & ANIMAL_KEYWORDS):
        category = "animal"
    else:
        category = "character"

    cat_meta = {
        "character": ("👤 Nhân vật", "Character"),
        "animal": ("🐾 Động vật", "Animal"),
        "prop": ("🧰 Đạo cụ / Vật dụng", "Prop / Item"),
        "location": ("🏞️ Bối cảnh", "Location"),
    }[category]

    hex_col, rgb_col = ENTITY_PALETTE[color_idx % len(ENTITY_PALETTE)]
    auto_prompt = build_ref_auto_prompt(disp, category, visual_style=visual_style)

    matched_path = find_local_ref_image(raw_clean)

    return {
        "filename": raw_clean,
        "display_name": disp,
        "short_name": short_tok,
        "category": category,
        "category_vi": cat_meta[0],
        "category_en": cat_meta[1],
        "color_hex": hex_col,
        "color_rgb": list(rgb_col),
        "auto_prompt": auto_prompt,
        "has_image": bool(matched_path and matched_path.is_file()),
        "local_path": str(matched_path).replace("\\", "/") if matched_path else "",
        "image_url": f"/api/storyboard/ref-image?name={urllib.parse.quote(raw_clean)}" if matched_path else "",
    }


STORYBOARD_CLEAN_H3_REFS_DIR = STORYBOARD_REFS_DIR / "clean_h3"
STORYBOARD_CLEAN_H3_REFS_DIR.mkdir(parents=True, exist_ok=True)


def _is_turnaround_or_grid_ref_sheet(im: Image.Image, filename: str = "") -> bool:
    """
    Phát hiện xem ảnh tham chiếu có phải là bảng multi-panel turnaround sheet / 2x2 location grid / 3x2 prop grid
    có nền trắng + chữ tiêu đề + thước đo chiều cao (như bộ 46 ảnh Ref_*.png) hay không.
    """
    fname_low = Path(filename).name.lower()
    if fname_low.startswith(("lastframe_", "composite_", "storyboard_")):
        return False
    w, h = im.size
    if w < 600 or h < 450:
        return False
    rgb = im.convert("RGB")
    # Kiểm tra dải nền trắng đặc trưng ở viền trên cùng (tiêu đề bảng Turnaround/Location/Prop)
    top_strip = [rgb.getpixel((int(w * fx), int(h * 0.015))) for fx in (0.02, 0.08, 0.92, 0.98)]
    white_corners = sum(1 for (r, g, b) in top_strip if r > 238 and g > 238 and b > 238)
    if white_corners >= 3 and (fname_low.startswith("ref_") or w >= 1200):
        return True
    return False


def prepare_clean_h3_ref_image(img_path: Path | str, category: str = "", force: bool = False) -> Path:
    """
    Tự động tách 1 khung hình đơn (Single Hero View / Single 16:9 Scene) sạch 100% từ bảng tham chiếu đa góc (Turnaround Sheet):
    - Loại bỏ hoàn toàn tiêu đề chữ trên đầu, thước đo chiều cao (110 cm, 65 cm...), nhãn chữ (FRONT, SIDE, BACK, 3/4 VIEW)
      và khung lưới nhiều ô để mô hình MiniMax H3 KHÔNG BAO GIỜ bị nhầm ảnh tham chiếu thành cảnh nền trắng trong video!
    - Lưu bản đã làm sạch vào `output/storyboard_refs/clean_h3/<filename>` (giữ nguyên file gốc trong `storyboard_refs` cho UI).
    """
    p = Path(str(img_path))
    if not p.is_file():
        return p
    if p.parent.name == "clean_h3" or p.name.lower().startswith(("lastframe_", "composite_")):
        return p

    if not category:
        category = classify_ref_asset(p.name).get("category", "prop")

    STORYBOARD_CLEAN_H3_REFS_DIR.mkdir(parents=True, exist_ok=True)
    out_p = STORYBOARD_CLEAN_H3_REFS_DIR / p.name
    try:
        if out_p.is_file() and not force and out_p.stat().st_mtime >= p.stat().st_mtime and out_p.stat().st_size > 1000:
            return out_p
    except Exception:
        pass

    try:
        import numpy as np
        try:
            from scipy import ndimage
        except Exception:
            ndimage = None

        with Image.open(p) as im:
            if not _is_turnaround_or_grid_ref_sheet(im, p.name):
                return p
            rgb = im.convert("RGB")
            w, h = rgb.size

            # 1. LOCATION (Bảng 2x2 góc chụp bối cảnh dưới thanh tiêu đề trắng):
            # Cắt lấy duy nhất ô ảnh 16:9 góc trên bên trái (WIDE ESTABLISHING), bỏ thanh tiêu đề trên & nhãn dưới
            if category == "location" or (w >= 1600 and 900 <= h <= 980):
                crop_box = (int(w * 0.012), int(h * 0.078), int(w * 0.490), int(h * 0.512))
                loc_im = rgb.crop(crop_box)
                loc_im.save(out_p, format="PNG")
                return out_p

            arr = np.asarray(rgb, dtype=np.uint8).copy()
            # Xóa các đường kẻ lưới xám mỏng ngang/dọc trên toàn bảng
            sat = arr.max(axis=2).astype(np.int16) - arr.min(axis=2).astype(np.int16)
            is_grey_line = (sat < 15) & (arr.min(axis=2) < 245) & (arr.min(axis=2) > 160)
            row_grey = is_grey_line.mean(axis=1)
            col_grey = is_grey_line.mean(axis=0)
            for ry in range(h):
                if row_grey[ry] > 0.35:
                    arr[max(0, ry - 2):min(h, ry + 3), :] = 255
            for cx in range(w):
                if col_grey[cx] > 0.30:
                    arr[:, max(0, cx - 2):min(w, cx + 3)] = 255

            # 2. CHARACTER / ANIMAL (Bảng 5 dáng đứng toàn thân + chân dung + thước đo cm):
            # Chọn dải có 5 dáng đứng và tách riêng dáng thứ 2 (3/4 VIEW / THREE-QUARTER FRONT) không dính thước đo & không dính chữ
            if category in ("character", "animal"):
                fg = (arr.min(axis=2) < 240)
                top_right_fg = fg[int(h * 0.14):int(h * 0.53), int(w * 0.80):int(w * 0.97)].mean()
                bot_right_fg = fg[int(h * 0.42):int(h * 0.90), int(w * 0.80):int(w * 0.97)].mean()
                if top_right_fg > bot_right_fg:
                    y0, y1 = int(h * 0.13), int(h * 0.55)
                else:
                    y0, y1 = int(h * 0.42), int(h * 0.935)

                band_arr = arr[y0:y1, :].copy()
                bh, bw = band_arr.shape[:2]
                band_fg = (band_arr.min(axis=2) < 238)

                if ndimage is not None:
                    dilated = ndimage.binary_dilation(band_fg, structure=np.ones((3, 3), dtype=bool))
                    labels, num_features = ndimage.label(dilated)
                    best_lbl = 1
                    best_score = -1.0
                    target_x = bw * 0.275
                    min_area = (bh * bw) * 0.012
                    for lbl_i in range(1, num_features + 1):
                        mask_i = (labels == lbl_i)
                        area = int(np.sum(mask_i))
                        if area < min_area:
                            continue
                        ys_i, xs_i = np.where(mask_i)
                        cx_i = float(xs_i.mean())
                        dist_x = abs(cx_i - target_x) / bw
                        if dist_x < 0.13:
                            score = area / (1.0 + 8.0 * dist_x)
                            if score > best_score:
                                best_score = score
                                best_lbl = lbl_i
                    keep_mask = ndimage.binary_dilation(labels == best_lbl, structure=np.ones((5, 5), dtype=bool))
                    clean_band = np.full_like(band_arr, 255)
                    clean_band[keep_mask] = band_arr[keep_mask]
                else:
                    col_density = band_fg.mean(axis=0)
                    vx1 = int(bw * 0.14) + int(np.argmin(col_density[int(bw * 0.14):int(bw * 0.24)]))
                    vx2 = int(bw * 0.34) + int(np.argmin(col_density[int(bw * 0.34):int(bw * 0.44)]))
                    clean_band = band_arr[:int(bh * 0.92), vx1:vx2]

                final_fg = (clean_band.min(axis=2) < 242)
                ys, xs = np.where(final_fg)
                if len(xs) > 20 and len(ys) > 20:
                    sub_arr = clean_band[
                        max(0, int(ys.min()) - 6):min(clean_band.shape[0], int(ys.max()) + 7),
                        max(0, int(xs.min()) - 6):min(clean_band.shape[1], int(xs.max()) + 7),
                    ]
                else:
                    sub_arr = clean_band
                sub_im = Image.fromarray(sub_arr)
                cw = max(sub_im.width + 80, int(sub_im.height * 0.88))
                ch = sub_im.height + 80
                canvas = Image.new("RGB", (cw, ch), (255, 255, 255))
                canvas.paste(sub_im, ((cw - sub_im.width) // 2, (ch - sub_im.height) // 2))
                canvas.save(out_p, format="PNG")
                return out_p

            # 3. PROP (Bảng 3x2 góc chụp đạo cụ):
            # Cắt lấy ô góc trên bên trái (FRONT) hoặc góc dưới bên phải (THREE-QUARTER), bỏ tiêu đề và nhãn chữ dưới đáy ô
            cell_arr = arr[int(h * 0.115):int(h * 0.442), int(w * 0.025):int(w * 0.318)].copy()
            ch_c, cw_c = cell_arr.shape[:2]
            c_fg = (cell_arr.min(axis=2) < 240)
            if ndimage is not None:
                dil = ndimage.binary_dilation(c_fg, structure=np.ones((5, 5), dtype=bool))
                lbls, n_feat = ndimage.label(dil)
                if n_feat > 0:
                    areas = [int(np.sum(lbls == i)) for i in range(1, n_feat + 1)]
                    best_i = int(np.argmax(areas)) + 1
                    keep_m = ndimage.binary_dilation(lbls == best_i, structure=np.ones((5, 5), dtype=bool))
                    cell_arr[~keep_m] = 255
            c_fg2 = (cell_arr.min(axis=2) < 242)
            ys, xs = np.where(c_fg2)
            if len(xs) > 15 and len(ys) > 15:
                cell_arr = cell_arr[
                    max(0, int(ys.min()) - 6):min(ch_c, int(ys.max()) + 7),
                    max(0, int(xs.min()) - 6):min(cw_c, int(xs.max()) + 7),
                ]
            prop_im = Image.fromarray(cell_arr)
            pw = max(prop_im.width + 72, int(prop_im.height * 1.05))
            ph = max(prop_im.height + 72, int(prop_im.width * 0.85))
            canvas = Image.new("RGB", (pw, ph), (255, 255, 255))
            canvas.paste(prop_im, ((pw - prop_im.width) // 2, (ph - prop_im.height) // 2))
            canvas.save(out_p, format="PNG")
            return out_p
    except Exception:
        return p


def find_local_ref_image(ref_filename: str, clean_for_h3: bool = False) -> Optional[Path]:
    """
    Tìm ảnh tham chiếu thật (uploaded hoặc AI generated) trong STORYBOARD_REFS_DIR
    hoặc trong thư mục sample_storyboards / thư mục gốc References trên ổ đĩa.
    Khi `clean_for_h3=True`, tự động trả về ảnh đơn đã cắt bỏ khung lưới/tiêu đề/thước đo trong `clean_h3/`.
    """
    if not ref_filename:
        return None
    clean_name = Path(ref_filename.strip()).name
    search_dirs = [
        STORYBOARD_REFS_DIR,
        STORYBOARD_DIR,
        BASE_DIR / "assets" / "sample_storyboards" / "refs",
        BASE_DIR / "assets" / "sample_storyboards",
        Path(r"D:\Episode_01_Storyboards_Only_Ep01_Refs\Episode_01_Storyboards_Only_Ep01_Refs\References"),
        Path(r"D:\Episode_01_Storyboards_Only_Ep01_Refs\Episode_01_Storyboards_Only_Ep01_Refs"),
    ]
    stem_target = Path(clean_name).stem.lower()
    found: Optional[Path] = None
    for sdir in search_dirs:
        if not sdir.is_dir():
            continue
        direct = sdir / clean_name
        if direct.is_file():
            found = direct
            break
        for p in sdir.iterdir():
            if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                if p.name.lower() == clean_name.lower() or p.stem.lower() == stem_target:
                    found = p
                    break
        if found:
            break
    if found and clean_for_h3:
        return prepare_clean_h3_ref_image(found)
    return found



def generate_smart_ref_card_image(ref_info: Dict[str, Any], force: bool = False) -> Path:
    """
    Tạo ảnh Reference Card sơ đồ trực quan (Placeholder) cho thực thể Ref_*.png
    khi chưa có ảnh thật tải lên. Lưu tại STORYBOARD_PLACEHOLDERS_DIR để phân biệt rõ với ảnh thật.
    """
    fname = Path(ref_info["filename"]).name
    if not fname.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        fname += ".png"
    out_path = STORYBOARD_PLACEHOLDERS_DIR / fname
    if out_path.is_file() and not force:
        return out_path

    w, h = 768, 768
    cat = ref_info.get("category", "character")
    rgb = tuple(ref_info.get("color_rgb") or (78, 205, 196))
    bg_map = {
        "character": (22, 38, 44),
        "animal": (26, 42, 34),
        "prop": (40, 34, 26),
        "location": (20, 34, 48),
    }
    img = Image.new("RGB", (w, h), bg_map.get(cat, (22, 38, 44)))
    dr = ImageDraw.Draw(img)

    # Border & subtle grid
    for x in range(0, w, 48):
        dr.line([(x, 0), (x, h)], fill=(32, 52, 58), width=1)
    for y in range(0, h, 48):
        dr.line([(0, y), (w, y)], fill=(32, 52, 58), width=1)
    dr.rectangle([12, 12, w - 12, h - 12], outline=rgb, width=4)

    f_title = _load_font(34, bold=True)
    f_sub = _load_font(22, bold=True)
    f_meta = _load_font(18, bold=False)

    # Top badge
    dr.rounded_rectangle([36, 36, w - 36, 118], radius=14, fill=(16, 28, 32), outline=rgb, width=2)
    dr.text((56, 48), ref_info.get("display_name", fname), fill=(243, 212, 136), font=f_title)
    dr.text((56, 86), f"{ref_info.get('filename', fname)}  •  {ref_info.get('category_en', 'Asset')} (SCHEMATIC PLACEHOLDER)", fill=(165, 223, 202), font=f_meta)

    cx, cy = w // 2, h // 2 + 20
    if cat == "character":
        dr.ellipse([cx - 58, cy - 170, cx + 58, cy - 54], fill=rgb, outline=(255, 255, 255), width=3)
        dr.rounded_rectangle([cx - 75, cy - 42, cx + 75, cy + 120], radius=28, fill=rgb, outline=(255, 255, 255), width=3)
        dr.rounded_rectangle([cx - 115, cy - 34, cx - 80, cy + 95], radius=14, fill=rgb, outline=(255, 255, 255), width=2)
        dr.rounded_rectangle([cx + 80, cy - 34, cx + 115, cy + 95], radius=14, fill=rgb, outline=(255, 255, 255), width=2)
        dr.rounded_rectangle([cx - 60, cy + 125, cx - 15, cy + 250], radius=14, fill=rgb, outline=(255, 255, 255), width=2)
        dr.rounded_rectangle([cx + 15, cy + 125, cx + 60, cy + 250], radius=14, fill=rgb, outline=(255, 255, 255), width=2)
    elif cat == "animal":
        dr.rounded_rectangle([cx - 120, cy - 40, cx + 90, cy + 65], radius=30, fill=rgb, outline=(255, 255, 255), width=3)
        dr.ellipse([cx + 55, cy - 110, cx + 155, cy - 15], fill=rgb, outline=(255, 255, 255), width=3)
        for lx in (cx - 100, cx - 50, cx + 25, cx + 65):
            dr.rounded_rectangle([lx, cy + 60, lx + 24, cy + 175], radius=8, fill=rgb, outline=(255, 255, 255), width=2)
    elif cat == "location":
        dr.rectangle([60, cy + 40, w - 60, cy + 190], fill=(34, 68, 52), outline=rgb, width=2)
        dr.polygon([(140, cy + 40), (280, cy - 110), (420, cy + 40)], fill=rgb, outline=(255, 255, 255))
        for fx in range(100, w - 100, 75):
            dr.rectangle([fx, cy + 10, fx + 12, cy + 85], fill=(230, 210, 170))
        dr.line([(90, cy + 32), (w - 90, cy + 32)], fill=(230, 210, 170), width=6)
        dr.line([(90, cy + 62), (w - 90, cy + 62)], fill=(230, 210, 170), width=6)
    else:
        dr.rounded_rectangle([cx - 130, cy - 95, cx + 130, cy + 95], radius=22, fill=rgb, outline=(255, 255, 255), width=4)
        dr.rounded_rectangle([cx - 60, cy - 130, cx + 60, cy - 90], radius=10, outline=(255, 255, 255), width=3)

    dr.rounded_rectangle([36, h - 96, w - 36, h - 32], radius=10, fill=(16, 28, 32), outline=(78, 119, 115), width=2)
    dr.text((54, h - 76), f"Sơ đồ tạm: {ref_info.get('short_name', ref_info.get('display_name', ''))} (Hãy tải ảnh thật hoặc bấm AI Tạo Ảnh)", fill=(243, 212, 136), font=f_sub)
    img.save(out_path, "PNG")
    return out_path


def _clean_html_fragment(frag: str) -> str:
    if not frag:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", frag, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_lib.unescape(s)
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    return "\n".join(lines)


def _extract_embedded_ref_images_from_html(html_text: str) -> int:
    """
    Quét mã HTML đầu vào để tự động nhận diện và trích xuất các thẻ <img> của Ref_*.png
    (hỗ trợ cả ảnh nhúng data:image/...;base64,... hoặc đường dẫn file nội bộ trên máy).
    """
    if not html_text or "<img" not in html_text.lower():
        return 0
    saved_count = 0
    for img_tag in re.findall(r"<img\b[^>]*>", html_text, flags=re.IGNORECASE | re.DOTALL):
        src_m = re.search(r'\bsrc=["\']([^"\']+)["\']', img_tag, flags=re.IGNORECASE)
        if not src_m:
            continue
        src_val = html_lib.unescape(src_m.group(1).strip())
        ref_m = (
            re.search(r'\bdata-ref=["\'](Ref_[A-Za-z0-9_]+\.(?:png|jpg|jpeg|webp))["\']', img_tag, flags=re.IGNORECASE)
            or re.search(r'\balt=["\'][^"\']*?(Ref_[A-Za-z0-9_]+\.(?:png|jpg|jpeg|webp))[^"\']*["\']', img_tag, flags=re.IGNORECASE)
            or re.search(r'\btitle=["\'][^"\']*?(Ref_[A-Za-z0-9_]+\.(?:png|jpg|jpeg|webp))[^"\']*["\']', img_tag, flags=re.IGNORECASE)
            or re.search(r'(Ref_[A-Za-z0-9_]+\.(?:png|jpg|jpeg|webp))', src_val, flags=re.IGNORECASE)
        )
        if not ref_m:
            continue
        ref_fname = Path(ref_m.group(1).strip()).name
        if src_val.startswith("data:image/") and "," in src_val:
            try:
                b64_part = src_val.split(",", 1)[1]
                raw_bytes = base64.b64decode(b64_part)
                if len(raw_bytes) > 128:
                    (STORYBOARD_REFS_DIR / ref_fname).write_bytes(raw_bytes)
                    saved_count += 1
            except Exception:
                pass
        elif not src_val.startswith(("http://", "https://", "/api/")):
            cand = Path(urllib.parse.unquote(src_val.replace("file:///", "").replace("file://", "")))
            if cand.is_file():
                try:
                    (STORYBOARD_REFS_DIR / ref_fname).write_bytes(cand.read_bytes())
                    saved_count += 1
                except Exception:
                    pass
    return saved_count


def parse_blocking_storyboard_html(
    raw_input: str,
    visual_style: str = "",
    global_prompt_addon: str = "",
) -> Dict[str, Any]:
    """
    Bóc tách file HTML Storyboard chuẩn Willow Creek Farm (<article id="E01S001">...)
    hoặc kịch bản văn bản thô thành cấu trúc dữ liệu Storyboard Nhận Diện Thực Thể,
    đồng thời tự động nhận diện cả các ảnh tham chiếu Ref_*.png nhúng/đính kèm trong HTML
    và gắn trọn bộ Prompt + Phong cách (Visual Style) cho TẤT CẢ các phân cảnh.
    """
    text = (raw_input or "").strip()
    if not text and SAMPLE_WILLOW_HTML.is_file():
        text = SAMPLE_WILLOW_HTML.read_text(encoding="utf-8", errors="ignore")

    extracted_img_count = _extract_embedded_ref_images_from_html(text)

    # Preserve existing visual_style / global_prompt_addon from cache if not explicitly passed
    cached_style = ""
    cached_addon = ""
    cache_fp = STORYBOARD_DIR / "current_storyboard.json"
    if cache_fp.is_file():
        try:
            old_data = json.loads(cache_fp.read_text(encoding="utf-8"))
            cached_style = str(old_data.get("visual_style") or "").strip()
            cached_addon = str(old_data.get("global_prompt_addon") or "").strip()
        except Exception:
            pass

    title = "Willow Creek Farm · Episode 1"
    meta_line = "Production blocking storyboard · 16:9"
    intro_desc = (
        "Schematic previs boards showing four successive moments, on-camera cast, "
        "subject paths, camera plan and dialogue. Use the named Ref_ images for final character and location design."
    )

    h1_m = re.search(r"<h1[^>]*>(.*?)</h1>", text, flags=re.IGNORECASE | re.DOTALL)
    if h1_m:
        title = _clean_html_fragment(h1_m.group(1))

    meta_m = re.search(r'<p\s+class=["\']meta["\'][^>]*>(.*?)</p>', text, flags=re.IGNORECASE | re.DOTALL)
    if meta_m:
        meta_line = _clean_html_fragment(meta_m.group(1))

    # Try to detect document-level visual style from HTML or plain text header
    doc_style_m = (
        re.search(r'<p\s+class=["\']style["\'][^>]*>(.*?)</p>', text, flags=re.IGNORECASE | re.DOTALL)
        or re.search(r"(?:Master\s*Style|Visual\s*Style|Art\s*Style|Phong\s*cách(?:\s*hình\s*ảnh|\s*phim)?)\s*:\s*([^\n<]+)", text, flags=re.IGNORECASE)
    )
    detected_doc_style = _clean_html_fragment(doc_style_m.group(1)).strip() if doc_style_m else ""
    eff_visual_style = (visual_style or detected_doc_style or cached_style or DEFAULT_VISUAL_STYLE).strip()
    eff_global_addon = (global_prompt_addon if global_prompt_addon != "" else cached_addon).strip()

    scenes: List[Dict[str, Any]] = []
    article_blocks = re.findall(
        r'<article\s+id=["\']([^"\']+)["\'][^>]*>(.*?)</article>',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if article_blocks:
        for idx, (art_id, art_body) in enumerate(article_blocks, start=1):
            scene_id = art_id.strip()
            h2_m = re.search(r"<h2[^>]*>(.*?)</h2>", art_body, flags=re.IGNORECASE | re.DOTALL)
            h2_text = _clean_html_fragment(h2_m.group(1)) if h2_m else scene_id
            if "·" in h2_text:
                _, location = [x.strip() for x in h2_text.split("·", 1)]
            else:
                location = h2_text

            s_meta_m = re.search(r'<p\s+class=["\']meta["\'][^>]*>(.*?)</p>', art_body, flags=re.IGNORECASE | re.DOTALL)
            s_meta = _clean_html_fragment(s_meta_m.group(1)) if s_meta_m else "15 seconds · 16:9 · two shots · four successive moments"

            img_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', art_body, flags=re.IGNORECASE)
            sb_img = img_m.group(1).strip() if img_m else f"Storyboard_{scene_id}.png"

            # Parse <dt>...</dt><dd>...</dd> pairs — capture ALL fields so no prompt or style is ever dropped
            dt_dd_pairs = re.findall(
                r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>",
                art_body,
                flags=re.IGNORECASE | re.DOTALL,
            )
            fields: Dict[str, str] = {}
            raw_dd_map: Dict[str, str] = {}
            extra_dt_parts: List[str] = []
            scene_style_val = ""

            for dt_raw, dd_raw in dt_dd_pairs:
                k_orig = _clean_html_fragment(dt_raw).strip()
                k = k_orig.lower()
                v = _clean_html_fragment(dd_raw)
                if not v:
                    continue
                if k.startswith("references") or k in ("reference", "ref", "assets", "ảnh tham chiếu"):
                    fields["references"] = v
                    raw_dd_map["references"] = dd_raw
                elif k in ("action and timing", "action & timing", "action", "timing", "diễn biến", "hành động"):
                    fields["action and timing"] = v
                elif k in ("character / animal / prop movement", "movement", "blocking", "spatial blocking", "chuyển động"):
                    fields["movement"] = v
                elif k in ("camera movement", "camera", "camera plan", "shot plan", "góc máy", "máy quay"):
                    fields["camera"] = v
                elif k in ("dialogue", "dialog", "voice", "speech", "lời thoại", "thoại"):
                    fields["dialogue"] = v
                elif any(st_k in k for st_k in ("style", "aesthetic", "phong cách")):
                    scene_style_val = v
                elif any(pr_k in k for pr_k in ("prompt", "lighting", "mood", "atmosphere", "sound", "audio", "note", "continuity")):
                    extra_dt_parts.append(f"{k_orig}: {v}")
                else:
                    extra_dt_parts.append(f"{k_orig}: {v}")

            # Also check if there is any <pre> or <div class="prompt"> inside <article>
            for pre_m in re.findall(r"<pre[^>]*>(.*?)</pre>", art_body, flags=re.IGNORECASE | re.DOTALL):
                pre_txt = _clean_html_fragment(pre_m).strip()
                if pre_txt and "For the target video" not in pre_txt and pre_txt not in extra_dt_parts:
                    extra_dt_parts.append(pre_txt)

            action_timing = fields.get("action and timing", "")
            movement = fields.get("movement", "")
            camera = fields.get("camera", "")
            dialogue_raw = fields.get("dialogue", "")
            refs_raw = fields.get("references", "")
            refs_dd_html = raw_dd_map.get("references", "")
            extra_prompt = "\n".join(extra_dt_parts).strip()

            # Cleanly extract Ref_*.png tokens even if <dd> contains visual preview cards/figures
            regex_refs = re.findall(r"\bRef_[A-Za-z0-9_]+\.(?:png|jpg|jpeg|webp)\b", refs_dd_html or refs_raw or art_body)
            if regex_refs:
                refs_list = list(dict.fromkeys(regex_refs))
            else:
                refs_list = [
                    r.strip()
                    for r in re.split(r"[,;\n]+", refs_raw)
                    if r.strip()
                ]

            dialogue_lines = [ln.strip() for ln in dialogue_raw.splitlines() if ln.strip()]

            scenes.append({
                "index": idx,
                "chip": f"{idx:03d}",
                "scene_id": scene_id,
                "title": h2_text,
                "location": location,
                "meta": s_meta,
                "storyboard_img": Path(sb_img).name,
                "storyboard_url": f"/api/storyboard/previs-image?scene_id={urllib.parse.quote(scene_id)}",
                "action_timing": action_timing,
                "movement": movement,
                "camera": camera,
                "dialogue": "\n".join(dialogue_lines),
                "dialogue_lines": dialogue_lines,
                "references": refs_list,
                "scene_style": scene_style_val or eff_visual_style,
                "extra_prompt": extra_prompt,
            })
    elif text.lstrip().startswith("[") and '"scene"' in text and '"references"' in text:
        # Native parser for scene_manifest.json
        try:
            manifest_items = json.loads(text)
            for idx, item in enumerate(manifest_items, start=1):
                scene_id = str(item.get("scene") or f"E01S{idx:03d}").strip()
                t_val = str(item.get("title") or scene_id).strip()
                loc_val = t_val.split("·", 1)[1].strip() if "·" in t_val else t_val
                dlg_raw = str(item.get("dialogue") or "").strip()
                dlg_lines = [ln.strip() for ln in dlg_raw.splitlines() if ln.strip()]
                scenes.append({
                    "index": idx,
                    "chip": f"{idx:03d}",
                    "scene_id": scene_id,
                    "title": t_val,
                    "location": loc_val,
                    "meta": "15 seconds · 16:9 · two shots · four successive moments",
                    "storyboard_img": f"Storyboard_{scene_id}.png",
                    "storyboard_url": f"/api/storyboard/previs-image?scene_id={urllib.parse.quote(scene_id)}",
                    "action_timing": str(item.get("action") or "").strip(),
                    "movement": str(item.get("movement") or "").strip(),
                    "camera": str(item.get("camera") or "").strip(),
                    "dialogue": "\n".join(dlg_lines),
                    "dialogue_lines": dlg_lines,
                    "references": list(dict.fromkeys(item.get("references") or [])),
                    "scene_style": eff_visual_style,
                    "extra_prompt": "",
                })
        except Exception:
            pass
    elif "H3 VIDEO PROMPT |" in text:
        # Native parser for H3_Prompts/E01S001.txt .. E01S056.txt (single or concatenated)
        h3_blocks = [b.strip() for b in re.split(r"(?=H3 VIDEO PROMPT\s*\|)", text) if b.strip() and "H3 VIDEO PROMPT" in b]
        for idx, blk in enumerate(h3_blocks, start=1):
            m_id = re.search(r"\b(E\d{2}S\d{3})\b", blk)
            scene_id = m_id.group(1).upper() if m_id else f"E01S{idx:03d}"
            m_style = re.search(r"MASTER VISUAL STYLE[^:]*:\s*(.+?)(?=\n\s*\n|\nAPPROVED VISUAL REFERENCE)", blk, flags=re.DOTALL | re.I)
            if m_style and not visual_style:
                eff_visual_style = m_style.group(1).strip()
            m_refs = re.search(r"APPROVED VISUAL REFERENCE FILES[^:]*:\s*(.+?)(?=\n\s*\n|\nSETTING\s*/\s*STORY BEAT)", blk, flags=re.DOTALL | re.I)
            refs_found = re.findall(r"\bRef_[A-Za-z0-9_]+\.(?:png|jpg|jpeg|webp)\b", m_refs.group(1) if m_refs else blk)
            m_beat = re.search(r"SETTING\s*/\s*STORY BEAT:\s*(.+?)(?=\n\s*\n|\nSUBJECTS,\s*BLOCKING)", blk, flags=re.DOTALL | re.I)
            beat_raw = m_beat.group(1).strip() if m_beat else ""
            t_val = f"{scene_id} · Scene {idx}"
            act_val = beat_raw
            if "00." in beat_raw:
                p_split = re.split(r"(?=00\.\d+[–-]\d+\.\d+s:)", beat_raw, maxsplit=1)
                if len(p_split) == 2:
                    t_val = p_split[0].strip().rstrip(".")
                    act_val = p_split[1].strip()
            loc_val = t_val.split("·", 1)[1].strip() if "·" in t_val else t_val
            m_mov = re.search(r"SUBJECTS,\s*BLOCKING[^:]*:\s*(.+?)(?=\n\s*\n|\nCAMERA AND EDIT)", blk, flags=re.DOTALL | re.I)
            m_cam = re.search(r"CAMERA AND EDIT[^:]*:\s*(.+?)(?=\n\s*\n|\nTIMING:)", blk, flags=re.DOTALL | re.I)
            m_dlg = re.search(r"DIALOGUE[^:]*:\s*(.+?)(?=\n\s*\n|\nSOUND:)", blk, flags=re.DOTALL | re.I)
            dlg_lines = [ln.strip() for ln in (m_dlg.group(1) if m_dlg else "").splitlines() if ln.strip()]
            scenes.append({
                "index": idx,
                "chip": f"{idx:03d}",
                "scene_id": scene_id,
                "title": t_val,
                "location": loc_val,
                "meta": "15 seconds · 16:9 · two shots · four successive moments",
                "storyboard_img": f"Storyboard_{scene_id}.png",
                "storyboard_url": f"/api/storyboard/previs-image?scene_id={urllib.parse.quote(scene_id)}",
                "action_timing": act_val,
                "movement": m_mov.group(1).strip() if m_mov else "",
                "camera": m_cam.group(1).strip() if m_cam else "",
                "dialogue": "\n".join(dlg_lines),
                "dialogue_lines": dlg_lines,
                "references": list(dict.fromkeys(refs_found)),
                "scene_style": eff_visual_style,
                "extra_prompt": "",
            })
    else:
        # Fallback: parse plain text / custom script blocks or prompt list into 15s blocking scenes
        raw_blocks = [b.strip() for b in re.split(r"\n\s*\n+", text) if b.strip()]
        # If user pasted single-line prompts (1 prompt per line), split by lines if only 1 block with multiple Scene/Cảnh lines
        if len(raw_blocks) == 1 and len(text.splitlines()) > 1:
            cand_lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not re.match(r"^(?:Master\s*Style|Visual\s*Style|Art\s*Style|Phong\s*cách)\s*:", ln.strip(), re.I)]
            if len(cand_lines) >= 2:
                raw_blocks = cand_lines

        for idx, blk in enumerate(raw_blocks[:60], start=1):
            scene_id = f"E01S{idx:03d}"
            found_refs = re.findall(r"\bRef_[A-Za-z0-9_]+\.(?:png|jpg|jpeg|webp)\b", blk)
            if not found_refs:
                found_refs = ["Ref_Lead_Character.png", "Ref_Main_Location.png"]
            lines = [ln.strip() for ln in blk.splitlines() if ln.strip()]
            first_ln = lines[0] if lines else f"Scene {idx}"
            full_body = "\n".join(lines) if len(lines) > 1 else first_ln
            flat_body = " ".join(lines[1:]) if len(lines) > 1 else first_ln

            # Extract any inline dialogue in quotes
            dlg_found = []
            for ln in lines:
                if re.match(r'^[^:]{1,35}:\s*["“].+["”]$', ln):
                    dlg_found.append(ln)

            scenes.append({
                "index": idx,
                "chip": f"{idx:03d}",
                "scene_id": scene_id,
                "title": f"{scene_id} · {first_ln[:64]}",
                "location": first_ln[:48],
                "meta": "15 seconds · 16:9 · two shots · four successive moments",
                "storyboard_img": f"Storyboard_{scene_id}.png",
                "storyboard_url": f"/api/storyboard/previs-image?scene_id={urllib.parse.quote(scene_id)}",
                "action_timing": (
                    f"00.00–07.50s: Shot 1: {flat_body}\n"
                    f"07.50–15.00s: Shot 2: Continuous follow-through, reaction and spatial settle in {first_ln[:48]}."
                ),
                "movement": flat_body,
                "camera": "Shot A (0–7.5s): smooth eye-level medium tracking shot on the established 180° axis. Shot B (7.5–15s): matched medium-close view, camera remains locked on the same side of the axis.",
                "dialogue": "\n".join(dlg_found),
                "dialogue_lines": dlg_found,
                "references": list(dict.fromkeys(found_refs)),
                "scene_style": eff_visual_style,
                "extra_prompt": full_body,
            })

    # Build deduplicated Reference Registry across all scenes
    ref_order: List[str] = []
    ref_scenes_map: Dict[str, List[str]] = {}
    for sc in scenes:
        for rf in sc.get("references", []):
            if rf not in ref_scenes_map:
                ref_order.append(rf)
                ref_scenes_map[rf] = []
            ref_scenes_map[rf].append(sc["scene_id"])

    ref_registry: List[Dict[str, Any]] = []
    ref_lookup: Dict[str, Dict[str, Any]] = {}
    for c_idx, rf_name in enumerate(ref_order):
        info = classify_ref_asset(rf_name, color_idx=c_idx, visual_style=eff_visual_style)
        info["stt"] = c_idx + 1
        info["scene_count"] = len(ref_scenes_map.get(rf_name, []))
        info["scene_ids"] = ref_scenes_map.get(rf_name, [])
        ref_registry.append(info)
        ref_lookup[rf_name] = info

    # Attach enriched ref objects and compiled H3 preview (with full prompts + visual style) to each scene
    for sc in scenes:
        compiled = compile_blocking_scene_to_h3(
            sc,
            ref_lookup,
            visual_style=eff_visual_style,
            global_prompt_addon=eff_global_addon,
        )
        sc["visual_style"] = compiled["visual_style"]
        sc["visual_style_lock"] = compiled["visual_style_lock"]
        sc["h3_prompt"] = compiled["prompt"]
        sc["picture_bindings"] = compiled["picture_bindings"]
        sc["all_ref_bindings"] = compiled["all_ref_bindings"]

    total_sec = len(scenes) * 15
    total_min = round(total_sec / 60.0, 1)
    if not meta_m:
        meta_line = f"{len(scenes)} scenes × 15 seconds = {total_min:g} minutes · Production blocking storyboard · 16:9"

    # Cache current storyboard state to disk for quick previs rendering & HTML export
    state_obj = {
        "ok": True,
        "title": title,
        "meta": meta_line,
        "description": intro_desc,
        "visual_style": eff_visual_style,
        "global_prompt_addon": eff_global_addon,
        "scene_count": len(scenes),
        "total_seconds": total_sec,
        "ref_count": len(ref_registry),
        "extracted_ref_images": extracted_img_count,
        "ref_registry": ref_registry,
        "scenes": scenes,
    }
    try:
        (STORYBOARD_DIR / "current_storyboard.json").write_text(
            json.dumps(state_obj, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
    return state_obj


def _find_scene_generated_videos() -> Dict[str, Dict[str, Any]]:
    """Quét tìm các file video .mp4 đã tạo cho từng cảnh Storyboard (E01S001..E01S999) kèm metadata ảnh Ref + Prompt."""
    import time as _time
    result: Dict[str, Dict[str, Any]] = {}
    records_fp = BASE_DIR / "temp" / "h3_library_records.json"
    meta_by_path: Dict[str, Dict[str, Any]] = {}
    if records_fp.is_file():
        try:
            recs = json.loads(records_fp.read_text(encoding="utf-8"))
            for r in recs:
                p_str = str(r.get("path") or "").strip()
                if p_str:
                    meta_by_path[p_str.lower().replace("\\", "/")] = r
                sid_r = str(r.get("scene_id") or "").upper().strip()
                if sid_r and p_str and Path(p_str).is_file():
                    fp = Path(p_str)
                    if sid_r.lower() not in fp.name.lower():
                        continue
                    mtime = fp.stat().st_mtime
                    if sid_r not in result or mtime >= result[sid_r].get("mtime", 0):
                        result[sid_r] = {
                            "scene_id": sid_r,
                            "name": fp.name,
                            "path": str(fp).replace("\\", "/"),
                            "url": f"/api/video?path={urllib.parse.quote(str(fp))}",
                            "mtime": mtime,
                            "mtime_str": _time.strftime("%H:%M:%S - %d/%m/%Y", _time.localtime(mtime)),
                            "size_mb": round(fp.stat().st_size / (1024 * 1024), 2),
                            "prompt": r.get("prompt") or "",
                            "visual_style": r.get("visual_style") or "",
                            "reference_images": r.get("reference_images") or [],
                            "seed": r.get("seed"),
                            "duration": r.get("duration") or 15,
                        }
        except Exception:
            pass

    for scan_dir in [Path.home() / "Downloads", BASE_DIR / "output"]:
        if not scan_dir.is_dir():
            continue
        try:
            for fp in scan_dir.glob("*.mp4"):
                m = re.search(r"\b(E\d{2}S\d{3})\b", fp.name, flags=re.IGNORECASE)
                if not m or not fp.is_file() or fp.stat().st_size < 1000:
                    continue
                sid = m.group(1).upper()
                mtime = fp.stat().st_mtime
                if sid in result and result[sid].get("mtime", 0) >= mtime:
                    continue
                rec = meta_by_path.get(str(fp.resolve()).lower().replace("\\", "/")) or {}
                result[sid] = {
                    "scene_id": sid,
                    "name": fp.name,
                    "path": str(fp).replace("\\", "/"),
                    "url": f"/api/video?path={urllib.parse.quote(str(fp))}",
                    "mtime": mtime,
                    "mtime_str": _time.strftime("%H:%M:%S - %d/%m/%Y", _time.localtime(mtime)),
                    "size_mb": round(fp.stat().st_size / (1024 * 1024), 2),
                    "prompt": rec.get("prompt") or "",
                    "visual_style": rec.get("visual_style") or "",
                    "reference_images": rec.get("reference_images") or [],
                    "seed": rec.get("seed"),
                    "duration": rec.get("duration") or 15,
                }
        except Exception:
            pass
    return result


def _grid_position_phrase(idx: int, total: int) -> str:
    """Trả về cụm từ định vị không gian chính xác của thực thể thứ idx bên trong bảng gộp Composite_Refs."""
    if total <= 1:
        return "in"
    cols = 2 if total == 2 else (3 if total == 3 else min(3, max(2, int((total + 1) // 2))))
    rows = max(1, (total + cols - 1) // cols)
    r_row = idx // cols
    r_col = idx % cols
    if rows == 1:
        if cols == 2:
            return "on the left side of" if r_col == 0 else "on the right side of"
        if cols == 3:
            return ("on the left side of", "in the center of", "on the right side of")[min(r_col, 2)]
    v_label = "top" if r_row == 0 else ("bottom" if r_row == rows - 1 else f"row {r_row + 1}")
    if cols == 2:
        h_label = "left" if r_col == 0 else "right"
    else:
        h_label = ("left", "center", "right")[min(r_col, 2)]
    return f"in the {v_label}-{h_label} section of"


def build_overflow_composite_ref_image(scene_id: str, overflow_refs: List[Any], out_filename: str = "") -> Path:
    """
    Khi 1 phân cảnh có nhiều hơn 7 thực thể Ref_*.png (cần dành 1 slot cho Storyboard_E01Sxxx.png và 1 slot cho LastFrame cảnh trước):
    - <Picture 1>..<Picture 6> truyền trực tiếp 6 file ảnh đơn quan trọng nhất (nhân vật, động vật, bối cảnh chính) trong `clean_h3/`.
    - <Picture 7> (Composite_Refs) tự động ghép các ảnh đơn sạch còn lại theo lưới rõ ràng trên nền trung tính,
      KHÔNG vẽ khung viền neon và KHÔNG vẽ chữ tên file để tránh bị nhầm thành bảng chữ trong video.
      Mỗi thực thể trong bảng gộp vẫn được định nghĩa bằng thẻ <Subject k> riêng biệt kèm vị trí không gian cụ thể!
    """
    STORYBOARD_CLEAN_H3_REFS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = STORYBOARD_CLEAN_H3_REFS_DIR / (out_filename or f"Composite_Refs_{scene_id}.png")
    norm_refs: List[Dict[str, Any]] = []
    for item in overflow_refs:
        if isinstance(item, dict):
            norm_refs.append(item)
        elif isinstance(item, str) and item.strip():
            norm_refs.append(classify_ref_asset(item.strip(), 0))
    if not norm_refs:
        norm_refs = [classify_ref_asset("Ref_Placeholder.png", 0)]
    n = len(norm_refs)
    cols = 2 if n == 2 else (3 if n == 3 else min(3, max(2, int((n + 1) // 2))))
    rows = max(1, (n + cols - 1) // cols)
    cell_w, cell_h = 440, 380
    canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), (255, 255, 255))

    for idx, r_info in enumerate(norm_refs):
        r_row = idx // cols
        r_col = idx % cols
        x0 = r_col * cell_w
        y0 = r_row * cell_h
        lp = find_local_ref_image(r_info["filename"], clean_for_h3=True)
        if not lp or not lp.is_file():
            lp = generate_smart_ref_card_image(r_info, force=False)
        try:
            with Image.open(lp) as im:
                im_rgb = im.convert("RGB")
                im_rgb.thumbnail((cell_w - 24, cell_h - 24))
                px = x0 + (cell_w - im_rgb.width) // 2
                py = y0 + (cell_h - im_rgb.height) // 2
                canvas.paste(im_rgb, (px, py))
        except Exception:
            pass

    canvas.save(out_path, format="PNG")
    # Cũng lưu bản sao ở STORYBOARD_REFS_DIR để UI preview hoạt động bình thường
    try:
        canvas.save(STORYBOARD_REFS_DIR / out_path.name, format="PNG")
    except Exception:
        pass
    return out_path


def load_cached_storyboard() -> Dict[str, Any]:
    cache_fp = STORYBOARD_DIR / "current_storyboard.json"
    if cache_fp.is_file():
        try:
            data = json.loads(cache_fp.read_text(encoding="utf-8"))
            eff_style = _strip_negative_style_clauses(str(data.get("visual_style") or DEFAULT_VISUAL_STYLE).strip())
            eff_addon = str(data.get("global_prompt_addon") or "").strip()
            data["visual_style"] = eff_style
            data["global_prompt_addon"] = eff_addon
            # Refresh has_image status, stt, and style-aware auto_prompt from disk
            ref_lookup = {}
            for idx, r in enumerate(data.get("ref_registry", [])):
                r["stt"] = idx + 1
                if not r.get("custom_prompt"):
                    r["auto_prompt"] = build_ref_auto_prompt(
                        r.get("display_name") or clean_ref_display_name(r["filename"]),
                        r.get("category") or "character",
                        visual_style=eff_style,
                    )
                p = find_local_ref_image(r["filename"])
                r["has_image"] = bool(p and p.is_file())
                r["local_path"] = str(p).replace("\\", "/") if p else ""
                r["image_url"] = f"/api/storyboard/ref-image?name={urllib.parse.quote(r['filename'])}&t={int(p.stat().st_mtime) if p else 0}"
                ref_lookup[r["filename"]] = r

            scene_vids = _find_scene_generated_videos()
            for sc in data.get("scenes", []):
                old_custom = str(sc.get("custom_h3_prompt") or "").strip()
                if old_custom and (
                    "at 0.00 seconds into the target video" in old_custom
                    or "APPROVED VISUAL REFERENCE FILES" in old_custom
                    or "8K reference sheet" in old_custom
                    or "never render character turnaround sheets" in old_custom
                    or "4-panel spatial blocking" in old_custom
                    or "Present in the scene with consistent placement" in old_custom
                ):
                    sc["custom_h3_prompt"] = ""
                comp = compile_blocking_scene_to_h3(
                    sc,
                    ref_lookup,
                    visual_style=eff_style,
                    global_prompt_addon=eff_addon,
                )
                sc["visual_style"] = comp["visual_style"]
                sc["visual_style_lock"] = comp["visual_style_lock"]
                sc["compiled_h3_prompt"] = comp["prompt"]
                sc["h3_prompt"] = sc.get("custom_h3_prompt") or comp["prompt"]
                sc["picture_bindings"] = comp["picture_bindings"]
                sc["all_ref_bindings"] = comp["all_ref_bindings"]
                sid = str(sc.get("scene_id") or "").upper()
                if sid in scene_vids:
                    gv = dict(scene_vids[sid])
                    if not gv.get("prompt"):
                        gv["prompt"] = sc["h3_prompt"]
                    if not gv.get("visual_style"):
                        gv["visual_style"] = sc["visual_style"]
                    if not gv.get("reference_images"):
                        gv["reference_images"] = [
                            {
                                "slot": b["picture_index"],
                                "tag": b["picture_tag"],
                                "name": b["filename"],
                                "display_name": b["display_name"],
                                "category_vi": b.get("category_vi", ""),
                                "url": b["image_url"],
                                "path": b.get("local_path", ""),
                            }
                            for b in sc["picture_bindings"]
                        ]
                    sc["generated_video"] = gv
                else:
                    sc["generated_video"] = None
            return data
        except Exception:
            pass
    if SAMPLE_WILLOW_HTML.is_file():
        return parse_blocking_storyboard_html(SAMPLE_WILLOW_HTML.read_text(encoding="utf-8", errors="ignore"))
    return parse_blocking_storyboard_html("")


def _norm_ref_key(s: str) -> str:
    """Chuẩn hóa chuỗi tên file / tên thực thể để so khớp linh hoạt."""
    s = s.lower().strip()
    s = re.sub(r"\.(png|jpg|jpeg|webp|gif|bmp)$", "", s, flags=re.IGNORECASE)
    s = s.replace("'s", "_s").replace("’s", "_s")
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    if s.startswith("ref_"):
        s = s[4:]
    return s


def _extract_stt_from_filename(raw_name: str) -> Optional[int]:
    """
    Trích xuất số thứ tự (STT: 1, 2, 3...) từ tên file ảnh:
    - Ví dụ: '1.png', '01.jpg', '02_Daisy.png', '#3.png', 'stt4.jpg', 'ref_5.png', 'image (6).png', 'anh_07.webp'
    """
    stem = Path(raw_name).stem.strip()
    # Case 1: Toàn bộ tên file là số ('1', '01', '005', '46')
    if re.fullmatch(r"#?\d{1,3}", stem):
        return int(stem.lstrip("#"))
    # Case 2: Bắt đầu bằng STT ('01_...', '1 - ...', '#02_...', 'stt_3', 'stt3', 'ref_04', 'no_5')
    m_lead = re.match(r"^(?:#|stt[_\-\s]*|so[_\-\s]*|no[_\-\s]*|ref[_\-\s]*)?(\d{1,3})(?:[_\-\s\.]+|$)", stem, flags=re.IGNORECASE)
    if m_lead:
        return int(m_lead.group(1))
    # Case 3: Tên chung chung kèm số ở cuối ('image (1)', 'img_02', 'anh 3', 'pic-4', 'download (5)', 'photo_6')
    m_tail = re.match(
        r"^(?:img|image|anh|pic|picture|photo|ref|char|prop|loc|bg|scene|item|file|download|untitled)[_\-\s\(]*(\d{1,3})\)?$",
        stem,
        flags=re.IGNORECASE,
    )
    if m_tail:
        return int(m_tail.group(1))
    return None


def _match_ref_by_name(raw_name: str, ref_registry: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    So khớp tên file ảnh tải lên với danh sách thực thể Ref_*.png:
    Hỗ trợ khớp chính xác hoặc gần đúng: 'Ref_Daisy.png', 'Daisy.jpg', '01_Daisy.png', 'Pasture Gate.png', 'Daisy (1).png'...
    """
    stem = Path(raw_name).stem.strip()
    # Bỏ hậu tố (1), (2), _copy nếu phía trước đã có tên chữ
    stem_no_copy = re.sub(r"[\s_\-]+(?:\(\d+\)|copy\s*\d*)$", "", stem, flags=re.IGNORECASE).strip()
    # Bỏ tiền tố STT ở đầu nếu phía sau có tên chữ (ví dụ '01_Ref_Daisy' -> 'Ref_Daisy', '02 - Clover' -> 'Clover')
    stem_no_lead_num = re.sub(r"^#?\d{1,3}[\s_\-\.]+", "", stem_no_copy).strip()

    candidates_norm = []
    for s_cand in (stem, stem_no_copy, stem_no_lead_num):
        nk = _norm_ref_key(s_cand)
        # Không dùng chuỗi chỉ toàn số hoặc từ chung chung ('img', 'anh', 'image') để khớp theo tên
        if nk and not nk.isdigit() and not re.fullmatch(r"(?:img|image|anh|pic|photo|download|file|stt)_?\d*", nk):
            if nk not in candidates_norm:
                candidates_norm.append(nk)

    if not candidates_norm:
        return None

    # 1. Khớp chính xác theo core stem / display_name / short_name
    for nk in candidates_norm:
        for r in ref_registry:
            r_full = _norm_ref_key(r["filename"])
            r_disp = _norm_ref_key(r.get("display_name", ""))
            r_short = _norm_ref_key(r.get("short_name", ""))
            r_no_poss = r_full.replace("_s_", "s_")
            if nk in (r_full, r_disp, r_short, r_no_poss):
                return r

    # 2. Khớp chứa từ khóa (Fuzzy substring / token match) — ưu tiên tên dài nhất để 'Old Gate Latch' không bị nhầm sang 'Gate'
    sorted_refs = sorted(ref_registry, key=lambda x: len(_norm_ref_key(x["filename"])), reverse=True)
    for nk in candidates_norm:
        nk_tokens = set(nk.split("_"))
        for r in sorted_refs:
            r_full = _norm_ref_key(r["filename"])
            r_short = _norm_ref_key(r.get("short_name", ""))
            r_tokens = set(r_full.split("_"))
            if r_full and (r_full in nk or (len(nk) >= 4 and nk in r_full)):
                return r
            if r_tokens and r_tokens.issubset(nk_tokens):
                return r
            if len(r_short) >= 4 and r_short in nk_tokens:
                return r

    return None


def match_and_save_uploaded_refs(
    files: List[Dict[str, Any]],
    ref_registry: List[Dict[str, Any]],
    target_ref: str = "",
    match_mode: str = "auto",
    candidate_filenames: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Xử lý tải ảnh hàng loạt Ref_*.png với 3 chế độ:
    - match_mode = 'auto': Ưu tiên khớp chính xác tên Ref_*.png trên toàn bộ 46 thực thể trước; nếu không khớp tên thì khớp theo STT (#01, #02... trong tên file hoặc thứ tự chọn file).
    - match_mode = 'name': Khớp theo Tên ảnh ≈ Tên Ref (kể cả '01_Daisy.png', 'Pasture Gate.jpg', 'Ref_Clover.png'...).
    - match_mode = 'stt' : Khớp theo Số Thứ Tự (STT #1, #2, #3... dựa trên số trong tên file '1.png, 2.png...' hoặc thứ tự file tải lên trong nhóm đang hiển thị).
    Đồng thời nếu gặp file ảnh bảng phân cảnh Storyboard_E01Sxxx.png trong thư mục tải lên thì tự động lưu vào STORYBOARD_DIR và không ghi đè vào Ref_*.png!
    """
    matched_results: List[Dict[str, Any]] = []
    if not files or not ref_registry:
        return matched_results

    if candidate_filenames:
        cand_set = set(candidate_filenames)
        active_refs = [r for r in ref_registry if r["filename"] in cand_set]
        if not active_refs:
            active_refs = list(ref_registry)
    else:
        active_refs = list(ref_registry)

    used_ref_names = set()
    seq_cursor = 0

    for idx, item in enumerate(files):
        raw_name = Path(str(item.get("name") or f"ref_{idx+1}.png")).name
        data_uri = str(item.get("data") or "")
        b64 = data_uri.split(",", 1)[1] if "," in data_uri else data_uri
        if not b64:
            continue

        # Nếu là ảnh bảng phân cảnh Storyboard_E01Sxxx.png đi kèm khi tải cả thư mục -> lưu vào STORYBOARD_DIR
        m_sb = re.match(r"^(Storyboard_E\d{2}S\d{3})\.(?:png|jpg|jpeg|webp)$", raw_name, flags=re.IGNORECASE)
        if m_sb and not target_ref:
            sb_fname = f"{m_sb.group(1)}.png"
            try:
                (STORYBOARD_DIR / sb_fname).write_bytes(base64.b64decode(b64))
            except Exception:
                pass
            continue

        chosen_ref: Optional[Dict[str, Any]] = None
        match_reason = ""

        # 1. Trường hợp gắn đích danh 1 ô cụ thể
        if target_ref:
            chosen_ref = next((r for r in ref_registry if r["filename"].lower() == target_ref.lower()), None)
            match_reason = "Đích danh"

        # 2. Nếu tên file bắt đầu bằng 'Ref_' hoặc khớp chính xác với tên thực thể trong ref_registry, LUÔN khớp chính xác theo tên (bất kể bộ lọc tab đang mở)
        if not chosen_ref:
            exact_ref = next(
                (r for r in ref_registry if r["filename"].lower() == raw_name.lower() or _norm_ref_key(r["filename"]) == _norm_ref_key(raw_name)),
                None,
            )
            if exact_ref and match_mode != "stt":
                chosen_ref = exact_ref
                match_reason = f"Theo tên chính xác ({raw_name})"

        # 3. Chế độ 'name' hoặc 'auto': Thử khớp theo Tên Ref trên toàn bộ ref_registry và active_refs
        if not chosen_ref and match_mode in ("auto", "name"):
            by_name = _match_ref_by_name(raw_name, ref_registry) or _match_ref_by_name(raw_name, active_refs)
            if by_name:
                chosen_ref = by_name
                match_reason = f"Theo tên ({raw_name})"

        # Nếu tên file bắt đầu bằng 'Ref_' mà không có trong danh sách thực thể của tập phim hiện tại, lưu thẳng theo tên gốc chứ KHÔNG gán đè sang STT của thực thể khác!
        if not chosen_ref and raw_name.lower().startswith("ref_"):
            out_p = STORYBOARD_REFS_DIR / raw_name
            out_p.write_bytes(base64.b64decode(b64))
            prepare_clean_h3_ref_image(out_p, force=True)
            continue

        # 4. Chế độ 'stt' hoặc 'auto' (khi file không phải Ref_* và chưa khớp tên): Khớp theo Số Thứ Tự (STT)
        if not chosen_ref and match_mode in ("auto", "stt"):
            stt_num = _extract_stt_from_filename(raw_name)
            if stt_num is not None and stt_num >= 1:
                if 1 <= stt_num <= len(active_refs):
                    chosen_ref = active_refs[stt_num - 1]
                    match_reason = f"STT #{stt_num:02d} (từ số file {raw_name})"
                elif 1 <= stt_num <= len(ref_registry):
                    chosen_ref = ref_registry[stt_num - 1]
                    match_reason = f"STT #{stt_num:02d} toàn cục (từ {raw_name})"

            if not chosen_ref:
                while seq_cursor < len(active_refs) and active_refs[seq_cursor]["filename"] in used_ref_names:
                    seq_cursor += 1
                if seq_cursor < len(active_refs):
                    chosen_ref = active_refs[seq_cursor]
                    seq_cursor += 1
                    match_reason = f"STT tuần tự #{chosen_ref.get('stt', seq_cursor):02d}"
                elif idx < len(ref_registry):
                    chosen_ref = ref_registry[idx]
                    match_reason = f"STT #{chosen_ref.get('stt', idx+1):02d}"

        if not chosen_ref:
            if match_mode == "name":
                continue
            chosen_ref = active_refs[idx % len(active_refs)]
            match_reason = f"STT #{chosen_ref.get('stt', 1):02d}"

        save_fname = Path(chosen_ref["filename"]).name
        out_p = STORYBOARD_REFS_DIR / save_fname
        out_p.write_bytes(base64.b64decode(b64))
        prepare_clean_h3_ref_image(out_p, category=chosen_ref.get("category", ""), force=True)
        used_ref_names.add(save_fname)
        matched_results.append({
            "stt": chosen_ref.get("stt", 0),
            "filename": save_fname,
            "display_name": chosen_ref.get("display_name", save_fname),
            "uploaded_name": raw_name,
            "input_name": raw_name,
            "saved_as": save_fname,
            "matched_by": "name" if "Tên" in match_reason or "tên" in match_reason else "stt",
            "match_reason": match_reason,
        })

    return matched_results


def _rewrite_storyboard_negatives_as_positives(text: str) -> str:
    """
    Theo Mẹo #8 của ComfyUI MiniMax H3 Prompt Guide (CFG=1 BasicGuider không có nhánh negative prompt):
    Chuyển toàn bộ các mệnh đề phủ định ('No orbit...', 'no gate closing', 'no animal visible', 'without teleporting'...)
    trong văn bản kịch bản/camera sang mô tả khẳng định dương tính tương đương.
    """
    if not text:
        return ""
    rules = [
        (r"\bNo\s+orbit\s+or\s+screen-direction\s+flip\.?", "Camera angle and left-to-right screen direction remain locked."),
        (r"\bno\s+gate\s+closing\b", "the gate stays wide open"),
        (r"\bNo\s+gate\s+opens\s+or\s+closes\s+again\.?", "The gate remains stationary in its current position."),
        (r"\bno\s+new\s+gate\s*opening\b", "the gate stays stationary"),
        (r"\bno\s+hinge\s+flip\b", "consistent hinge orientation"),
        (r"\bno\s+sudden\s+zoom\b", "steady focal length"),
        (r"\bno\s+latch\s+removal\s+yet\b", "the latch remains fastened in place"),
        (r"\bno\s+animals?\s+visible\b", "only the human characters and farm environment in view"),
        (r"\bno\s+animal\s+arrives\s+in\s+this\s+crop\b", "only the active subject remains in frame"),
        (r"\bno\s+goat\s+appears\s+through\s+it\b", "the open doorway stays clear"),
        (r"\bno\s+goat\s+crosses\s+this\s+shot\b", "the foreground path stays clear"),
        (r"\bno\s+hen\s+appears\s+here\b", "only the active characters remain in frame"),
        (r"\bNo\s+second\s+hen\s+appears\.?", "Only the single hen remains in frame."),
        (r"\bno\s+extra\s+hen\b", "only the single hen in view"),
        (r"\bno\s+new\s+hen\s+or\s+vehicle\b", "only the established subjects in view"),
        (r"\bno\s+new\s+animal\s+or\s+goat\s+already\s+enclosed\b", "the enclosure interior starts empty and clear"),
        (r"\bno\s+animal\s+changes\s+pen\b", "each animal stays in its current pen"),
        (r"\bno\s+animal\s+reverses\s+direction\b", "all animals maintain steady forward movement"),
        (r"\bno\s+reverse\s+exit\b", "steady forward progression"),
        (r"\bno\s+reverse\s+travel\s+or\s+new\s+exit\b", "consistent forward progression along the path"),
        (r"\bno\s+duplicate\s+apple\b", "a single intact apple"),
        (r"\bno\s+body\s+intersects\s+pot\b", "natural physical clearance around the pot"),
        (r"\bno\s+wall\s*penetration\b", "solid physical boundaries"),
        (r"\bno\s+flock\s+panorama\b", "focused medium framing"),
        (r"\bno\s+premature\s+paddock\s+cut\b", "continuous uninterrupted shot"),
        (r"\bno\s+instant\s+arrival\s+elsewhere\b", "continuous real-time movement in the same space"),
        (r"\bno\s+repeated\s+action\b", "smooth single continuous action"),
        (r"\bno\s+repeated\s+entrance\b", "single clean entrance"),
        (r"\bno\s+new\s*arrival\b", "consistent on-screen cast"),
        (r"\bno\s+new\s*prop\b", "consistent on-screen props"),
        (r"\bNo\s+vehicle\s+moves\.?", "The parked vehicle remains stationary."),
        (r"\bNo\s+seat\s+changes\b", "Everyone remains in their seated positions"),
        (r"\bno\s+one\s+enters\s+the\s+greenhouse\b", "everyone stays outside the greenhouse threshold"),
        (r"\bno\s+one\s+steps\s+on\s+plants\b", "everyone steps carefully on the clear path"),
        (r"\bwithout\s+stepping\s+on\s+it\b", "stepping cleanly beside it"),
        (r"\bwithout\s+cloning\s+or\s+swapping\s+positions\b", "maintaining exact individual positions"),
        (r"\bwithout\s+teleporting\b", "moving with smooth continuous motion"),
        (r"\bwithout\s+changing\s+location\b", "staying in the same spot"),
        (r"\bwithout\s+lifting\s+hen\s+or\s+moving\s+fence\b", "keeping the hen on the ground and the fence stationary"),
        (r"\bwithout\s+touching\s+Rusty\b", "keeping a gentle distance from Rusty"),
        (r"\bwithout\s+biting\b", "gently sniffing"),
        (r"\bno\s+handoff\s+or\s+bite\b", "holding the item steadily"),
        (r"\bno\s+shattered\s+extra\s+pieces\b", "clean intact pieces"),
        (r"\bno\s+rust\s+or\s+black\s+coating\b", "clean bright metallic surface"),
        (r"\bnever\s+changes\s+owner\b", "stays in the same character's hand"),
    ]
    out = text
    for pat, rep in rules:
        out = re.sub(pat, rep, out, flags=re.IGNORECASE)
    return out


def sanitize_h3_prompt_no_ref_sheet_bleed(prompt: str) -> str:
    """
    Làm sạch câu lệnh H3 theo đúng hướng dẫn chính thức ComfyUI & MiniMax H3:
    1. Loại bỏ lệnh 'at 0.00 seconds into the target video, <Picture 1>.. are fully referenced' gây nháy ảnh tham chiếu (trừ khi đang dùng <Video 1> hoặc integrated_multimodal_description).
    2. Loại bỏ các từ khóa meta ('reference sheet', 'isolated prop reference photo', 'neutral background').
    3. Loại bỏ các câu phủ định ('No cartoon, no anime...', 'never render character turnaround sheets...', 'no orbit...') vì ở CFG=1
       (BasicGuider không có nhánh negative), việc liệt kê từ khóa cấm sẽ vô tình đưa các từ đó vào bộ mã hóa dương!
    """
    if not prompt:
        return ""
    out = prompt
    if "<Video 1>" not in out and "integrated_multimodal_description:" not in out:
        out = re.sub(
            r"For the target video,\s*at 0\.00 seconds into the target video,[^.\n]*\.\s*",
            "",
            out,
            flags=re.IGNORECASE,
        )
    out = re.sub(
        r"APPROVED VISUAL REFERENCE FILES[^\n]*\n(?:REFERENCE SLOT BINDINGS:[^\n]*\n)?",
        "",
        out,
        flags=re.IGNORECASE,
    )
    replacements = [
        (r"\b8K reference sheet\b", "natural live-action appearance"),
        (r"\b8K reference image\b", "natural live-action appearance"),
        (r"\bClean character reference portrait and full-body standing design of\b", "Live-action character design of"),
        (r"\bClean animal reference sheet of\b", "Live-action animal design of"),
        (r"\bClean isolated prop reference photo of\b", "Physical farm prop design of"),
        (r"\bWide establishing architectural/environment reference shot of\b", "Real-world outdoor farm environment of"),
        (r",?\s*neutral clean background\b", ""),
        (r",?\s*neutral background\b", ""),
        (r"\bRef_[A-Za-z0-9_]+\.(?:png|jpg|jpeg|webp)\b\s*[—\-:]?\s*", ""),
        (r"\bNo\s+cartoon,\s*no\s+anime[^.\n]*\.?", "Clean full-bleed cinematic camera framing with organic natural textures."),
        (r";?\s*never\s+render\s+character\s+turnaround\s+sheets[^.\n]*\.?", "."),
        (r"\n?<Picture\s+\d+>\s+is\s+the\s+4-panel\s+spatial\s+blocking\s+storyboard\s+diagram[^\n]*", ""),
        (r"\n?<Picture\s+\d+>\s+\(spatial\s+blocking\s+storyboard[^\n]*", ""),
        (r"Following\s+the\s+(?:4-panel\s+)?spatial\s+blocking\s+and\s+movement\s+path\s+of\s+<Picture\s+\d+>\s*(?:in\s+the\s+environment\s+of\s*)?", "In the sunlit farm environment of "),
        (r"Following\s+the\s+(?:4-panel\s+)?spatial\s+blocking\s+and\s+movement\s+path\s+of\s+<Picture\s+\d+>,\s*", ""),
        (r"\s*Present\s+in\s+the\s+scene\s+with\s+consistent\s+(?:placement\s+and\s+)?design:\s*[^.\n]+\.?", ""),
    ]
    for pat, rep in replacements:
        out = re.sub(pat, rep, out, flags=re.IGNORECASE)
    out = _rewrite_storyboard_negatives_as_positives(out)
    return out.strip()


def parse_h3_three_tier_director_script(raw_text: str, max_pictures: int = 9) -> Optional[Dict[str, Any]]:
    """
    Nhận diện & bóc tách Cẩm Nang Cấu Trúc 3 Tầng Chuẩn MiniMax H3 (Bản Tiếng Trung / Song Ngữ EN-ZH / Template):
      - Tầng 1: `全局总控:` (Global Master Control: Style + Aspect Ratio + Core Subject + Master Color Grading + Camera Pacing + Outro)
      - Tầng 2: `逐镜提示词:` (`镜1(0-8s): ...` hoặc `Shot 1 (0–8s) | ... EN: ... ZH: ...`) theo Quy tắc 2 Câu/Shot
      - Tầng 3: `负面提示词:` (Negative Prompt — tự động tách khỏi luồng dương tính CFG=1.0 và chuyển thành Positive Quality Lock)
    """
    text = (raw_text or "").strip()
    if not text:
        return None

    has_tier_markers = any(
        k in text
        for k in ("全局总控", "逐镜提示词", "负面提示词")
    ) or bool(re.search(r"(?:^|\n)\s*(?:镜\s*\d+|Shot\s*\d+)\s*[\(（]\s*\d+(?:\.\d+)?\s*[\-–—~]\s*\d+(?:\.\d+)?\s*s?\s*[\)）]", text, flags=re.IGNORECASE))

    if not has_tier_markers:
        return None

    # 1. Tách Tầng 3: 负面提示词 (Negative Prompt) để KHÔNG bị lọt vào positive conditioning (CFG=1.0)
    neg_prompt = ""
    main_body = text
    neg_m = re.search(r"(?:^|\n)\s*(?:负面提示词|Negative\s*Prompt)\s*[:：]\s*([\s\S]+)$", main_body, flags=re.IGNORECASE)
    if neg_m:
        neg_prompt = neg_m.group(1).strip()
        main_body = main_body[:neg_m.start()].strip()

    # 2. Tách Tầng 1: 全局总控 (Global Master Control)
    global_master = ""
    gm_m = re.search(
        r"(?:全局总控|Global\s*Master\s*Control)\s*[:：]\s*([\s\S]*?)(?=(?:\n\s*(?:逐镜提示词|Shot-by-Shot|镜\s*1\b|Shot\s*1\b))|$)",
        main_body,
        flags=re.IGNORECASE,
    )
    if gm_m:
        global_master = " ".join(gm_m.group(1).strip().split())

    # 3. Tách Tầng 2: 逐镜提示词 (Danh sách các Shot có mốc thời gian 镜1(0-8s) hoặc Shot 1 (0–8s))
    shots_parsed: List[Dict[str, Any]] = []
    # Kiểm tra định dạng Song Ngữ Phần 5: Shot 1 (0–8s) | Tiêu đề \n EN: ... \n ZH: ...
    bilingual_blocks = list(
        re.finditer(
            r"(?:^|\n)\s*(?:Shot|镜)\s*(\d+)\s*[\(（]\s*(\d+(?:\.\d+)?)\s*[\-–—~]\s*(\d+(?:\.\d+)?)\s*s?\s*[\)）]\s*(?:\|\s*([^\n]+))?\n([\s\S]*?)(?=(?:\n\s*(?:Shot|镜)\s*\d+\s*[\(（])|$)",
            main_body,
            flags=re.IGNORECASE,
        )
    )
    if bilingual_blocks:
        for m in bilingual_blocks:
            s_idx = int(m.group(1))
            t_start = float(m.group(2))
            t_end = float(m.group(3))
            dur = max(2.0, min(15.0, round(t_end - t_start, 2)))
            note_vi = (m.group(4) or f"Shot {s_idx} ({t_start:g}-{t_end:g}s)").strip()
            block_body = m.group(5).strip()
            en_m = re.search(r"(?:^|\n)\s*EN\s*[:：]\s*([\s\S]*?)(?=(?:\n\s*ZH\s*[:：])|$)", block_body, flags=re.IGNORECASE)
            zh_m = re.search(r"(?:^|\n)\s*ZH\s*[:：]\s*([\s\S]*?)$", block_body, flags=re.IGNORECASE)
            en_txt = " ".join(en_m.group(1).strip().split()) if en_m else ""
            zh_txt = " ".join(zh_m.group(1).strip().split()) if zh_m else ""
            chosen_txt = en_txt or zh_txt or " ".join(block_body.split())
            shots_parsed.append({
                "shot_number": s_idx,
                "start_sec": t_start,
                "end_sec": t_end,
                "seconds": int(round(dur)) if dur >= 4 else 4,
                "note_vi": note_vi,
                "text_en": en_txt,
                "text_zh": zh_txt,
                "text": chosen_txt,
            })
    else:
        # Định dạng Phần 1 & Phần 4: 镜1(0-8s): ...
        inline_blocks = list(
            re.finditer(
                r"(?:^|\n)\s*(?:镜|Shot\s*)(\d+)\s*[\(（]\s*(\d+(?:\.\d+)?)\s*[\-–—~]\s*(\d+(?:\.\d+)?)\s*s?\s*[\)）]\s*[:：]\s*([^\n]+)",
                main_body,
                flags=re.IGNORECASE,
            )
        )
        for m in inline_blocks:
            s_idx = int(m.group(1))
            t_start = float(m.group(2))
            t_end = float(m.group(3))
            dur = max(2.0, min(15.0, round(t_end - t_start, 2)))
            s_txt = m.group(4).strip()
            shots_parsed.append({
                "shot_number": s_idx,
                "start_sec": t_start,
                "end_sec": t_end,
                "seconds": int(round(dur)) if dur >= 4 else 4,
                "note_vi": f"镜{s_idx} ({t_start:g}–{t_end:g}s)",
                "text_en": "",
                "text_zh": s_txt,
                "text": s_txt,
            })

    if not global_master and not shots_parsed:
        return None

    return {
        "global_master": global_master,
        "shots": shots_parsed,
        "negative_prompt": neg_prompt,
        "positive_quality_lock": (
            "Pristine full-bleed cinematic footage, natural soft light roll-off, rich material reflections, "
            "accurate character anatomy and fluid dynamic posture, seamless cloud and trail continuity, sharp 8K focus."
        ),
    }


def auto_upgrade_raw_prompt_to_h3_r2v(prompt: str, image_paths: Optional[List[Any]] = None) -> str:
    """
    Tự động nâng cấp câu lệnh thô sang cấu trúc chuẩn MiniMax H3 Full-Reference.
    QUAN TRỌNG: Nếu câu lệnh đã có `<Video 1>`, `integrated_multimodal_description:`, hoặc `subject_definitions:`,
    giữ nguyên 100% nội dung gốc của người dùng (chỉ đảm bảo có `<Picture 1>` nếu có ảnh tham chiếu)!
    """
    if not prompt or not prompt.strip():
        return ""
    raw_in = re.sub(r",\s*(?:16:9|9:16|1:1)\s*$", "", prompt.strip())
    imgs = [Path(str(p)) for p in (image_paths or []) if str(p).strip()]
    for idx_im in range(min(len(imgs), 9)):
        raw_in = re.sub(
            rf"\bimage_{idx_im}\.(?:png|jpg|jpeg|webp)\b",
            f"<Picture {idx_im + 1}>",
            raw_in,
            flags=re.IGNORECASE,
        )

    def _prepend_missing_picture_tags(txt: str) -> str:
        if not imgs:
            return txt
        missing = [f"<Picture {i}>" for i in range(1, min(len(imgs), 9) + 1) if f"<Picture {i}>" not in txt]
        if missing:
            tags_str = ", ".join(missing)
            verb = "are" if len(missing) > 1 else "is"
            return f"For the target video, at 0.00 seconds into the target video, {tags_str} {verb} referenced.\n\n{txt}"
        return txt

    # Nếu câu lệnh đã có <Video 1> hoặc integrated_multimodal_description: (chuẩn native MiniMax H3), giữ nguyên 100%!
    if "<Video 1>" in raw_in or "integrated_multimodal_description:" in raw_in:
        return _prepend_missing_picture_tags(raw_in)

    cleaned = sanitize_h3_prompt_no_ref_sheet_bleed(raw_in)
    if not cleaned:
        return ""
    # Nếu đã có đủ cấu trúc chuẩn 6 mục của MiniMax H3 R2V thì giữ nguyên
    if "subject_definitions:" in cleaned and "detailed_description:" in cleaned:
        return cleaned
    # Nếu người dùng dán định dạng 'H3 VIDEO PROMPT | ...' từ bộ file .txt
    if "H3 VIDEO PROMPT |" in prompt or "SETTING / STORY BEAT:" in prompt:
        try:
            parsed = parse_blocking_storyboard_html(prompt)
            if parsed.get("scenes"):
                return parsed["scenes"][0].get("h3_prompt") or cleaned
        except Exception:
            pass

    # Kiểm tra xem người dùng có dán Cẩm Nang 3 Tầng H3 (全局总控 + 逐镜提示词 + 负面提示词) không
    three_tier = parse_h3_three_tier_director_script(prompt)

    if not imgs and not three_tier:
        return cleaned

    subj_defs = []
    ret_lines = []
    bindings = []
    for idx, img_p in enumerate(imgs[:9], start=1):
        info = classify_ref_asset(img_p.name, idx - 1)
        disp = info.get("display_name") or clean_ref_display_name(img_p.name)
        short_n = info.get("short_name") or short_ref_token(img_p.name)
        cat = info.get("category", "character")
        pic_t = f"<Picture {idx}>"
        subj_t = f"<Subject {idx}>"
        bindings.append({"disp": disp, "short": short_n, "subj": subj_t, "pic": pic_t, "cat": cat})
        subj_desc = {
            "character": f"{subj_t} is the character in {pic_t}, preserving exact facial features, hairstyle, and outfit.",
            "animal": f"{subj_t} is the animal/creature in {pic_t}, standing on grounded legs with natural markings and anatomy.",
            "prop": f"{subj_t} is the physical prop in {pic_t}, featuring authentic geometry and material textures.",
            "location": f"{subj_t} is the environment in {pic_t}, featuring authentic architecture, ground plane, and lighting.",
        }.get(cat, f"{subj_t} is in {pic_t}.")
        subj_defs.append(subj_desc)
        ret_lines.append(f"{subj_t} (appears in [Shot 1]): fully_preserved - Visual identity and design from {pic_t} are retained.")

    if three_tier:
        gm_txt = three_tier.get("global_master") or "High-end cinematic visual aesthetic, consistent character identity, smooth natural motion."
        t_shots = three_tier.get("shots") or []
        if not subj_defs:
            subj_defs = ["<Subject 1> is the primary character in <Picture 1>."]
            ret_lines = ["<Subject 1> (appears in [Shot 1]): fully_preserved - Character identity and outfit are retained."]
        shot_lines = []
        for idx, sh in enumerate(t_shots, start=1):
            s_txt = sh.get("text") or ""
            if len(bindings) >= 1 and "<Subject " not in s_txt and "<Picture " not in s_txt:
                if "黑衣剑客" in s_txt or "black-robed swordsman" in s_txt.lower():
                    s_txt = s_txt.replace("黑衣剑客", "黑衣剑客 (<Subject 1>)").replace("black-robed swordsman", "black-robed swordsman (<Subject 1>)")
                elif idx == 1:
                    s_txt = f"(<Subject 1>) {s_txt}"
            if len(bindings) >= 2:
                if "红衣女剑客" in s_txt or "red-robed" in s_txt.lower() or "female swordsman" in s_txt.lower():
                    s_txt = s_txt.replace("红衣女剑客", "红衣女剑客 (<Subject 2>)").replace("female swordsman", "female swordsman (<Subject 2>)")
            shot_lines.append(f"[Shot {idx}] ({sh['start_sec']:g}-{sh['end_sec']:g}s) {s_txt}")
        detailed_body = "\n\n".join(shot_lines) if shot_lines else f"[Shot 1] {gm_txt}"
        return (
            "subject_definitions:\n" + "\n".join(subj_defs) + "\n\n"
            "summary:\n[reference generation] " + gm_txt + "\n\n"
            "retention_analysis:\n" + "\n".join(ret_lines) + "\n\n"
            "detailed_description:\n"
            f"The target video is in a {gm_txt}\n"
            f"{detailed_body}\n"
            f"{three_tier['positive_quality_lock']}\n\n"
            "overall_soundscape:\nHigh-speed wind rushing past clouds, sword energy resonance, and cinematic atmospheric acoustics.\n\n"
            "non_diegetic_music:\nEpic orchestral Xianxia / cinematic score synchronized with the camera pacing."
        )

    is_farm_storyboard = any(k in cleaned.lower() for k in ("daisy", "clover", "bramble", "pasture", "hazel"))
    if not is_farm_storyboard:
        # Với câu lệnh tự do của người dùng, giữ nguyên 100% văn phong & nội dung, chỉ đảm bảo gắn đủ <Picture 1..N>
        return _prepend_missing_picture_tags(cleaned)

    body = cleaned
    for b in bindings:
        body = re.sub(rf"{re.escape(b['pic'])}", b["subj"], body, flags=re.IGNORECASE)
        if len(b["disp"]) >= 3 and re.search(rf"\b{re.escape(b['disp'])}\b(?!\s*\(<Subject)", body, flags=re.IGNORECASE):
            body = re.sub(rf"\b({re.escape(b['disp'])})\b(?!\s*\(<Subject)", rf"\1 ({b['subj']})", body, count=1, flags=re.IGNORECASE)
        elif len(b["short"]) >= 3 and re.search(rf"\b{re.escape(b['short'])}\b(?!\s*\(<Subject)", body, flags=re.IGNORECASE):
            body = re.sub(rf"\b({re.escape(b['short'])})\b(?!\s*\(<Subject)", rf"\1 ({b['short_subj'] if 'short_subj' in b else b['subj']})", body, count=1, flags=re.IGNORECASE)

    shot_prefix = "" if "[Shot 1]" in body else "[Shot 1] "
    first_sentence = body.splitlines()[0][:220]
    return (
        "subject_definitions:\n" + "\n".join(subj_defs) + "\n\n"
        "summary:\n[reference generation] The target video shows " + first_sentence + "\n\n"
        "retention_analysis:\n" + "\n".join(ret_lines) + "\n\n"
        "detailed_description:\n"
        f"The target video is in a {DEFAULT_VISUAL_STYLE}\n"
        f"{shot_prefix}{body}\n\n"
        "overall_soundscape:\nNatural ambient environment audio, realistic physical foley, and clear lip-synced dialogue.\n\n"
        "non_diegetic_music:\nSoft cinematic background score that remains subtle beneath the scene audio."
    )


def compile_blocking_scene_to_h3(
    scene: Dict[str, Any],
    ref_lookup: Optional[Dict[str, Dict[str, Any]]] = None,
    visual_style: str = "",
    global_prompt_addon: str = "",
) -> Dict[str, Any]:
    """
    Biên dịch 1 phân cảnh Storyboard Nhận Diện (E01Sxxx) sang cấu trúc chuẩn chính thức
    MiniMax H3 Full-Reference Mode (`VIDEO_PROMPT_WRITING_GUIDE_ref_en.md` & ComfyUI H3 Prompt Guide):
    - `subject_definitions:`: Định nghĩa `<Subject k> is the ... in <Picture k>` (chỉ trích dẫn `<Picture k>` tại đây;
      bên trong `[Shot 1]` và `[Shot 2]` sử dụng `<Subject k>` để mô hình hiểu đây là thực thể sống chứ KHÔNG PHẢI frame tĩnh!).
    - `summary:`: Mở đầu bằng tiền tố chuẩn `[reference generation]` và tóm tắt cảnh qua `<Subject k>`.
    - `retention_analysis:`: Liệt kê từng `<Subject k> (appears in [Shot 1], [Shot 2]): fully_preserved - ...`.
    - `detailed_description:`: Mở đầu bằng câu thiết lập phong cách hình ảnh (Style sentence) trước `[Shot 1]`,
      dùng `<Subject k> (S1) says, <d>[English] ...</d>` cho lời thoại, hoàn toàn dương tính (không chứa mệnh đề phủ định 'No/Never' vì CFG=1).
    """
    ref_lookup = ref_lookup or {}
    raw_refs = list(dict.fromkeys(scene.get("references") or []))
    scene_id = str(scene.get("scene_id") or "E01S001").strip()

    scene_style = _strip_negative_style_clauses(str(scene.get("scene_style") or "").strip())
    master_style = _strip_negative_style_clauses(str(visual_style or DEFAULT_VISUAL_STYLE).strip())
    if scene_style and scene_style.lower() != master_style.lower():
        eff_style = f"{master_style} | Scene Style: {scene_style}"
    else:
        eff_style = scene_style or master_style

    enriched_refs = []
    for idx, rf in enumerate(raw_refs):
        info = dict(ref_lookup.get(rf) or classify_ref_asset(rf, idx, visual_style=eff_style))
        lp = find_local_ref_image(info["filename"])
        if lp and lp.is_file():
            clean_lp = prepare_clean_h3_ref_image(lp, category=info.get("category", ""))
            info["has_image"] = True
            info["local_path"] = str(clean_lp).replace("\\", "/")
            info["raw_local_path"] = str(lp).replace("\\", "/")
            info["image_url"] = f"/api/storyboard/ref-image?name={urllib.parse.quote(info['filename'])}&t={int(lp.stat().st_mtime)}"
        enriched_refs.append(info)

    # Phân bổ thông minh 9 ô tham chiếu <Picture 1..9> chuẩn MiniMax H3 Ref2VA:
    # - TUYỆT ĐỐI KHÔNG đưa ảnh sơ đồ 2D `Storyboard_E01Sxxx.png` (có khung PANEL 1..4, chữ tiêu đề WILLOW CREEK FARM và hình vẽ que)
    #   vào `reference_images` của MiniMax H3, vì ReferenceVAE sẽ copy nguyên khung giao diện 4 ô và chữ vào video!
    #   (Ảnh `Storyboard_E01Sxxx.png` chỉ dùng để hiển thị xem trước trên giao diện Web UI; toàn bộ hướng di chuyển đã nằm trong lời văn `[Shot 1]`, `[Shot 2]`).
    # - Dành 8 slot đầu (<Picture 1>..<Picture 8>) cho ảnh thực thể sạch `clean_h3/Ref_*.png` và giữ trống Slot 9 (<Picture 9>) cho `LastFrame` cảnh trước:
    #   + Khi cảnh có <= 8 ảnh Ref_*.png (55/56 cảnh): P1..P8 là ảnh đơn 100% độc lập, P9 dành cho LastFrame!
    #   + Khi cảnh có >= 9 ảnh Ref_*.png (ví dụ E01S002 có 9 Ref): ưu tiên các thực thể được nhắc tên trực tiếp trong kịch bản cảnh đó vào P1..P7 (ảnh đơn),
    #     gộp các đạo cụ phụ còn lại vào P8 (`Composite_Refs_E01Sxxx.png` trên nền trắng đồng nhất không viền), và giữ trống P9 cho LastFrame!
    scene_script_text = " ".join(
        str(scene.get(k) or "") for k in ("action_timing", "movement", "camera", "dialogue", "extra_prompt")
    ).lower()

    def _is_mentioned_in_scene(r_item: Dict[str, Any]) -> bool:
        if r_item.get("category") in ("character", "animal", "location"):
            return True
        disp_low = str(r_item.get("display_name") or "").lower().strip()
        short_low = str(r_item.get("short_name") or "").lower().strip()
        if disp_low and disp_low in scene_script_text:
            return True
        if short_low and len(short_low) >= 3 and re.search(rf"\b{re.escape(short_low)}\b", scene_script_text):
            return True
        return False

    has_overflow = len(enriched_refs) > 8
    if has_overflow:
        cat_priority = {"character": 0, "animal": 1, "location": 2, "prop": 3}
        enriched_refs.sort(
            key=lambda x: (
                0 if _is_mentioned_in_scene(x) else 1,
                cat_priority.get(x.get("category", "prop"), 3),
            )
        )

    direct_refs = enriched_refs[:7] if has_overflow else enriched_refs[:8]
    overflow_refs = enriched_refs[7:] if has_overflow else []

    picture_bindings = []
    all_ref_bindings = []
    subj_defs = []
    retention_lines = []

    for p_idx, r_info in enumerate(direct_refs, start=1):
        pic_tag = f"<Picture {p_idx}>"
        subj_tag = f"<Subject {p_idx}>"
        disp = r_info.get("display_name") or clean_ref_display_name(r_info["filename"])
        short_n = r_info.get("short_name") or short_ref_token(r_info["filename"])
        cat = r_info.get("category", "character")
        ent_prompt = str(r_info.get("custom_prompt") or r_info.get("auto_prompt") or "").strip()
        b_item = {
            "picture_tag": pic_tag,
            "subject_tag": subj_tag,
            "picture_index": p_idx,
            "subject_index": p_idx,
            "stt": r_info.get("stt", p_idx),
            "filename": r_info["filename"],
            "display_name": disp,
            "short_name": short_n,
            "category": cat,
            "category_vi": r_info.get("category_vi", "👤 Nhân vật"),
            "color_hex": r_info.get("color_hex", "#4ecdc4"),
            "has_image": bool(r_info.get("has_image")),
            "local_path": r_info.get("local_path", ""),
            "raw_local_path": r_info.get("raw_local_path", r_info.get("local_path", "")),
            "image_url": r_info.get("image_url") or f"/api/storyboard/ref-image?name={urllib.parse.quote(r_info['filename'])}",
            "auto_prompt": ent_prompt,
        }
        picture_bindings.append(b_item)
        all_ref_bindings.append(b_item)
        subj_desc = {
            "character": f"{subj_tag} is the live-action human character {disp} in {pic_tag}, with natural facial features, hairstyle, and everyday farm attire.",
            "animal": f"{subj_tag} is the live farm animal {disp} in {pic_tag}, standing on grounded legs with natural coat markings and anatomy.",
            "prop": f"{subj_tag} is the physical farm prop {disp} in {pic_tag}, featuring authentic hardware geometry and material textures.",
            "location": f"{subj_tag} is the real-world farm environment {disp} in {pic_tag}, featuring authentic wooden fences, ground path, and natural daylight layout.",
        }.get(cat, f"{subj_tag} is {disp} in {pic_tag}.")
        subj_defs.append(subj_desc)

    if overflow_refs:
        comp_idx = len(picture_bindings) + 1
        comp_pic_tag = f"<Picture {comp_idx}>"
        first_ov_subj = len(all_ref_bindings) + 1
        last_ov_subj = first_ov_subj + len(overflow_refs) - 1
        comp_subj_range = f"<Subject {first_ov_subj}>..<Subject {last_ov_subj}>" if len(overflow_refs) > 1 else f"<Subject {first_ov_subj}>"
        comp_fname = f"Composite_Refs_{scene_id}.png"
        comp_path = build_overflow_composite_ref_image(scene_id, overflow_refs)
        ov_names = ", ".join(r.get("display_name") or r["filename"] for r in overflow_refs)
        picture_bindings.append({
            "picture_tag": comp_pic_tag,
            "subject_tag": comp_subj_range,
            "picture_index": comp_idx,
            "stt": comp_idx,
            "filename": comp_fname,
            "display_name": f"Bảng gộp {len(overflow_refs)} Ref ({ov_names[:36]})",
            "short_name": "CompositeRefs",
            "category": "prop",
            "category_vi": f"🧩 Gộp {len(overflow_refs)} Ref ({comp_subj_range})",
            "color_hex": "#4ecdc4",
            "has_image": True,
            "local_path": str(comp_path).replace("\\", "/"),
            "image_url": f"/api/storyboard/ref-image?name={urllib.parse.quote(comp_fname)}",
            "auto_prompt": f"Multi-subject reference grid ({comp_subj_range}): {ov_names}",
            "is_composite": True,
            "sub_refs": [r["filename"] for r in overflow_refs],
            "overflow_refs": [r["filename"] for r in overflow_refs],
        })
        total_ov = len(overflow_refs)
        for ov_i, r_info in enumerate(overflow_refs):
            subj_idx = len(all_ref_bindings) + 1
            ov_subj_tag = f"<Subject {subj_idx}>"
            pos_phrase = _grid_position_phrase(ov_i, total_ov)
            disp = r_info.get("display_name") or clean_ref_display_name(r_info["filename"])
            short_n = r_info.get("short_name") or short_ref_token(r_info["filename"])
            cat = r_info.get("category", "prop")
            ent_prompt = str(r_info.get("custom_prompt") or r_info.get("auto_prompt") or "").strip()
            all_ref_bindings.append({
                "picture_tag": comp_pic_tag,
                "subject_tag": ov_subj_tag,
                "picture_index": comp_idx,
                "subject_index": subj_idx,
                "grid_position": pos_phrase,
                "stt": r_info.get("stt", subj_idx),
                "filename": r_info["filename"],
                "display_name": disp,
                "short_name": short_n,
                "category": cat,
                "category_vi": r_info.get("category_vi", "🧰 Đạo cụ"),
                "color_hex": r_info.get("color_hex", "#4ecdc4"),
                "has_image": bool(r_info.get("has_image")),
                "local_path": r_info.get("local_path", ""),
                "image_url": r_info.get("image_url") or f"/api/storyboard/ref-image?name={urllib.parse.quote(r_info['filename'])}",
                "auto_prompt": ent_prompt,
            })
            ov_subj_desc = {
                "character": f"{ov_subj_tag} is the live-action human character {disp} {pos_phrase} {comp_pic_tag}, with natural facial features, hairstyle, and everyday farm attire.",
                "animal": f"{ov_subj_tag} is the live farm animal {disp} {pos_phrase} {comp_pic_tag}, standing on grounded legs with natural coat markings and anatomy.",
                "prop": f"{ov_subj_tag} is the physical farm prop {disp} {pos_phrase} {comp_pic_tag}, featuring authentic hardware geometry and material textures.",
                "location": f"{ov_subj_tag} is the real-world farm environment {disp} {pos_phrase} {comp_pic_tag}, featuring authentic wooden fences, ground path, and natural daylight layout.",
            }.get(cat, f"{ov_subj_tag} is {disp} {pos_phrase} {comp_pic_tag}.")
            subj_defs.append(ov_subj_desc)

    # Vẫn đảm bảo tạo file ảnh sơ đồ Storyboard_E01Sxxx.png trên ổ đĩa để giao diện Web UI hiển thị xem trước
    sb_img_name = Path(str(scene.get("storyboard_img") or f"Storyboard_{scene_id}.png")).name
    sb_img_path = find_local_ref_image(sb_img_name) or (STORYBOARD_DIR / sb_img_name)
    if not sb_img_path.is_file():
        try:
            render_schematic_storyboard_image(scene, ref_lookup=ref_lookup, force=False)
        except Exception:
            pass

    def _inject_subject_tags(txt: str) -> str:
        """
        Theo chuẩn chính thức VIDEO_PROMPT_WRITING_GUIDE_ref_en.md (Mục 2.1, 3, 5.3):
        Bên trong summary và detailed_description ([Shot 1], [Shot 2]), phải gọi tên thực thể qua <Subject k>
        (thay vì <Picture k>) để H3 trích xuất thực thể sống và diễn hoạt mượt mà trong cảnh!
        """
        if not txt:
            return ""
        out = txt
        for b in all_ref_bindings:
            disp_n = b["display_name"]
            short_n = b["short_name"]
            subj_t = b.get("subject_tag") or f"<Subject {b['picture_index']}>"
            if len(disp_n) >= 3 and re.search(rf"\b{re.escape(disp_n)}\b(?!\s*\(<Subject)", out, flags=re.IGNORECASE):
                out = re.sub(rf"\b({re.escape(disp_n)})\b(?!\s*\(<Subject)", rf"\1 ({subj_t})", out, count=1, flags=re.IGNORECASE)
            elif len(short_n) >= 3 and re.search(rf"\b{re.escape(short_n)}\b(?!\s*\(<Subject)", out, flags=re.IGNORECASE):
                out = re.sub(rf"\b({re.escape(short_n)})\b(?!\s*\(<Subject)", rf"\1 ({subj_t})", out, count=1, flags=re.IGNORECASE)
        return out

    act_raw = _rewrite_storyboard_negatives_as_positives((scene.get("action_timing") or "").strip())
    mov_raw = _rewrite_storyboard_negatives_as_positives((scene.get("movement") or "").strip())
    cam_raw = _rewrite_storyboard_negatives_as_positives((scene.get("camera") or "").strip())
    extra_text = _inject_subject_tags(_rewrite_storyboard_negatives_as_positives(str(scene.get("extra_prompt") or "").strip()))
    addon_text = _inject_subject_tags(_rewrite_storyboard_negatives_as_positives(str(global_prompt_addon or "").strip()))

    # Tách Shot 1 (0–7.5s) và Shot 2 (7.5–15s) từ action_timing và camera để tạo khối [Shot 1] và [Shot 2] chuẩn Ref2VA
    shot1_act, shot2_act = "", ""
    for ln in act_raw.splitlines():
        ln_s = ln.strip()
        if not ln_s:
            continue
        m_s1 = re.match(r"^(?:00[.:]00\s*[\-–—]\s*07[.:]50s?\s*:\s*)?(?:Shot\s*1\s*:\s*)(.+)$", ln_s, flags=re.IGNORECASE)
        m_s2 = re.match(r"^(?:07[.:]50\s*[\-–—]\s*15[.:]00s?\s*:\s*)?(?:Shot\s*2\s*:\s*)(.+)$", ln_s, flags=re.IGNORECASE)
        if m_s1:
            shot1_act = m_s1.group(1).strip()
        elif m_s2:
            shot2_act = m_s2.group(1).strip()
        elif not shot1_act:
            shot1_act = ln_s
        else:
            shot2_act = (shot2_act + " " + ln_s).strip()

    shot_a_cam, shot_b_cam = cam_raw, ""
    m_cam_split = re.split(r"\bShot\s*B\s*(?:\([^)]*\))?\s*:\s*", cam_raw, maxsplit=1, flags=re.IGNORECASE)
    if len(m_cam_split) == 2:
        shot_a_cam = re.sub(r"^Shot\s*A\s*(?:\([^)]*\))?\s*:\s*", "", m_cam_split[0], flags=re.IGNORECASE).strip()
        shot_b_cam = m_cam_split[1].strip()
    else:
        shot_a_cam = re.sub(r"^Shot\s*A\s*(?:\([^)]*\))?\s*:\s*", "", cam_raw, flags=re.IGNORECASE).strip()

    # Tách movement thành phần đầu cho Shot 1 và phần sau cho Shot 2 để mô tả hành động liên tục mượt mà
    mov_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", mov_raw) if s.strip()]
    if len(mov_sentences) >= 2 and shot2_act:
        mid_m = max(1, (len(mov_sentences) + 1) // 2)
        mov_part1 = " ".join(mov_sentences[:mid_m])
        mov_part2 = " ".join(mov_sentences[mid_m:])
    else:
        mov_part1 = mov_raw
        mov_part2 = ""

    # Format dialogue lines into spoken <Subject k> (S1) says, <d>[English] ...</d> per official R2V guide Section 5.6
    dlg_lines = scene.get("dialogue_lines") or [
        ln.strip() for ln in (scene.get("dialogue") or "").splitlines() if ln.strip()
    ]
    parsed_dlg = []
    speaker_id_map: Dict[str, str] = {}
    for d_ln in dlg_lines:
        m = re.match(r'^([^:]+):\s*["“]?(.+?)["”]?\s*$', d_ln)
        if m:
            speaker = m.group(1).strip()
            spoken = " ".join(m.group(2).strip().split())
            sp_key = speaker.lower()
            if sp_key not in speaker_id_map:
                speaker_id_map[sp_key] = f"S{len(speaker_id_map) + 1}"
            sp_code = speaker_id_map[sp_key]
            speaker_tag = f"{speaker} ({sp_code})"
            for b in all_ref_bindings:
                if b["short_name"].lower() == sp_key or sp_key in b["display_name"].lower():
                    subj_t = b.get("subject_tag") or f"<Subject {b['picture_index']}>"
                    speaker_tag = f"{subj_t} ({sp_code})"
                    break
            lang_prefix = "" if spoken.startswith("[") else "[English] "
            parsed_dlg.append(f"{speaker_tag} says, <d>{lang_prefix}{spoken}</d>")
        else:
            spoken = " ".join(d_ln.strip().split())
            if spoken:
                lang_prefix = "" if spoken.startswith("[") else "[English] "
                parsed_dlg.append(f"<d>{lang_prefix}{spoken}</d>")

    dlg_shot1, dlg_shot2 = "", ""
    if parsed_dlg:
        if shot2_act or shot_b_cam:
            split_d = max(1, (len(parsed_dlg) + 1) // 2)
            dlg_shot1 = " ".join(parsed_dlg[:split_d])
            dlg_shot2 = " ".join(parsed_dlg[split_d:])
        else:
            dlg_shot1 = " ".join(parsed_dlg)

    loc_bindings = [b for b in all_ref_bindings if b.get("category") == "location"]
    loc_intro = (
        f"In the sunlit farm environment of {loc_bindings[0]['display_name']} ({loc_bindings[0].get('subject_tag', '<Subject 1>')}), "
        if loc_bindings else "In the sunlit outdoor Willow Creek Farm environment, "
    )

    s1_raw = f"{loc_intro}{shot_a_cam} {shot1_act} {mov_part1}".strip()
    s1_desc = _inject_subject_tags(s1_raw)
    if dlg_shot1:
        s1_desc = f"{s1_desc} {dlg_shot1}".strip()

    s2_blocks = []
    if shot2_act or shot_b_cam or mov_part2 or dlg_shot2:
        cam_b_clean = shot_b_cam
        if cam_b_clean:
            cam_b_clean = re.sub(r"^a\s+matched\s+cut\s+to\s+", "", cam_b_clean, flags=re.IGNORECASE).strip()
        s2_core = " ".join(p for p in (cam_b_clean, shot2_act, mov_part2) if p).strip()
        s2_desc = _inject_subject_tags(s2_core)
        if dlg_shot2:
            s2_desc = f"{s2_desc} {dlg_shot2}".strip()
        if not s2_desc.lower().startswith(("the camera cuts to", "the shot cuts to", "the shot transitions to")):
            s2_desc = f"the camera cuts to {s2_desc}"
        s2_blocks.append(f"[Shot 2] At 00:07.500, {s2_desc}")

    if extra_text:
        s1_desc = f"{s1_desc} {extra_text}".strip()
    if addon_text:
        s1_desc = f"{s1_desc} {addon_text}".strip()

    shots_present_label = "[Shot 1], [Shot 2]" if s2_blocks else "[Shot 1]"
    for b in all_ref_bindings:
        subj_t = b.get("subject_tag") or f"<Subject {b['picture_index']}>"
        disp = b["display_name"]
        cat = b.get("category", "character")
        ret_detail = {
            "character": f"{disp}'s facial features, hairstyle, proportions, and farm outfit are retained.",
            "animal": f"{disp}'s species anatomy, coat markings, and grounded four-legged posture are retained.",
            "prop": f"{disp}'s physical structure, weathered wood/metal hardware, and scale are retained.",
            "location": f"{disp}'s spatial layout, fence architecture, ground path, and natural lighting are retained.",
        }.get(cat, f"{disp}'s visual appearance and design are retained.")
        retention_lines.append(f"{subj_t} (appears in {shots_present_label}): fully_preserved - {ret_detail}")

    title_str = scene.get("title") or scene_id
    act_summary = _inject_subject_tags(act_raw.replace("\n", " "))

    subject_definitions_block = "\n".join(subj_defs) if subj_defs else "<Subject 1> is the main scene subject in <Picture 1>."
    summary_block = (
        f"[reference generation] The target video shows {title_str}: {act_summary}"
    ).strip()
    retention_block = "\n".join(retention_lines) if retention_lines else "<Subject 1> (appears in [Shot 1]): fully_preserved"
    style_opening_sentence = f"The target video is in a {eff_style}"
    if not style_opening_sentence.endswith("."):
        style_opening_sentence += "."
    detailed_block = (
        f"{style_opening_sentence}\n"
        f"[Shot 1] {s1_desc}"
        + ("\n\n" + "\n\n".join(s2_blocks) if s2_blocks else "")
    )
    soundscape_block = (
        "Natural outdoor farm ambience, gentle wind through pasture grass, grounded footsteps and animal hooves on dirt, "
        "authentic wooden gate and metal latch foley, and clear lip-synced English dialogue."
    )
    music_block = "Soft, restrained acoustic guitar and warm pastoral strings that stay subtle beneath the dialogue and natural farm foley."

    compiled_prompt = (
        f"subject_definitions:\n{subject_definitions_block}\n\n"
        f"summary:\n{summary_block}\n\n"
        f"retention_analysis:\n{retention_block}\n\n"
        f"detailed_description:\n{detailed_block}\n\n"
        f"overall_soundscape:\n{soundscape_block}\n\n"
        f"non_diegetic_music:\n{music_block}"
    )

    custom_h3 = str(scene.get("custom_h3_prompt") or "").strip()
    if custom_h3:
        cleaned_custom = sanitize_h3_prompt_no_ref_sheet_bleed(custom_h3)
        if cleaned_custom and "H3 VIDEO PROMPT | Willow Creek Farm" not in cleaned_custom:
            compiled_prompt = cleaned_custom

    return {
        "prompt": compiled_prompt,
        "visual_style": eff_style,
        "visual_style_lock": eff_style,
        "picture_bindings": picture_bindings,
        "all_ref_bindings": all_ref_bindings,
    }


def apply_style_and_prompts_to_all_scenes(
    visual_style: str = "",
    global_prompt_addon: str = "",
    bulk_scene_prompts: str = "",
    reset_custom_h3: bool = True,
) -> Dict[str, Any]:
    """
    Đồng bộ Phong Cách Hình Ảnh (Master Visual Style) + Prompt Bổ Sung Toàn Tập + Danh Sách Prompt Theo Từng Cảnh
    vào TẤT CẢ các phân cảnh (E01S001..E01S056) và TẤT CẢ thực thể Ref_*.png.
    """
    sb = load_cached_storyboard()
    eff_style = (visual_style or sb.get("visual_style") or DEFAULT_VISUAL_STYLE).strip()
    eff_addon = (global_prompt_addon if global_prompt_addon is not None else sb.get("global_prompt_addon") or "").strip()

    sb["visual_style"] = eff_style
    sb["global_prompt_addon"] = eff_addon

    # 1. Cập nhật phong cách cho toàn bộ thực thể Ref_*.png
    ref_lookup: Dict[str, Dict[str, Any]] = {}
    for idx, r in enumerate(sb.get("ref_registry", [])):
        r["stt"] = idx + 1
        disp = r.get("display_name") or clean_ref_display_name(r["filename"])
        cat = r.get("category") or "character"
        r["auto_prompt"] = build_ref_auto_prompt(disp, cat, visual_style=eff_style)
        if r.get("custom_prompt") and reset_custom_h3:
            # Cập nhật nhãn [Visual Style: ...] trong custom_prompt của Ref nếu có
            r["custom_prompt"] = re.sub(
                r"^\[Visual Style:[^\]]+\]\s*",
                f"[Visual Style: {eff_style}] ",
                str(r["custom_prompt"]),
            )
        ref_lookup[r["filename"]] = r

    # 2. Nếu người dùng dán danh sách Prompt hàng loạt cho các cảnh (bulk_scene_prompts),
    # tự động bóc tách theo mã cảnh (E01S001, Scene 1, Cảnh 1, #1...) hoặc theo thứ tự từng dòng/đoạn
    scenes = sb.get("scenes") or []
    bulk_raw = (bulk_scene_prompts or "").strip()
    if bulk_raw and scenes:
        # Thử tách theo đoạn trống hoặc từng dòng
        blocks = [b.strip() for b in re.split(r"\n\s*\n+", bulk_raw) if b.strip()]
        if len(blocks) == 1 and len(bulk_raw.splitlines()) > 1:
            blocks = [ln.strip() for ln in bulk_raw.splitlines() if ln.strip()]

        scene_by_id = {sc["scene_id"].upper(): sc for sc in scenes}
        unmatched_blocks: List[str] = []

        for blk in blocks:
            # Kiểm tra mã E01S001..E01S999
            m_sid = re.search(r"\b(E\d{2}S\d{3})\b", blk, flags=re.IGNORECASE)
            if m_sid and m_sid.group(1).upper() in scene_by_id:
                target_sc = scene_by_id[m_sid.group(1).upper()]
                clean_p = re.sub(r"^(?:E\d{2}S\d{3})\s*[:\-·\.]*\s*", "", blk, flags=re.IGNORECASE).strip()
                target_sc["extra_prompt"] = clean_p or blk
                if reset_custom_h3:
                    target_sc.pop("custom_h3_prompt", None)
                continue

            # Kiểm tra số thứ tự cảnh: Scene 1, Cảnh 02, Shot 3, #04, 1. ...
            m_num = re.match(
                r"^(?:scene|cảnh|canh|shot|clip| tập|stt|#)\s*0*(\d{1,3})\s*[:\-·\.\)]+\s*(.+)$",
                blk,
                flags=re.IGNORECASE | re.DOTALL,
            ) or re.match(r"^0*(\d{1,3})\s*[:\-\.\)]\s*(.+)$", blk, flags=re.DOTALL)
            if m_num:
                s_idx = int(m_num.group(1))
                p_body = m_num.group(2).strip()
                if 1 <= s_idx <= len(scenes):
                    scenes[s_idx - 1]["extra_prompt"] = p_body
                    if reset_custom_h3:
                        scenes[s_idx - 1].pop("custom_h3_prompt", None)
                    continue

            unmatched_blocks.append(blk)

        # Nếu các dòng/đoạn không ghi số cảnh cụ thể:
        # - Nếu chỉ có 1 đoạn duy nhất -> gắn làm quy tắc prompt bổ sung cho TẤT CẢ các cảnh
        # - Nếu có nhiều đoạn -> gán tuần tự cho Cảnh 1, Cảnh 2, Cảnh 3...
        if len(unmatched_blocks) == 1 and len(blocks) == 1:
            for sc in scenes:
                sc["extra_prompt"] = unmatched_blocks[0]
                if reset_custom_h3:
                    sc.pop("custom_h3_prompt", None)
        elif unmatched_blocks:
            for idx_b, blk in enumerate(unmatched_blocks):
                if idx_b < len(scenes):
                    scenes[idx_b]["extra_prompt"] = blk
                    if reset_custom_h3:
                        scenes[idx_b].pop("custom_h3_prompt", None)

    # 3. Biên dịch lại 100% phân cảnh để mọi cảnh nhận đủ Prompt + Phong Cách
    for sc in scenes:
        sc["scene_style"] = eff_style
        if reset_custom_h3:
            sc.pop("custom_h3_prompt", None)
        comp = compile_blocking_scene_to_h3(
            sc,
            ref_lookup,
            visual_style=eff_style,
            global_prompt_addon=eff_addon,
        )
        sc["visual_style"] = comp["visual_style"]
        sc["h3_prompt"] = comp["prompt"]
        sc["picture_bindings"] = comp["picture_bindings"]

    try:
        (STORYBOARD_DIR / "current_storyboard.json").write_text(
            json.dumps(sb, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
    return load_cached_storyboard()


def render_schematic_storyboard_image(
    scene: Dict[str, Any],
    ref_lookup: Optional[Dict[str, Dict[str, Any]]] = None,
    force: bool = False,
) -> Path:
    """
    Vẽ bảng phân cảnh sơ đồ 4 khoảnh khắc liên tiếp (Four Successive Moments Schematic Previs Board):
    Storyboard_E01Sxxx.png (1280x720, 16:9) với:
    - 4 khung Moment 1 (00.00-03.75s), Moment 2 (03.75-07.50s), Moment 3 (07.50-11.25s), Moment 4 (11.25-15.00s)
    - Nhúng trực tiếp thumbnail ảnh Ref_*.png lên thanh tiêu đề để đối chiếu trực quan
    - Biểu tượng nhân vật/động vật/đạo cụ mã hóa màu sắc (Colored Blocking Figures)
    - Mũi tên quỹ đạo di chuyển (Subject Movement Paths), Trục 180° Axis, Camera Plan (Shot A / Shot B) & Lời thoại.
    """
    scene_id = scene.get("scene_id", "E01S001")
    out_png = STORYBOARD_DIR / f"Storyboard_{scene_id}.png"
    if out_png.is_file() and not force:
        return out_png

    ref_lookup = ref_lookup or {}
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (16, 32, 35))  # #102023
    dr = ImageDraw.Draw(img)

    f_h1 = _load_font(21, bold=True)
    f_panel_t = _load_font(15, bold=True)
    f_small = _load_font(13, bold=False)
    f_SmallBold = _load_font(13, bold=True)
    f_tiny = _load_font(12, bold=False)

    # Top Header Bar
    dr.rectangle([0, 0, W, 62], fill=(22, 42, 46))
    dr.line([(0, 62), (W, 62)], fill=(78, 119, 115), width=2)
    title_str = f"{scene.get('title', scene_id)}   •   15s · 16:9 · 2 Shots · 4 Successive Moments Previs"
    dr.text((18, 7), title_str, fill=(243, 212, 136), font=f_h1)

    # Active entities legend on header row 2 with embedded mini thumbnail crops
    raw_refs = scene.get("references") or []
    entities = []
    for idx, rf in enumerate(raw_refs):
        info = ref_lookup.get(rf) or classify_ref_asset(rf, idx)
        entities.append(info)

    lx = 18
    dr.text((lx, 36), "Ref Assets:", fill=(185, 215, 206), font=f_small)
    lx += 78
    for ent_idx, ent in enumerate(entities[:8], start=1):
        rgb = tuple(ent.get("color_rgb") or (78, 205, 196))
        lbl = f"P{ent_idx}:{ent.get('short_name', '')[:12]}"
        tw = max(62, len(lbl) * 7 + 30)
        if lx + tw > W - 16:
            break
        dr.rounded_rectangle([lx, 33, lx + tw, 57], radius=5, fill=(28, 48, 52), outline=rgb, width=2)
        # Try to paste 20x20 thumbnail from real ref image or placeholder card
        ref_fp = find_local_ref_image(ent["filename"]) or generate_smart_ref_card_image(ent, force=False)
        if ref_fp and ref_fp.is_file():
            try:
                with Image.open(ref_fp) as r_im:
                    thumb = r_im.convert("RGB").resize((20, 20))
                    img.paste(thumb, (lx + 3, 35))
            except Exception:
                dr.ellipse([lx + 5, 39, lx + 15, 49], fill=rgb)
        else:
            dr.ellipse([lx + 5, 39, lx + 15, 49], fill=rgb)
        dr.text((lx + 26, 37), lbl, fill=(233, 243, 239), font=f_tiny)
        lx += tw + 6

    # Split Action & Timing into Shot 1 and Shot 2
    act_lines = [ln.strip() for ln in (scene.get("action_timing") or "").splitlines() if ln.strip()]
    shot1_desc = act_lines[0] if len(act_lines) >= 1 else (scene.get("movement") or "Shot 1 blocking")
    shot2_desc = act_lines[1] if len(act_lines) >= 2 else (act_lines[0] if act_lines else "Shot 2 blocking")
    shot1_clean = re.sub(r"^\d+\.\d+[–-]\d+\.\d+s:\s*(?:Shot\s*\d+:?\s*)?", "", shot1_desc, flags=re.IGNORECASE)
    shot2_clean = re.sub(r"^\d+\.\d+[–-]\d+\.\d+s:\s*(?:Shot\s*\d+:?\s*)?", "", shot2_desc, flags=re.IGNORECASE)

    cam_raw = scene.get("camera") or ""
    cam_a_m = re.search(r"Shot\s*A[^:]*:\s*([^.]+)", cam_raw, flags=re.IGNORECASE)
    cam_b_m = re.search(r"Shot\s*B[^:]*:\s*([^.]+)", cam_raw, flags=re.IGNORECASE)
    cam_a_txt = cam_a_m.group(1).strip() if cam_a_m else "Eye-level medium view (180° axis locked)"
    cam_b_txt = cam_b_m.group(1).strip() if cam_b_m else "Matched angle / detail view (180° axis locked)"

    dlg_lines = scene.get("dialogue_lines") or [
        ln.strip() for ln in (scene.get("dialogue") or "").splitlines() if ln.strip()
    ]

    moments = [
        {
            "idx": 1,
            "time": "00.00–03.75s",
            "shot": "SHOT 1 (A) · SETUP",
            "action": shot1_clean[:92],
            "cam": cam_a_txt[:70],
            "dlg": dlg_lines[0] if len(dlg_lines) >= 1 else "",
            "progress": 0.15,
        },
        {
            "idx": 2,
            "time": "03.75–07.50s",
            "shot": "SHOT 1 (A) · ACTION",
            "action": (scene.get("movement") or shot1_clean)[:92],
            "cam": cam_a_txt[:70],
            "dlg": dlg_lines[1] if len(dlg_lines) >= 2 else (dlg_lines[0] if len(dlg_lines) == 1 else ""),
            "progress": 0.48,
        },
        {
            "idx": 3,
            "time": "07.50–11.25s",
            "shot": "SHOT 2 (B) · TRANSITION",
            "action": shot2_clean[:92],
            "cam": cam_b_txt[:70],
            "dlg": dlg_lines[2] if len(dlg_lines) >= 3 else (dlg_lines[-1] if dlg_lines else ""),
            "progress": 0.75,
        },
        {
            "idx": 4,
            "time": "11.25–15.00s",
            "shot": "SHOT 2 (B) · SETTLE END STATE",
            "action": shot2_clean[-92:] if len(shot2_clean) > 40 else shot2_clean,
            "cam": cam_b_txt[:70],
            "dlg": dlg_lines[3] if len(dlg_lines) >= 4 else (dlg_lines[-1] if dlg_lines else ""),
            "progress": 1.0,
        },
    ]

    mov_low = (scene.get("movement") or "").lower() + " " + (scene.get("action_timing") or "").lower()
    moves_ltr = "left-to-right" in mov_low or "left to right" in mov_low or "walks through" in mov_low or "approaches" in mov_low or "enters" in mov_low
    has_handoff = "handoff" in mov_low or "extends it" in mov_low or "passes" in mov_low or "gives" in mov_low
    gate_opens = "open" in mov_low and "gate" in mov_low
    gate_closes = ("close" in mov_low or "shut" in mov_low) and "gate" in mov_low

    # Separate entities by type for spatial layout
    chars_animals = [e for e in entities if e["category"] in ("character", "animal")][:4]
    props_list = [e for e in entities if e["category"] == "prop"][:3]
    locs_list = [e for e in entities if e["category"] == "location"][:1]
    if not chars_animals and entities:
        chars_animals = entities[:2]

    # 2x2 Panel Grid coordinates
    pad_x, pad_y = 14, 10
    grid_top = 68
    pw = (W - pad_x * 3) // 2
    ph = (H - grid_top - pad_y * 3) // 2

    for m_idx, mom in enumerate(moments):
        col = m_idx % 2
        row = m_idx // 2
        px = pad_x + col * (pw + pad_x)
        py = grid_top + row * (ph + pad_y)
        prog = mom["progress"]

        # Panel frame
        dr.rounded_rectangle([px, py, px + pw, py + ph], radius=10, fill=(24, 44, 48), outline=(78, 119, 115), width=2)

        # Panel Header strip
        dr.rounded_rectangle([px + 2, py + 2, px + pw - 2, py + 32], radius=8, fill=(18, 33, 37))
        dr.text((px + 12, py + 7), f"MOMENT {mom['idx']}  •  {mom['time']}", fill=(243, 212, 136), font=f_panel_t)
        shot_badge_col = (52, 211, 153) if m_idx < 2 else (96, 165, 250)
        dr.text((px + pw - 215, py + 8), mom["shot"], fill=shot_badge_col, font=f_SmallBold)

        # Stage Ground Plane & Horizon inside panel
        horizon_y = py + 110
        stage_bot = py + ph - 66
        dr.rectangle([px + 8, py + 34, px + pw - 8, horizon_y], fill=(20, 38, 44))
        dr.rectangle([px + 8, horizon_y, px + pw - 8, stage_bot], fill=(29, 52, 50))
        dr.line([(px + 8, horizon_y), (px + pw - 8, horizon_y)], fill=(66, 108, 102), width=2)

        # Perspective ground lines
        for gx in range(px + 60, px + pw - 40, 90):
            dr.line([(gx, horizon_y), (gx + (gx - (px + pw // 2)) // 2, stage_bot)], fill=(36, 64, 61), width=1)

        # Location backdrop label + fence line
        loc_label = locs_list[0]["display_name"] if locs_list else scene.get("location", "Scene Set")
        dr.text((px + 16, py + 40), f"SET: {loc_label}", fill=(165, 223, 202), font=f_tiny)
        for fx in range(px + 30, px + pw - 30, 64):
            dr.line([(fx, horizon_y - 16), (fx, horizon_y + 4)], fill=(90, 128, 120), width=3)
        dr.line([(px + 20, horizon_y - 10), (px + pw - 20, horizon_y - 10)], fill=(90, 128, 120), width=2)

        # 180-degree camera axis line near bottom of stage
        axis_y = stage_bot - 18
        for ax in range(px + 24, px + pw - 80, 22):
            dr.line([(ax, axis_y), (ax + 12, axis_y)], fill=(243, 212, 136), width=2)
        dr.text((px + pw - 165, axis_y - 14), "180° CAMERA AXIS", fill=(243, 212, 136), font=f_tiny)

        # Camera Icon & lens frustum
        cam_x = px + 42 + (int(28 * prog) if "dolly" in mom["cam"].lower() or "track" in mom["cam"].lower() else 0)
        cam_y = axis_y - 4
        dr.polygon([(cam_x, cam_y), (cam_x - 16, cam_y + 12), (cam_x + 16, cam_y + 12)], fill=shot_badge_col)
        dr.line([(cam_x, cam_y), (cam_x + 55, horizon_y + 25)], fill=shot_badge_col, width=1)
        dr.line([(cam_x, cam_y), (cam_x - 25, horizon_y + 25)], fill=shot_badge_col, width=1)
        dr.text((px + 16, py + 56), f"CAM: {mom['cam']}", fill=(175, 199, 194), font=f_tiny)

        # Draw Props (e.g., Gate, Latch, Bucket, Tub, Apple)
        non_gate_idx = 0
        for p_i, pr in enumerate(props_list):
            p_rgb = tuple(pr.get("color_rgb") or (251, 191, 36))
            p_name = pr.get("short_name", "Prop")
            if "gate" in p_name.lower() and "latch" not in p_name.lower() and "bolt" not in p_name.lower():
                gx = px + pw // 2 - 15
                gy = horizon_y + 18
                swing_dx = 34 if (gate_opens and prog >= 0.4) or (not gate_closes and "open" in mov_low) else 0
                if gate_closes and prog >= 0.45:
                    swing_dx = 0
                dr.rectangle([gx - 4, gy - 28, gx + 4, gy + 26], fill=(220, 220, 220))
                dr.line([(gx, gy - 22), (gx + 48 - swing_dx, gy - 22 - swing_dx // 2)], fill=p_rgb, width=4)
                dr.line([(gx, gy + 12), (gx + 48 - swing_dx, gy + 12 - swing_dx // 2)], fill=p_rgb, width=4)
                dr.text((gx - 22, gy - 44), p_name[:14], fill=p_rgb, font=f_tiny)
            else:
                if has_handoff and non_gate_idx == 0:
                    pr_x = int(px + pw * (0.38 + 0.18 * prog))
                    pr_y = horizon_y + 46
                    dr.line([(px + int(pw * 0.38), pr_y), (pr_x, pr_y)], fill=p_rgb, width=2)
                else:
                    pr_x = px + pw - 75 - non_gate_idx * 95
                    pr_y = horizon_y + 58 + (non_gate_idx % 2) * 18
                non_gate_idx += 1
                dr.rounded_rectangle([pr_x - 13, pr_y - 10, pr_x + 13, pr_y + 10], radius=5, fill=p_rgb, outline=(255, 255, 255), width=2)
                dr.text((pr_x - 28, pr_y + 13), p_name[:14], fill=p_rgb, font=f_tiny)

        # Draw Characters & Animals with progressive blocking positions
        n_subj = max(1, len(chars_animals))
        for s_i, subj in enumerate(chars_animals):
            s_rgb = tuple(subj.get("color_rgb") or (255, 107, 107))
            s_name = subj.get("short_name", f"S{s_i+1}")
            is_animal = subj.get("category") == "animal"

            # Base X and progressive movement X
            base_x = px + 115 + s_i * ((pw - 240) // max(1, n_subj))
            if moves_ltr and (s_i == 0 or is_animal):
                cur_x = int(base_x + (prog * 115))
                prev_x = int(base_x + (max(0.0, prog - 0.28) * 115))
            else:
                cur_x = int(base_x + (prog * 18 if s_i % 2 == 0 else -prog * 12))
                prev_x = base_x
            cur_x = max(px + 55, min(px + pw - 65, cur_x))
            cur_y = horizon_y + 42 + (s_i % 2) * 22

            # Trajectory arrow if moved
            if abs(cur_x - prev_x) > 8:
                dr.line([(prev_x, cur_y + 18), (cur_x - 10, cur_y + 18)], fill=s_rgb, width=3)
                dr.polygon(
                    [(cur_x - 4, cur_y + 18), (cur_x - 14, cur_y + 13), (cur_x - 14, cur_y + 23)],
                    fill=s_rgb,
                )

            if is_animal:
                dr.rounded_rectangle([cur_x - 22, cur_y - 4, cur_x + 16, cur_y + 16], radius=7, fill=s_rgb, outline=(255, 255, 255), width=2)
                dr.ellipse([cur_x + 10, cur_y - 16, cur_x + 28, cur_y + 2], fill=s_rgb, outline=(255, 255, 255), width=2)
                for lx in (cur_x - 18, cur_x - 8, cur_x + 4, cur_x + 12):
                    dr.line([(lx, cur_y + 16), (lx, cur_y + 32)], fill=s_rgb, width=3)
                dr.text((cur_x - 20, cur_y - 32), s_name, fill=(255, 255, 255), font=f_SmallBold)
            else:
                dr.ellipse([cur_x - 11, cur_y - 36, cur_x + 11, cur_y - 14], fill=s_rgb, outline=(255, 255, 255), width=2)
                dr.rounded_rectangle([cur_x - 13, cur_y - 13, cur_x + 13, cur_y + 18], radius=6, fill=s_rgb, outline=(255, 255, 255), width=2)
                arm_reach = 12 if prog in (0.48, 0.75) else 4
                dr.line([(cur_x + 12, cur_y - 6), (cur_x + 22 + arm_reach, cur_y - 2)], fill=s_rgb, width=4)
                dr.line([(cur_x - 12, cur_y - 6), (cur_x - 20, cur_y + 8)], fill=s_rgb, width=3)
                dr.line([(cur_x - 6, cur_y + 18), (cur_x - 8, cur_y + 38)], fill=s_rgb, width=4)
                dr.line([(cur_x + 6, cur_y + 18), (cur_x + 8, cur_y + 38)], fill=s_rgb, width=4)
                dr.text((cur_x - 24, cur_y - 52), s_name, fill=(255, 255, 255), font=f_SmallBold)

        # Bottom Caption Box (Action Beat + Dialogue)
        dr.rectangle([px + 4, stage_bot + 2, px + pw - 4, py + ph - 4], fill=(15, 28, 31))
        dr.text((px + 12, stage_bot + 6), f"Beat: {mom['action']}", fill=(215, 235, 228), font=f_tiny)
        if mom["dlg"]:
            dr.text((px + 12, stage_bot + 26), f">> {mom['dlg'][:85]}", fill=(243, 212, 136), font=f_SmallBold)
        else:
            dr.text((px + 12, stage_bot + 26), ">> (Physical blocking / natural farm ambiance)", fill=(135, 165, 158), font=f_tiny)

    img.save(out_png, "PNG")
    return out_png


def export_blocking_storyboard_html(storyboard_data: Dict[str, Any]) -> Tuple[Path, str]:
    """
    Xuất trọn bộ trang HTML Storyboard chuẩn Willow Creek Farm (#102023 dark-teal theme)
    bao gồm:
    1. Bảng Kiểm Tra Trực Quan Toàn Bộ Ảnh Tham Chiếu (Reference Asset Visual Check Sheet: Ref_*.png) ở đầu trang
    2. Bảng sơ đồ 4 khoảnh khắc Storyboard_E01Sxxx.png cho từng cảnh
    3. Bộ thẻ ảnh tham chiếu Ref_*.png trực quan (<Picture 1..N> + Thumbnail + Lightbox phóng to) ngay trong mục References của từng cảnh.
    """
    scenes = storyboard_data.get("scenes") or []
    ref_registry = storyboard_data.get("ref_registry") or []
    ref_lookup = {r["filename"]: r for r in ref_registry}

    for sc in scenes:
        render_schematic_storyboard_image(sc, ref_lookup=ref_lookup, force=False)

    title_esc = html_lib.escape(storyboard_data.get("title") or "Episode 1 · Storyboards")
    meta_esc = html_lib.escape(storyboard_data.get("meta") or f"{len(scenes)} scenes × 15 seconds · Production blocking storyboard · 16:9")
    desc_esc = html_lib.escape(
        storyboard_data.get("description")
        or "These are newly drawn schematic previs boards for the rewritten script. Each board shows four successive moments, on-camera cast, subject paths, camera plan and dialogue. Colored figures are blocking symbols, not replacement identity references. Use the named Ref_ images for final character and location design. A storyboard panel is never an extracted last frame."
    )

    # Top Reference Asset Visual Check Gallery in HTML
    ref_cards_html = []
    for idx, r in enumerate(ref_registry):
        stt_num = int(r.get("stt") or (idx + 1))
        stt_badge = f"#{stt_num:02d}"
        rf_name = html_lib.escape(r["filename"])
        disp = html_lib.escape(r.get("display_name") or rf_name)
        cat = html_lib.escape(r.get("category") or "prop")
        cat_vi = html_lib.escape(r.get("category_vi") or "Asset")
        col_hex = html_lib.escape(r.get("color_hex") or "#4ecdc4")
        has_real = bool(r.get("has_image"))
        badge_txt = "🟢 Ảnh thật" if has_real else "📐 Sơ đồ tạm"
        badge_bg = "#065f46" if has_real else "#374151"
        r_img_url = f"/api/storyboard/ref-image?name={urllib.parse.quote(r['filename'])}"
        sc_links = " ".join(
            f'<a href="#{html_lib.escape(sid)}" style="font-size:10px;background:#102023;padding:1px 5px;border-radius:4px;border:1px solid #4e7773;text-decoration:none">{html_lib.escape(sid)}</a>'
            for sid in (r.get("scene_ids") or [])[:8]
        )
        ref_cards_html.append(
            f'<div class="ref-card" data-cat="{cat}" data-stt="{stt_num}" style="background:#15262a;border:1.5px solid {col_hex};border-radius:10px;padding:10px;display:flex;gap:10px;align-items:flex-start">'
            f'<a href="{r_img_url}" target="_blank" style="position:relative;display:block;flex-shrink:0">'
            f'<img src="{r_img_url}" data-ref="{rf_name}" alt="{rf_name}" style="width:72px;height:72px;object-fit:cover;border-radius:8px;border:2px solid {col_hex};background:#102023;display:block">'
            f'<span style="position:absolute;top:2px;left:2px;background:rgba(13,148,136,0.92);color:#fff;font-size:10px;font-weight:800;padding:1px 5px;border-radius:4px;font-family:monospace">{stt_badge}</span>'
            f'</a>'
            f'<div style="min-width:0;flex:1">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;gap:4px">'
            f'<strong style="color:#f3d488;font-size:13px"><span style="font-family:monospace;color:#6ee7b7;margin-right:4px">{stt_badge}</span>{disp}</strong>'
            f'<span style="background:{badge_bg};color:#e9f3ef;font-size:10px;padding:1px 6px;border-radius:4px">{badge_txt}</span>'
            f'</div>'
            f'<div style="font-family:monospace;font-size:11px;color:#a5dfca;margin:2px 0">{rf_name}</div>'
            f'<div style="font-size:11px;color:#b9d7ce;margin-bottom:4px">{cat_vi} &bull; {r.get("scene_count", 0)} scenes</div>'
            f'<div style="display:flex;flex-wrap:wrap;gap:3px">{sc_links}</div>'
            f'</div></div>'
        )

    chips_html = " ".join(
        f'<a class="chip" href="#{html_lib.escape(sc["scene_id"])}">{html_lib.escape(sc["chip"])}</a>'
        for sc in scenes
    )

    articles_html = []
    for sc in scenes:
        sid = html_lib.escape(sc["scene_id"])
        stitle = html_lib.escape(sc.get("title") or sid)
        smeta = html_lib.escape(sc.get("meta") or "15 seconds · 16:9 · two shots · four successive moments")
        img_url = f"/api/storyboard/previs-image?scene_id={urllib.parse.quote(sc['scene_id'])}"
        act_html = "<br>".join(html_lib.escape(ln) for ln in (sc.get("action_timing") or "").splitlines() if ln.strip())
        mov_html = html_lib.escape(sc.get("movement") or "")
        cam_html = html_lib.escape(sc.get("camera") or "")
        dlg_html = "<br>".join(html_lib.escape(ln) for ln in (sc.get("dialogue") or "").splitlines() if ln.strip())
        refs_text_html = html_lib.escape(", ".join(sc.get("references") or []))

        # Visual reference thumbnails inside each scene's <dd> References block
        scene_ref_thumbs = []
        bindings_map = {b["filename"]: b for b in (sc.get("picture_bindings") or [])}
        for rf in (sc.get("references") or []):
            r_obj = ref_lookup.get(rf) or classify_ref_asset(rf, 0)
            b_obj = bindings_map.get(rf)
            pic_badge = html_lib.escape(b_obj["picture_tag"]) if b_obj else "Ref"
            col_hex = html_lib.escape(r_obj.get("color_hex") or "#4e7773")
            rf_esc = html_lib.escape(rf)
            disp_esc = html_lib.escape(r_obj.get("display_name") or rf)
            r_url = f"/api/storyboard/ref-image?name={urllib.parse.quote(rf)}"
            scene_ref_thumbs.append(
                f'<a href="{r_url}" target="_blank" style="display:inline-flex;align-items:center;gap:8px;background:#142529;border:1.5px solid {col_hex};border-radius:8px;padding:6px 9px;text-decoration:none;color:#e9f3ef">'
                f'<img src="{r_url}" data-ref="{rf_esc}" alt="{rf_esc}" style="width:52px;height:52px;object-fit:cover;border-radius:6px;border:1px solid {col_hex};background:#102023">'
                f'<span><b style="color:#f3d488;font-size:11.5px;display:block">{pic_badge} &middot; {disp_esc}</b>'
                f'<code style="font-size:10.5px;color:#a5dfca">{rf_esc}</code></span></a>'
            )
        refs_visual_block = (
            f'<div style="margin-bottom:6px;font-family:monospace;font-size:12.5px;color:#a5dfca">{refs_text_html}</div>'
            f'<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:6px">{"".join(scene_ref_thumbs)}</div>'
        )

        sstyle_html = html_lib.escape(sc.get("visual_style") or storyboard_data.get("visual_style") or DEFAULT_VISUAL_STYLE)
        extra_p_html = html_lib.escape(sc.get("extra_prompt") or "")
        h3_p_html = html_lib.escape(sc.get("h3_prompt") or "")
        extra_dt_dd = f"<dt>Prompt</dt><dd>{extra_p_html}</dd>" if extra_p_html else ""

        articles_html.append(
            f'<article id="{sid}"><h2>{stitle}</h2>\n'
            f'    <p class="meta">{smeta}</p>\n'
            f'    <a href="{img_url}" target="_blank"><img class="previs-board" loading="lazy" src="{img_url}" alt="Schematic storyboard {sid}"></a>\n'
            f'    <dl><dt>Visual style</dt><dd>{sstyle_html}</dd>'
            f'<dt>Action and timing</dt><dd>{act_html}</dd>'
            f'<dt>Character / animal / prop movement</dt><dd>{mov_html}</dd>\n'
            f'    <dt>Camera movement</dt><dd>{cam_html}</dd>'
            f'<dt>Dialogue</dt><dd>{dlg_html}</dd>\n'
            f'    {extra_dt_dd}'
            f'<dt>References</dt><dd>{refs_visual_block}</dd>'
            f'<dt>Full Compiled Video Prompt (MiniMax H3 + Style + Refs)</dt>'
            f'<dd><pre style="white-space:pre-wrap;background:#102023;color:#b9d7ce;padding:10px;border-radius:8px;border:1px solid #355855;font-size:12px;margin:4px 0 0 0">{h3_p_html}</pre></dd>'
            f'</dl></article>'
        )

    master_style_esc = html_lib.escape(storyboard_data.get("visual_style") or DEFAULT_VISUAL_STYLE)
    full_html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{title_esc}</title>'
        '<style>body{margin:0;background:#102023;color:#e9f3ef;font:16px/1.5 system-ui,Segoe UI,sans-serif}'
        'main{max-width:1350px;margin:auto;padding:24px}h1{margin-bottom:4px}a{color:#a5dfca}'
        'nav{display:flex;flex-wrap:wrap;gap:7px;margin:20px 0}.chip{padding:5px 9px;background:#254044;border-radius:6px;text-decoration:none}'
        'article{background:#1c3034;border:1px solid #4e7773;border-radius:12px;padding:20px;margin:20px 0}'
        'article img.previs-board{width:100%;height:auto;border-radius:8px}dt{color:#f3d488;font-weight:700;margin-top:8px}'
        'dd{margin-left:0}p{margin:7px 0}.meta{color:#b9d7ce}.small{font-size:13px;color:#afc7c2}'
        '.ref-sheet{background:#1c3034;border:1.5px solid #4e7773;border-radius:12px;padding:18px;margin:18px 0}'
        '.ref-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(275px,1fr));gap:10px;margin-top:12px}</style></head>'
        f'<body><main><h1>{title_esc}</h1><p class="meta">{meta_esc}</p>'
        f'<p class="style" style="color:#f3d488;font-weight:700">🎨 Master Visual Style: {master_style_esc}</p>'
        f'<p>{desc_esc}</p>'
        f'<section class="ref-sheet"><h2 style="margin:0 0 4px 0;color:#f3d488;font-size:18px">🔍 Reference Assets Visual Check Sheet ({len(ref_registry)} Ref_*.png Entities)</h2>'
        f'<p class="small" style="margin:0">Click any reference thumbnail below to inspect full resolution or click scene chips to jump to scenes where that asset is referenced.</p>'
        f'<div class="ref-grid">{"".join(ref_cards_html)}</div></section>'
        f'<nav>{chips_html}</nav>'
        + "".join(articles_html)
        + "</main></body></html>"
    )

    out_html_path = STORYBOARD_DIR / "Episode_Blocking_Storyboard.html"
    out_html_path.write_text(full_html, encoding="utf-8")
    return out_html_path, full_html


def generate_blocking_storyboard_ai(
    story_text: str,
    scene_count: int = 8,
    episode_title: str = "Episode 1",
    api_url: str = "",
    model: str = "gemini-3.1-pro",
    visual_style: str = "",
    global_prompt_addon: str = "",
) -> Dict[str, Any]:
    """
    Tự động biên kịch bộ Storyboard Nhận Diện chuẩn Willow Creek Farm
    (mỗi cảnh 15 giây = 2 shots × 7.5s, 4 khoảnh khắc, khóa trục 180° Axis, và tự động đặt mã Ref_*.png)
    từ ý tưởng hoặc cốt truyện của người dùng, đồng thời gắn Phong Cách Hình Ảnh vào toàn bộ các cảnh.
    """
    import urllib.request

    scene_count = max(2, min(60, int(scene_count or 8)))
    raw = (story_text or "").strip()
    ep_title = (episode_title or "Episode 1").strip()
    eff_style = (visual_style or DEFAULT_VISUAL_STYLE).strip()

    if api_url and raw:
        sys_prompt = f"""You are a Production Blocking Storyboard Director following the exact 'Willow Creek Farm' 15-second 2-shot production blocking standard.
Transform the user's story/script into an HTML Storyboard document with EXACTLY {scene_count} scenes (`<article id="E01S001">` through `<article id="E01S{scene_count:03d}">`).
MASTER VISUAL STYLE FOR ALL SCENES: {eff_style}

STRICT RULES FOR EVERY SCENE:
1. Each scene is 15 seconds · 16:9 · two shots · four successive moments (`00.00–07.50s: Shot 1` and `07.50–15.00s: Shot 2`).
2. Inside each `<article id="E01Sxxx">`, output:
   - `<h2>E01Sxxx · [Location Name]</h2>`
   - `<p class="meta">15 seconds · 16:9 · two shots · four successive moments</p>`
   - `<a href="Storyboard_E01Sxxx.png"><img loading="lazy" src="Storyboard_E01Sxxx.png" alt="Schematic storyboard E01Sxxx"></a>`
   - `<dl>` with these `<dt>/<dd>` pairs:
     * `<dt>Visual style</dt><dd>{eff_style}</dd>`
     * `<dt>Action and timing</dt><dd>00.00–07.50s: Shot 1: ...<br>07.50–15.00s: Shot 2: ...</dd>`
     * `<dt>Character / animal / prop movement</dt><dd>Detailed physical blocking, left-to-right paths, prop handoff rules, grounded movement, no teleporting or duplicate props.</dd>`
     * `<dt>Camera movement</dt><dd>Shot A (0–7.5s): ... Shot B (7.5–15s): ... respecting the 180° axis.</dd>`
     * `<dt>Dialogue</dt><dd>Speaker: "Line 1"<br>Speaker: "Line 2"</dd>`
     * `<dt>References</dt><dd>Ref_Character_Name.png, Ref_Prop_Name.png, Ref_Location_Name.png</dd>`
3. Keep `Ref_*.png` filenames 100% consistent across all scenes so the Entity Recognition engine can bind every character, animal, prop, and location.
Return ONLY the HTML code starting with `<h1>{ep_title}</h1>` and containing all `<article>` blocks."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": f"{sys_prompt}\n\nUSER STORY:\n{raw}"}],
            "stream": False,
        }
        try:
            req = urllib.request.Request(
                f"{api_url.rstrip('/')}/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                rj = json.loads(resp.read().decode("utf-8", errors="ignore"))
            reply_html = rj["choices"][0]["message"]["content"]
            if "<article" in reply_html:
                return parse_blocking_storyboard_html(
                    reply_html,
                    visual_style=eff_style,
                    global_prompt_addon=global_prompt_addon,
                )
        except Exception:
            pass

    # Built-in Smart Blocking Storyboard Generator when Cookie API is offline or user wants instant generation
    if not raw and SAMPLE_WILLOW_HTML.is_file():
        return parse_blocking_storyboard_html(
            SAMPLE_WILLOW_HTML.read_text(encoding="utf-8", errors="ignore"),
            visual_style=eff_style,
            global_prompt_addon=global_prompt_addon,
        )

    sentences = [s.strip() for s in re.split(r"(?:\r?\n+|(?<=[.!?。！？])\s+)", raw) if s.strip()]
    if not sentences:
        sentences = [f"Scene beat {i+1} in {ep_title}" for i in range(scene_count)]

    # Extract capitalized names or default cast/props/locations
    found_refs = list(dict.fromkeys(re.findall(r"\bRef_[A-Za-z0-9_]+\.png\b", raw)))
    if not found_refs:
        found_refs = [
            "Ref_Lead_Hero.png",
            "Ref_Partner_Character.png",
            "Ref_Companion_Pet.png",
            "Ref_Key_Artifact.png",
            "Ref_Wooden_Gate.png",
            "Ref_Main_Courtyard.png",
            "Ref_Garden_Path.png",
        ]

    articles = [f"<h1>{html_lib.escape(ep_title)}</h1>"]
    articles.append(f'<p class="meta">{scene_count} scenes × 15 seconds = {round(scene_count*15/60, 1):g} minutes · Production blocking storyboard · 16:9</p>')
    articles.append(f'<p class="style">{html_lib.escape(eff_style)}</p>')
    for idx in range(1, scene_count + 1):
        sid = f"E01S{idx:03d}"
        b1 = sentences[(idx * 2 - 2) % len(sentences)]
        b2 = sentences[(idx * 2 - 1) % len(sentences)]
        loc = "Main Courtyard / Route" if idx % 2 == 1 else "Garden Path / Approach"
        scene_refs = found_refs[:min(len(found_refs), 5)]
        articles.append(
            f'<article id="{sid}"><h2>{sid} · {loc}</h2>'
            f'<p class="meta">15 seconds · 16:9 · two shots · four successive moments</p>'
            f'<a href="Storyboard_{sid}.png"><img loading="lazy" src="Storyboard_{sid}.png" alt="Schematic storyboard {sid}"></a>'
            f'<dl>'
            f'<dt>Visual style</dt><dd>{html_lib.escape(eff_style)}</dd>'
            f'<dt>Action and timing</dt><dd>00.00–07.50s: Shot 1: {html_lib.escape(b1)}<br>07.50–15.00s: Shot 2: {html_lib.escape(b2)}</dd>'
            f'<dt>Character / animal / prop movement</dt><dd>{html_lib.escape(b1)} The subject moves left-to-right along the established path with grounded steps; props remain in consistent hands without duplicating or teleporting. {html_lib.escape(b2)}</dd>'
            f'<dt>Camera movement</dt><dd>Shot A (0–7.5s): smooth eye-level medium tracking shot on the established side of the 180° axis. Shot B (7.5–15s): matched medium-close angle settling to a locked frame before the dialogue finishes.</dd>'
            f'<dt>Dialogue</dt><dd>Lead: &quot;{html_lib.escape(b1[:70])}&quot;<br>Partner: &quot;{html_lib.escape(b2[:70])}&quot;</dd>'
            f'<dt>References</dt><dd>{", ".join(scene_refs)}</dd>'
            f'</dl></article>'
        )
    return parse_blocking_storyboard_html(
        "\n".join(articles),
        visual_style=eff_style,
        global_prompt_addon=global_prompt_addon,
    )
