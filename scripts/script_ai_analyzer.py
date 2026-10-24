# -*- coding: utf-8 -*-
"""
Bộ Phân Tích Kịch Bản AI & Biên Dịch Chuẩn MiniMax H3 (Gemini Cookie + Gemini API Key + Smart Fallback)
Hỗ trợ:
1. Tích hợp trực tiếp Gemini Cookie API nội bộ (127.0.0.1:4981/openai/v1)
2. Tích hợp Google Gemini API Key chính thức (generativelanguage.googleapis.com)
3. Bộ biên dịch thông minh Offline (Smart Compiler) đảm bảo hoạt động 100% mọi lúc
4. Sinh song song:
   - Danh sách Tham Chiếu (<Picture 1>..<Picture N>) đầy đủ 4 nhóm:
     * Nhân vật chính: Sheet 3 góc (Toàn thân Trước + Toàn thân Sau + Cận cảnh góc nghiêng 3/4 khuôn mặt)
     * Nhân vật phụ: Sheet 3 góc (Toàn thân Trước + Toàn thân Sau + Cận cảnh góc nghiêng 3/4 khuôn mặt)
     * Bối cảnh (Environment / Background Reference)
     * Đạo cụ / Vũ khí (Prop / Artifact Reference Sheet)
   - Danh sách Phân Cảnh (Cảnh 1..Cảnh N) theo đúng Thời gian mỗi cảnh & Tổng thời gian kịch bản,
     với Prompt Chuẩn MiniMax H3 (subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

STYLE_PRESETS = {
    "xianxia_live": {
        "name_vi": "⚔️ Tiên Hiệp / Cổ Trang Live-Action 8K",
        "style_en": (
            "Ultra-photorealistic 8K live-action Chinese Xianxia fantasy cinema, "
            "natural skin texture with realistic pores, flowing silk hanfu robes, "
            "ethereal celestial clouds and golden sunlight volumetric rays, ARRI Alexa 65 anamorphic lens"
        ),
        "img_prefix_en": "Ultra-photorealistic 8K live-action Xianxia cinema style, studio-grade natural lighting, ",
        "music_en": "Ethereal Chinese guzheng, bamboo flute (dizi), and warm cinematic orchestral strings.",
    },
    "anime_2d_fantasy": {
        "name_vi": "🌸 Hoạt Hình 2D Fantasy Anime (Liền mạch)",
        "style_en": (
            "High-budget 2D Japanese fantasy anime film style (Ufotable and Makoto Shinkai aesthetic), "
            "crisp hand-drawn line art, vibrant cel-shaded colors, glowing magical particle effects, "
            "expressive anime eyes, dynamic sakuga fluid animation, painterly fantasy backgrounds"
        ),
        "img_prefix_en": "Masterpiece 2D fantasy anime style (Ufotable / Makoto Shinkai aesthetic), clean crisp line art, vibrant cel-shading, ",
        "music_en": "Sweeping emotional anime orchestral soundtrack with piano, violin, and celesta.",
    },
    "donghua_3d": {
        "name_vi": "🐉 Hoạt Hình 3D Tu Tiên CG (Donghua UE5)",
        "style_en": (
            "Ultra-detailed 3D Chinese Donghua CG animation rendered in Unreal Engine 5, "
            "delicate subsurface scattering porcelain skin, intricate embroidered silk robes and hair physics, "
            "epic martial arts spiritual aura effects, ray-traced cinematic lighting"
        ),
        "img_prefix_en": "Masterpiece 3D Donghua CG animation rendered in Unreal Engine 5, delicate porcelain skin, intricate hair strands and ornate silk cultivation robes, ",
        "music_en": "Epic 3D Donghua orchestral battle and cultivation theme with traditional drums and flute.",
    },
    "pixar_3d": {
        "name_vi": "🧸 Hoạt Hình 3D Pixar / Disney CG",
        "style_en": (
            "High-end 3D Pixar and Disney feature animation style, expressive stylized proportions, "
            "warm global illumination, rich fabric and hair grooming textures, smooth appealing character acting"
        ),
        "img_prefix_en": "High-end 3D Pixar/Disney animated movie style, appealing expressive features, soft cinematic rim lighting, rich textures, ",
        "music_en": "Warm whimsical orchestral Pixar-style adventure score.",
    },
    "cinema_8k": {
        "name_vi": "🎬 Điện Ảnh Hiện Đại Hollywood 8K",
        "style_en": (
            "Ultra-photorealistic 8K Hollywood live-action cinema shot on ARRI Alexa 65 with 35mm Master Prime lens, "
            "natural skin texture, shallow depth of field, rich cinematic color grading, realistic physical motion"
        ),
        "img_prefix_en": "Ultra-photorealistic 8K raw Hollywood cinema photo shot on 85mm f/1.4 Master Prime lens, natural skin pores, realistic lighting, ",
        "music_en": "Subtle modern cinematic ambient score.",
    },
    "cyberpunk_scifi": {
        "name_vi": "🤖 Cyberpunk / Sci-Fi Điện Ảnh 8K",
        "style_en": (
            "Ultra-photorealistic 8K Cyberpunk Sci-Fi live-action cinema, neon-drenched rain reflections, "
            "futuristic high-tech visor and cybernetic attire, volumetric fog, anamorphic lens flares"
        ),
        "img_prefix_en": "Ultra-photorealistic 8K Cyberpunk Sci-Fi cinema style, neon rim lighting, detailed futuristic textures, sharp focus, ",
        "music_en": "Atmospheric dark synthwave and futuristic sci-fi pulse.",
    },
}


def _extract_json_obj(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        cand = text[start : end + 1]
        try:
            return json.loads(cand)
        except Exception:
            cleaned = re.sub(r",\s*([\}\]])", r"\1", cand)
            return json.loads(cleaned)
    raise ValueError("Phản hồi không chứa JSON hợp lệ")


def _build_3view_character_sheet_prompt(subj_def_en: str, style_info: dict, existing_prompt: str = "") -> str:
    """
    Đảm bảo prompt tạo ảnh nhân vật (chính & phụ) luôn chuẩn Sheet 3 góc:
    1) Toàn thân mặt trước (Full-body Front view)
    2) Toàn thân mặt sau (Full-body Back view)
    3) Cận cảnh góc nghiêng 3/4 khuôn mặt (Close-up 3/4 Three-Quarter Angle Facial Portrait)
    """
    ep = (existing_prompt or "").strip()
    lower_ep = ep.lower()
    if (
        ep
        and ("front" in lower_ep)
        and ("back" in lower_ep or "rear" in lower_ep)
        and ("3/4" in lower_ep or "three-quarter" in lower_ep or "three quarter" in lower_ep)
    ):
        return ep

    base_desc = (subj_def_en or ep or "A distinctive character with clear facial features, signature hairstyle, and detailed costume").strip().rstrip(".")
    return (
        f"3-Panel Character Turnaround Reference Sheet (Sheet 3 Góc: Trước - Sau - Góc nghiêng 3/4 khuôn mặt) in {style_info['style_en']}: "
        f"Subject: {base_desc}. "
        f"Layout divided into 3 clean side-by-side panels on a neutral studio background: "
        f"[Panel 1 - Left: Full-body FRONT view standing in a balanced pose, showing complete front costume design, footwear, and proportions], "
        f"[Panel 2 - Center: Full-body BACK view showing rear garment structure, hair length from behind, cape/robe back details, and accessories], "
        f"[Panel 3 - Right: High-resolution CLOSE-UP 3/4 THREE-QUARTER ANGLE FACIAL PORTRAIT capturing sharp eyes, nose bridge, jawline, skin texture, expression, and hairline details]. "
        f"Consistent studio lighting, identical outfit and colors across all 3 views, 8K concept sheet, no text, no watermark."
    )


def _build_environment_prompt(subj_def_en: str, style_info: dict, existing_prompt: str = "") -> str:
    """Đảm bảo prompt tạo ảnh bối cảnh (Environment / Location) chi tiết, không có người."""
    ep = (existing_prompt or "").strip()
    if ep and len(ep) > 90 and "no character" in ep.lower():
        return ep
    base_desc = (subj_def_en or ep or "Cinematic atmospheric environment with rich architectural and natural details").strip().rstrip(".")
    return (
        f"Masterpiece Wide-Angle Establishing Environment Reference Plate in {style_info['style_en']}: "
        f"{base_desc}. "
        f"Cinematic volumetric lighting, deep atmospheric perspective, rich surface and terrain textures, "
        f"empty background environment plate with NO characters or people in frame, 8K ultra-detailed concept art, no text, no watermark."
    )


def _build_prop_prompt(subj_def_en: str, style_info: dict, existing_prompt: str = "") -> str:
    """Đảm bảo prompt tạo ảnh đạo cụ / vũ khí (Prop / Weapon Sheet) chi tiết, tách nền sạch."""
    ep = (existing_prompt or "").strip()
    if ep and len(ep) > 90 and ("prop" in ep.lower() or "isolated" in ep.lower()):
        return ep
    base_desc = (subj_def_en or ep or "Signature hero prop and weapon with intricate craftsmanship and glowing details").strip().rstrip(".")
    return (
        f"Hero Prop & Weapon Design Reference Sheet in {style_info['style_en']}: "
        f"{base_desc}. "
        f"Showing clear front orthographic view and 3/4 angled detail view on a clean dark neutral studio background, "
        f"intricate material textures, metallic/crystal reflections, engraved runes, isolated object with NO hands or people holding it, 8K studio render, no text."
    )


def _normalize_ref_category(role_vi: str, category_raw: str = "", name_vi: str = "") -> tuple[str, str]:
    """Phân loại chuẩn thành 4 nhóm: character_main, character_sub, environment, prop."""
    cat = (category_raw or "").strip().lower()
    comb = f"{cat} {(role_vi or '').lower()} {(name_vi or '').lower()}"
    if cat == "environment" or any(k in comb for k in ("bối cảnh", "không gian", "địa điểm", "background", "environment", "location", "cảnh nền", "núi", "đài đá", "rừng", "cung điện")):
        return "environment", "🏞️ Bối cảnh (Environment)"
    if cat == "prop" or any(k in comb for k in ("đạo cụ", "vũ khí", "pháp bảo", "thanh kiếm", "cổ kiếm", "trượng", "prop", "weapon", "artifact", "item")):
        return "prop", "🗡️ Đạo cụ / Vũ khí (Prop)"
    if cat == "character_sub" or any(k in comb for k in ("nhân vật phụ", "phụ", "đối thủ", "đồng hành", "linh thú", "supporting", "secondary", "số 2", "quái", "rồng")):
        return "character_sub", "👥 Nhân vật phụ (Sheet 3 góc)"
    return "character_main", "👤 Nhân vật chính (Sheet 3 góc)"


def call_google_gemini_api_key(prompt_text: str, api_key: str, preferred_model: str = "gemini-2.5-flash") -> tuple[dict | None, str]:
    """
    Gọi trực tiếp Google Gemini REST API chính thức bằng API Key (AIzaSy...).
    Tự động thử qua danh sách model ổn định nếu model được chọn không khả dụng.
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return None, "Chưa nhập Gemini API Key"

    models_to_try = []
    if preferred_model and not preferred_model.startswith("gemini-3.") and not preferred_model.startswith("gemini-4"):
        models_to_try.append(preferred_model.strip())
    for m in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro", "gemini-1.5-flash", "gemini-1.5-pro"):
        if m not in models_to_try:
            models_to_try.append(m)

    last_err = ""
    for m_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={api_key}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
            "generationConfig": {
                "temperature": 0.45,
                "responseMimeType": "application/json",
            },
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                resp_data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            candidates = resp_data.get("candidates") or []
            if candidates:
                parts = (candidates[0].get("content") or {}).get("parts") or []
                raw_txt = "".join(str(p.get("text") or "") for p in parts)
                parsed = _extract_json_obj(raw_txt)
                if isinstance(parsed, dict):
                    return parsed, f"Google Gemini API ({m_name})"
        except urllib.error.HTTPError as he:
            try:
                err_body = he.read().decode("utf-8", errors="ignore")
                err_j = json.loads(err_body)
                msg = (err_j.get("error") or {}).get("message") or str(he)
            except Exception:
                msg = str(he)
            last_err = f"{m_name}: {msg}"
            if "API_KEY_INVALID" in last_err or (he.code in (400, 403) and "key" in last_err.lower()):
                break
        except Exception as e:
            last_err = f"{m_name}: {e}"
            continue

    return None, last_err or "Không gọi được Google Gemini API"


def _ensure_h3_contract_format(
    raw_prompt: str,
    references: list[dict],
    used_tags: list[str],
    style_info: dict,
    scene_idx: int,
    total_scenes: int,
    start_sec: int,
    end_sec: int,
    scene_seconds: int,
    summary_en: str,
    aspect: str,
) -> str:
    """Đảm bảo prompt của mỗi cảnh luôn có đầy đủ 6 phần chuẩn MiniMax H3 Reference Contract."""
    txt = (raw_prompt or "").strip()
    if "subject_definitions:" in txt and "detailed_description:" in txt:
        return txt

    ref_map = {r["tag"]: r for r in references}
    active_tags = [t for t in used_tags if t in ref_map] or ([r["tag"] for r in references[:4]] if references else ["<Picture 1>"])

    subj_lines = []
    for t in active_tags:
        r_obj = ref_map.get(t)
        desc = (r_obj.get("subject_def_en") if r_obj else "") or "Key reference subject with consistent visual identity."
        subj_lines.append(f"- {t}: {desc}")
    subj_block = "\n".join(subj_lines)

    half_s = max(2, scene_seconds // 2)
    continuity_note = (
        f"Seamless continuation from Scene {scene_idx - 1}, preserving exact lighting, environment, and character positioning. "
        if scene_idx > 1
        else "Opening establishing shot introducing the main subject, prop, and environment atmosphere. "
    )

    if "[Shot 1]" not in txt:
        shot_body = (
            f"[Shot 1] (0s-{half_s}s) {continuity_note}{style_info['style_en']}. "
            f"{txt} "
            f"[Shot 2] ({half_s}s-{scene_seconds}s) Smooth camera progression holding focus on {', '.join(active_tags)}, "
            f"natural micro-expressions, realistic cloth, prop, and hair movement, ending in a coherent pose ready for the next continuous scene."
        )
    else:
        shot_body = f"{continuity_note}{style_info['style_en']}. {txt}"

    tags_joined = ", ".join(active_tags)
    return (
        f"subject_definitions:\n{subj_block}\n\n"
        f"summary: [reference generation] {style_info['style_en']}. Scene {scene_idx}/{total_scenes} ({start_sec}s-{end_sec}s, {aspect}): {summary_en or 'Continuous cinematic sequence.'}\n\n"
        f"retention_analysis: Strictly preserve the exact facial structure, 3/4 profile, hairstyle, signature outfit, prop details, and environment of {tags_joined} from the reference image(s) without any morphing or wardrobe drift across Scene {scene_idx}.\n\n"
        f"detailed_description: {shot_body}\n\n"
        f"overall_soundscape: Synchronized realistic environmental foley, subtle footsteps, cloth rustle, weapon/prop resonance, atmospheric ambience, and crystal-clear vocal lip-sync if dialogue is spoken.\n\n"
        f"non_diegetic_music: {style_info['music_en']}"
    )


def _smart_local_script_compiler(
    script_text: str,
    style_key: str,
    custom_style: str,
    scene_seconds: int,
    total_seconds: int,
    aspect: str,
    dialogue_mode: str,
    camera_pov: str,
) -> dict:
    """
    Bộ phân tích kịch bản dự phòng thông minh:
    Luôn bóc tách đủ 4 nhóm tham chiếu chuẩn:
    1) Nhân vật chính (<Picture 1>) — Sheet 3 góc (Trước + Sau + Góc nghiêng 3/4 khuôn mặt)
    2) Nhân vật phụ (<Picture 2>) — Sheet 3 góc (Trước + Sau + Góc nghiêng 3/4 khuôn mặt)
    3) Bối cảnh (<Picture 3>) — Prompt tạo bối cảnh không người
    4) Đạo cụ (<Picture 4>) — Prompt tạo đạo cụ / vũ khí tách nền
    """
    style_info = dict(STYLE_PRESETS.get(style_key, STYLE_PRESETS["xianxia_live"]))
    if custom_style:
        style_info["style_en"] = f"{custom_style.strip()}, {style_info['style_en']}"
        style_info["img_prefix_en"] = f"{custom_style.strip()}, {style_info['img_prefix_en']}"

    scene_count = max(1, min(12, int(round(total_seconds / max(4, scene_seconds)))))
    raw = (script_text or "").strip()
    lower = raw.lower()

    dialogues = [m.group(1).strip() for m in re.finditer(r'["“”]([^"“”]{3,140})["“”]', raw)]
    raw_lines = [
        ln.strip()
        for ln in re.split(r"(?:\r?\n)+|(?<=[.!?])\s+(?=[A-ZÀ-Ỹ\[])", raw)
        if ln.strip() and len(ln.strip()) > 4
    ]
    if not raw_lines:
        raw_lines = ["Nhân vật chính xuất hiện trong khung cảnh tuyệt đẹp, ánh sáng điện ảnh rực rỡ và chuyển động liền mạch."]

    # 1. Nhân vật chính (Sheet 3 góc: Trước + Sau + Mặt 3/4)
    if any(w in lower for w in ("lâm phong", "kiếm khách", "nam chính", " thiếu niên", "chàng trai")):
        main_name_vi = "Nhân Vật Chính - Kiếm Khách (<Picture 1>)"
        main_def_en = (
            "Handsome young martial arts swordsman with sharp剑眉 (sword eyebrows), piercing dark eyes, "
            "high ponytail black hair with silver crown, wearing black silk robe embroidered with silver cloud patterns."
        )
    elif any(w in lower for w in ("tiên nữ", "nữ hiệp", "cô gái", "thiếu nữ", "nàng", "lyra", "female", "girl", "woman")):
        main_name_vi = "Nữ Chính (<Picture 1>)"
        main_def_en = (
            "Breathtaking young woman with delicate expressive facial features, luminous eyes, "
            "long flowing hair with subtle ornaments, wearing an intricate flowing signature silk robe."
        )
    else:
        main_name_vi = "Nhân Vật Chính (<Picture 1>)"
        main_def_en = "Charismatic protagonist with sharp expressive eyes, distinct hairstyle, and detailed signature attire."

    # 2. Nhân vật phụ / Linh thú / Đối thủ (Sheet 3 góc: Trước + Sau + Mặt 3/4)
    if any(w in lower for w in ("rồng", "thanh long", "tiểu rồng", "linh thú", "hồ ly")):
        sub_name_vi = "Nhân Vật Phụ / Linh Thú (<Picture 2>)"
        sub_def_en = (
            "Mystical azure spirit dragon companion with luminous cyan scales, translucent spiritual horns, "
            "flowing energy whiskers, and ethereal aura."
        )
    else:
        sub_name_vi = "Nhân Vật Phụ / Đối Thủ (<Picture 2>)"
        sub_def_en = (
            "Supporting rival warrior in contrasting dark crimson and gold armor-trimmed robes, "
            "sharp determined gaze, braided dark hair, distinct silhouette."
        )

    # 3. Bối cảnh (Environment)
    if any(w in lower for w in ("núi tuyết", "tuyết vân", "đài đá", "mây")):
        env_name_vi = "Bối Cảnh: Đỉnh Núi Tuyết Vân & Đài Đá Cổ (<Picture 3>)"
        env_def_en = (
            "Ancient carved stone martial platform atop snowy Mount Tuyet Van surrounded by swirling sea of clouds, "
            "ancient runes on stone pillars, dramatic sunrise golden and cyan sky."
        )
    elif any(w in lower for w in ("rừng", "pha lê", "cầu ngọc", "tiên giới")):
        env_name_vi = "Bối Cảnh: Rừng Pha Lê & Cầu Ngọc Tiên Giới (<Picture 3>)"
        env_def_en = (
            "Bioluminescent crystal forest and glowing jade bridge floating above celestial clouds, "
            "volumetric god rays and drifting magical particles."
        )
    else:
        env_name_vi = "Bối Cảnh Chính Trong Kịch Bản (<Picture 3>)"
        env_def_en = (
            "Grand cinematic environment matching the screenplay setting, rich architectural and natural details, "
            "dramatic volumetric lighting and atmospheric depth."
        )

    # 4. Đạo cụ / Vũ khí (Prop)
    if any(w in lower for w in ("kiếm", "cổ kiếm", "đao", "thương")):
        prop_name_vi = "Đạo Cụ: Thanh Cổ Kiếm Phát Sáng Lam (<Picture 4>)"
        prop_def_en = (
            "Ancient celestial longsword with a translucent ice-blue jade blade, silver dragon-hilt guard, "
            "glowing cyan runes engraved along the fuller, and a dark blue tassel."
        )
    elif any(w in lower for w in ("trượng", "ngọc", "quạt", "sáo")):
        prop_name_vi = "Đạo Cụ: Pháp Trượng / Cổ Bảo Phát Sáng (<Picture 4>)"
        prop_def_en = (
            "Ornate celestial staff crowned with a levitating glowing crystal orb, filigree silver and gold carvings, "
            "emitting soft magical particles."
        )
    else:
        prop_name_vi = "Đạo Cụ Chính Trong Cảnh (<Picture 4>)"
        prop_def_en = (
            "Signature hero artifact and prop from the screenplay with intricate craftsmanship, "
            "distinctive silhouette, and subtle luminous details."
        )

    references = [
        {
            "slot": 1,
            "tag": "<Picture 1>",
            "category": "character_main",
            "name_vi": main_name_vi,
            "role_vi": "👤 Nhân vật chính (Sheet 3 góc)",
            "subject_def_en": main_def_en,
            "image_prompt_en": _build_3view_character_sheet_prompt(main_def_en, style_info),
        },
        {
            "slot": 2,
            "tag": "<Picture 2>",
            "category": "character_sub",
            "name_vi": sub_name_vi,
            "role_vi": "👥 Nhân vật phụ (Sheet 3 góc)",
            "subject_def_en": sub_def_en,
            "image_prompt_en": _build_3view_character_sheet_prompt(sub_def_en, style_info),
        },
        {
            "slot": 3,
            "tag": "<Picture 3>",
            "category": "environment",
            "name_vi": env_name_vi,
            "role_vi": "🏞️ Bối cảnh (Environment)",
            "subject_def_en": env_def_en,
            "image_prompt_en": _build_environment_prompt(env_def_en, style_info),
        },
        {
            "slot": 4,
            "tag": "<Picture 4>",
            "category": "prop",
            "name_vi": prop_name_vi,
            "role_vi": "🗡️ Đạo cụ / Vũ khí (Prop)",
            "subject_def_en": prop_def_en,
            "image_prompt_en": _build_prop_prompt(prop_def_en, style_info),
        },
    ]

    pov_prefix = (
        "First-person POV shot from the viewer's eye level, looking directly at <Picture 1> in <Picture 3>. "
        if camera_pov == "pov"
        else (
            "Seamless One-Take continuous tracking camera following <Picture 1> with <Picture 4> inside <Picture 3> without hard cuts. "
            if camera_pov == "onetake"
            else "Dynamic multi-angle cinematic camera framing <Picture 1> holding <Picture 4> inside the <Picture 3> environment. "
        )
    )

    scenes = []
    for idx in range(scene_count):
        s_num = idx + 1
        start_s = idx * scene_seconds
        end_s = (idx + 1) * scene_seconds
        line_vi = raw_lines[idx % len(raw_lines)]
        dlg_line = dialogues[idx] if idx < len(dialogues) else ""

        dlg_clause = ""
        if dialogue_mode == "vi" and dlg_line:
            dlg_clause = f' <Picture 1> looks with lifelike emotion and speaks with clear lip-sync: <d>In Vietnamese, "{dlg_line}"</d>.'
        elif dialogue_mode == "vi" and idx == 0 and "thoại" in lower:
            dlg_clause = ' <Picture 1> speaks with natural lip-sync: <d>In Vietnamese, "Hôm nay ta nhất định phá giải kiếm trận này!"</d>.'

        used_tags = ["<Picture 1>", "<Picture 3>", "<Picture 4>"]
        if idx > 0 or any(w in line_vi.lower() for w in ("rồng", "đối thủ", "hai", "cùng")):
            used_tags = ["<Picture 1>", "<Picture 2>", "<Picture 3>", "<Picture 4>"]

        half_s = max(2, scene_seconds // 2)
        shot_desc = (
            f"[Shot 1] (0s-{half_s}s) {pov_prefix}"
            + (f"Continuation from Scene {s_num - 1} in <Picture 3>. " if s_num > 1 else "Establishing <Picture 3> and atmosphere. ")
            + f"<Picture 1> wields <Picture 4> and performs natural, fluid motion matching the scene beat ({line_vi[:160]}), with realistic fabric, hair, and lighting interaction. "
            f"[Shot 2] ({half_s}s-{scene_seconds}s) Camera glides to a 3/4 medium close-up of <Picture 1>'s expressive face and <Picture 4>.{dlg_clause} "
            f"The motion settles naturally into a coherent pose at {scene_seconds}s for seamless transition."
        )

        h3_prompt = _ensure_h3_contract_format(
            raw_prompt=shot_desc,
            references=references,
            used_tags=used_tags,
            style_info=style_info,
            scene_idx=s_num,
            total_scenes=scene_count,
            start_sec=start_s,
            end_sec=end_s,
            scene_seconds=scene_seconds,
            summary_en=f"Scene {s_num} featuring {', '.join(used_tags)} in {style_info['name_vi']} style.",
            aspect=aspect,
        )

        scenes.append({
            "index": s_num,
            "start_sec": start_s,
            "end_sec": end_s,
            "duration_sec": scene_seconds,
            "time_range": f"{start_s:02d}s - {end_s:02d}s",
            "title_vi": f"Cảnh {s_num} ({start_s}s–{end_s}s)",
            "summary_vi": line_vi,
            "used_pictures": used_tags,
            "h3_prompt_en": h3_prompt,
        })

    return {
        "ok": True,
        "ai_source": "Bộ Biên Dịch Kịch Bản H3 Nội Bộ (Offline Compiler)",
        "style_key": style_key,
        "style_name_vi": style_info["name_vi"],
        "scene_seconds": scene_seconds,
        "total_seconds": scene_count * scene_seconds,
        "scene_count": scene_count,
        "aspect": aspect,
        "references": references,
        "scenes": scenes,
    }


def analyze_script_for_h3_pipeline(
    d: dict,
    cookie_cfg: dict | None = None,
    cookie_status_fn=None,
) -> dict:
    """
    Hàm trung tâm phân tích kịch bản qua Gemini Cookie / Gemini API -> trả về:
    - references: Danh sách <Picture 1..K> gồm đủ:
      1) Nhân vật chính (Sheet 3 góc: Trước + Sau + Góc nghiêng 3/4 khuôn mặt)
      2) Nhân vật phụ (Sheet 3 góc: Trước + Sau + Góc nghiêng 3/4 khuôn mặt)
      3) Bối cảnh (Prompt tạo bối cảnh không người)
      4) Đạo cụ / Vũ khí (Prompt tạo đạo cụ tách nền)
    - scenes: Danh sách Cảnh 1..N kèm Prompt chuẩn MiniMax H3.
    """
    d = d or {}
    cookie_cfg = cookie_cfg or {}
    script_text = str(d.get("script") or "").strip()
    if not script_text:
        script_text = "Trên đỉnh Tiên Giới mây ngũ sắc bồng bềnh, một tiên nữ áo lụa trắng bước trên cầu ngọc phát sáng, quay lại mỉm cười dịu dàng và cất tiếng chào bằng tiếng Việt."

    style_key = str(d.get("style_preset") or "xianxia_live").strip()
    custom_style = str(d.get("custom_style") or "").strip()
    style_info = dict(STYLE_PRESETS.get(style_key, STYLE_PRESETS["xianxia_live"]))
    if custom_style:
        style_info["style_en"] = f"{custom_style}, {style_info['style_en']}"
        style_info["img_prefix_en"] = f"{custom_style}, {style_info['img_prefix_en']}"

    scene_seconds = int(float(d.get("scene_seconds") or 10))
    scene_seconds = max(4, min(15, scene_seconds))
    total_seconds = int(float(d.get("total_seconds") or 60))
    total_seconds = max(scene_seconds, min(180, total_seconds))
    scene_count = max(1, min(12, int(round(total_seconds / scene_seconds))))
    eff_total_seconds = scene_count * scene_seconds

    aspect = str(d.get("aspect") or "16:9").strip()
    if aspect not in ("16:9", "9:16", "1:1"):
        aspect = "16:9"
    dialogue_mode = str(d.get("dialogue_mode") or "vi").strip().lower()
    camera_pov = str(d.get("camera_pov") or "cinematic").strip().lower()
    preferred_model = str(d.get("model") or cookie_cfg.get("default_model") or "gemini-2.5-flash").strip()
    api_key = str(d.get("api_key") or cookie_cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or "").strip()

    dlg_instruction = {
        "vi": 'If a character speaks in a scene, include natural Vietnamese lip-sync dialogue wrapped inside `<d>In Vietnamese, "Exact Vietnamese spoken line..."</d>` inside `detailed_description`.',
        "en": 'If a character speaks in a scene, include natural English lip-sync dialogue wrapped inside `<d>In English, "Exact English spoken line..."</d>` inside `detailed_description`.',
        "none": "Do NOT include spoken dialogue `<d>...</d>`; focus purely on visual storytelling and environmental sound effects.",
    }.get(dialogue_mode, "")

    cam_instruction = {
        "pov": "Use First-Person POV camera framing (viewer's eye perspective with subtle camera breathing and interactive foreground hands when appropriate).",
        "onetake": "Use seamless One-Take continuous tracking camera movement across all scenes so Scene K+1 continues directly from the final pose and camera vector of Scene K.",
        "cinematic": "Use expressive cinematic camera blocking (establishing wide, tracking medium, emotional close-up) while keeping strict spatial and lighting continuity between consecutive scenes.",
    }.get(camera_pov, "")

    half_s = max(2, scene_seconds // 2)
    master_prompt = f"""You are the Master AI Screenplay Analyzer & Visual Bible Prompt Compiler for MiniMax H3 Video Studio.

Analyze the user's screenplay/idea and compile a complete Visual Reference Bible (`references`) + Sequential H3 Scene Prompts (`scenes`).

CRITICAL REQUIREMENT FOR `references` (MUST EXTRACT ALL 4 CATEGORIES FROM THE SCREENPLAY, up to 8 items total):
1. **Main Character(s) (`category: "character_main"`)**:
   - `role_vi`: `"👤 Nhân vật chính (Sheet 3 góc)"`
   - `image_prompt_en` MUST be a **3-Panel Character Turnaround Reference Sheet** in `{style_info['style_en']}`:
     * **Panel 1 (Left)**: Full-body FRONT view standing in neutral A-pose showing complete front outfit, proportions, and footwear.
     * **Panel 2 (Center)**: Full-body BACK view showing rear costume design, hair length from behind, and cape/robe back details.
     * **Panel 3 (Right)**: High-resolution CLOSE-UP **3/4 THREE-QUARTER ANGLE FACIAL PORTRAIT** showing exact facial bone structure, eyes, nose bridge, lips, expression, and hair strands.
     * Clean neutral studio background, consistent lighting across all 3 views, 8K, no text/watermark.
2. **Supporting Character(s) / Creatures / Rivals (`category: "character_sub"`)**:
   - Extract any supporting character, rival, spirit beast/dragon, or companion mentioned or implied in the screenplay.
   - `role_vi`: `"👥 Nhân vật phụ (Sheet 3 góc)"`
   - `image_prompt_en` MUST ALSO be a **3-Panel Turnaround Reference Sheet (Full-body Front view + Full-body Back view + Close-up 3/4 Three-Quarter Angle Face/Head Portrait)** in `{style_info['style_en']}` on a clean neutral studio background.
3. **Environment / Location (`category: "environment"`)**:
   - Extract the key environment(s) / setting(s) of the screenplay (e.g. snowy mountain peak, ancient stone altar, crystal forest).
   - `role_vi`: `"🏞️ Bối cảnh (Environment)"`
   - `image_prompt_en` MUST be an ultra-detailed **Wide-Angle Establishing Environment Reference Prompt** in `{style_info['style_en']}` (architectural & natural details, volumetric lighting, atmospheric depth, **empty environment plate with NO characters or people in frame**).
4. **Key Prop(s) / Weapons / Artifacts (`category: "prop"`)**:
   - Extract the key weapon, sword, talisman, or prop used in the screenplay.
   - `role_vi`: `"🗡️ Đạo cụ / Vũ khí (Prop)"`
   - `image_prompt_en` MUST be a **Hero Prop Reference Sheet Prompt** in `{style_info['style_en']}` showing front view and 3/4 angled detail view of the object, intricate textures/engravings/glow, **isolated on a clean neutral dark studio background with NO hands or people holding it**.

Each item in `references` MUST have:
- `slot`: integer `1..K`
- `tag`: `"<Picture 1>"`, `"<Picture 2>"`, etc.
- `category`: one of `"character_main"`, `"character_sub"`, `"environment"`, `"prop"`
- `name_vi`: Clear Vietnamese name & tag (e.g. `"Lâm Phong - Kiếm Khách Hắc Bào (<Picture 1>)"`, `"Cổ Kiếm Lam Quang (<Picture 4>)"`)
- `role_vi`: Vietnamese category badge (`"👤 Nhân vật chính (Sheet 3 góc)"`, `"👥 Nhân vật phụ (Sheet 3 góc)"`, `"🏞️ Bối cảnh (Environment)"`, `"🗡️ Đạo cụ / Vũ khí (Prop)"`)
- `subject_def_en`: Concise English identity lock for H3's `subject_definitions:` section
- `image_prompt_en`: Complete English Text-to-Image prompt following the exact rules above!

REQUIREMENT FOR `scenes`:
Break the screenplay into EXACTLY **{scene_count} sequential scenes**, where EACH scene is **{scene_seconds} seconds** long (Total duration = **{eff_total_seconds} seconds**).
- Visual Style: `{style_info['style_en']}`
- Camera Style: {cam_instruction}
- Dialogue Rule: {dlg_instruction}
- Reference Binding: In each scene, reference the relevant `<Picture k>` tags (main character, supporting character, environment, prop) in `used_pictures`, `subject_definitions:`, and `detailed_description:`.
- For each scene `1..{scene_count}`, return:
  * `index`: integer `1..{scene_count}`
  * `title_vi`: Short Vietnamese title for this scene
  * `summary_vi`: 1-2 sentence Vietnamese summary of the action, camera, and dialogue
  * `used_pictures`: array of active tags in this scene, e.g. `["<Picture 1>", "<Picture 3>", "<Picture 4>"]`
  * `h3_prompt_en`: COMPLETE English prompt following the official **MiniMax H3 Reference Contract** format (all 6 sections: `subject_definitions:`, `summary:`, `retention_analysis:`, `detailed_description: [Shot 1] (0s-{half_s}s)... [Shot 2] ({half_s}s-{scene_seconds}s)...`, `overall_soundscape:`, `non_diegetic_music:`).

Return ONLY a valid JSON object matching this exact structure:
{{
  "references": [
    {{
      "slot": 1,
      "tag": "<Picture 1>",
      "category": "character_main",
      "name_vi": "Tên nhân vật chính (<Picture 1>)",
      "role_vi": "👤 Nhân vật chính (Sheet 3 góc)",
      "subject_def_en": "Detailed English appearance lock...",
      "image_prompt_en": "3-Panel Character Turnaround Reference Sheet... [Panel 1 - Left: Full-body FRONT view...], [Panel 2 - Center: Full-body BACK view...], [Panel 3 - Right: Close-up 3/4 THREE-QUARTER ANGLE FACIAL PORTRAIT...]..."
    }}
  ],
  "scenes": [
    {{
      "index": 1,
      "title_vi": "Tiêu đề Cảnh 1",
      "summary_vi": "Tóm tắt tiếng Việt của Cảnh 1...",
      "used_pictures": ["<Picture 1>", "<Picture 3>", "<Picture 4>"],
      "h3_prompt_en": "subject_definitions:\\n- <Picture 1>: ...\\n\\nsummary: [reference generation] ...\\n\\nretention_analysis: ...\\n\\ndetailed_description: [Shot 1] ... [Shot 2] ...\\n\\noverall_soundscape: ...\\n\\nnon_diegetic_music: ..."
    }}
  ]
}}

=== USER SCREENPLAY / IDEA ===
{script_text}
"""

    parsed = None
    ai_source = ""

    # 1. Gọi qua Gemini Cookie API nội bộ (127.0.0.1:4981/openai/v1)
    if callable(cookie_status_fn):
        try:
            st = cookie_status_fn(auto_start=True)
            if st and st.get("connected"):
                api_url = str(st.get("api_url") or cookie_cfg.get("api_url") or "http://127.0.0.1:4981/openai/v1").rstrip("/")
                req_m = str(d.get("model") or cookie_cfg.get("default_model") or "gemini-3.1-pro").strip()
                valid_cookie_models = ("gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.1-pro", "gemini-4-argon")
                cookie_model = req_m if req_m in valid_cookie_models else "gemini-3.1-pro"
                payload = {
                    "model": cookie_model,
                    "messages": [{"role": "user", "content": master_prompt}],
                    "stream": False,
                }
                req = urllib.request.Request(
                    f"{api_url}/chat/completions",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    resp_j = json.loads(resp.read().decode("utf-8", errors="ignore"))
                raw_reply = resp_j["choices"][0]["message"]["content"]
                parsed = _extract_json_obj(raw_reply)
                if isinstance(parsed, dict):
                    ai_source = f"Gemini Cookie API ({cookie_model})"
        except Exception:
            parsed = None

    # 2. Dự phòng qua API Key nếu có
    if not parsed and api_key:
        parsed, src_msg = call_google_gemini_api_key(master_prompt, api_key, preferred_model=preferred_model)
        if parsed:
            ai_source = src_msg

    # 3. Bộ biên dịch nội bộ nếu chưa có phản hồi từ Gemini
    fallback_bundle = _smart_local_script_compiler(
        script_text=script_text,
        style_key=style_key,
        custom_style=custom_style,
        scene_seconds=scene_seconds,
        total_seconds=eff_total_seconds,
        aspect=aspect,
        dialogue_mode=dialogue_mode,
        camera_pov=camera_pov,
    )
    if not isinstance(parsed, dict) or not parsed.get("scenes"):
        return fallback_bundle

    # Chuẩn hóa `references` và đảm bảo luôn có đủ 4 nhóm: Nhân vật chính (Sheet 3 góc), Nhân vật phụ (Sheet 3 góc), Bối cảnh, Đạo cụ
    raw_refs = parsed.get("references") or parsed.get("cast") or []
    references = []
    seen_categories = set()

    for idx, r in enumerate(raw_refs[:9]):
        if not isinstance(r, dict):
            continue
        slot_num = len(references) + 1
        tag = f"<Picture {slot_num}>"
        name_vi = str(r.get("name_vi") or f"Tham Chiếu #{slot_num} ({tag})").strip()
        if "<Picture" not in name_vi:
            name_vi = f"{name_vi} ({tag})"
        cat_key, role_label = _normalize_ref_category(
            role_vi=str(r.get("role_vi") or r.get("role") or ""),
            category_raw=str(r.get("category") or ""),
            name_vi=name_vi,
        )
        seen_categories.add(cat_key)
        subj_def = str(r.get("subject_def_en") or r.get("desc") or "Consistent visual reference subject.").strip()
        raw_img_p = str(r.get("image_prompt_en") or r.get("image_prompt") or "").strip()

        if cat_key in ("character_main", "character_sub"):
            img_p = _build_3view_character_sheet_prompt(subj_def, style_info, existing_prompt=raw_img_p)
        elif cat_key == "environment":
            img_p = _build_environment_prompt(subj_def, style_info, existing_prompt=raw_img_p)
        else:
            img_p = _build_prop_prompt(subj_def, style_info, existing_prompt=raw_img_p)

        references.append({
            "slot": slot_num,
            "tag": tag,
            "category": cat_key,
            "name_vi": name_vi,
            "role_vi": role_label,
            "subject_def_en": subj_def,
            "image_prompt_en": img_p,
        })

    # Bổ sung tự động nếu AI bỏ sót bất kỳ nhóm nào trong 4 nhóm (character_main, character_sub, environment, prop)
    for fb_ref in fallback_bundle["references"]:
        fb_cat = fb_ref.get("category")
        if fb_cat not in seen_categories and len(references) < 9:
            slot_num = len(references) + 1
            tag = f"<Picture {slot_num}>"
            nm = re.sub(r"\(<Picture \d+>\)", f"({tag})", fb_ref["name_vi"])
            references.append({
                "slot": slot_num,
                "tag": tag,
                "category": fb_cat,
                "name_vi": nm,
                "role_vi": fb_ref["role_vi"],
                "subject_def_en": fb_ref["subject_def_en"],
                "image_prompt_en": fb_ref["image_prompt_en"],
            })
            seen_categories.add(fb_cat)

    # Chuẩn hóa `scenes`
    raw_scenes = parsed.get("scenes") or parsed.get("shots") or []
    valid_tags = {r["tag"] for r in references}
    default_scene_tags = [r["tag"] for r in references if r.get("category") in ("character_main", "environment", "prop")] or [references[0]["tag"]]

    scenes = []
    for idx in range(scene_count):
        s_num = idx + 1
        start_s = idx * scene_seconds
        end_s = (idx + 1) * scene_seconds
        sc = raw_scenes[idx] if idx < len(raw_scenes) and isinstance(raw_scenes[idx], dict) else (raw_scenes[-1] if raw_scenes and isinstance(raw_scenes[-1], dict) else {})
        title_vi = str(sc.get("title_vi") or f"Cảnh {s_num}").strip()
        summary_vi = str(sc.get("summary_vi") or sc.get("note_vi") or f"Phân cảnh {s_num} ({start_s}s–{end_s}s)").strip()
        used_pics = sc.get("used_pictures")
        if isinstance(used_pics, list):
            used_pics = [t for t in used_pics if t in valid_tags]
        if not used_pics:
            used_pics = list(default_scene_tags)

        raw_h3 = str(sc.get("h3_prompt_en") or sc.get("text") or sc.get("prompt") or "").strip()
        full_h3 = _ensure_h3_contract_format(
            raw_prompt=raw_h3,
            references=references,
            used_tags=used_pics,
            style_info=style_info,
            scene_idx=s_num,
            total_scenes=scene_count,
            start_sec=start_s,
            end_sec=end_s,
            scene_seconds=scene_seconds,
            summary_en=summary_vi,
            aspect=aspect,
        )
        scenes.append({
            "index": s_num,
            "start_sec": start_s,
            "end_sec": end_s,
            "duration_sec": scene_seconds,
            "time_range": f"{start_s:02d}s - {end_s:02d}s",
            "title_vi": title_vi,
            "summary_vi": summary_vi,
            "used_pictures": used_pics,
            "h3_prompt_en": full_h3,
        })

    return {
        "ok": True,
        "ai_source": ai_source or "Google Gemini AI",
        "style_key": style_key,
        "style_name_vi": style_info["name_vi"],
        "scene_seconds": scene_seconds,
        "total_seconds": eff_total_seconds,
        "scene_count": scene_count,
        "aspect": aspect,
        "references": references,
        "scenes": scenes,
    }
