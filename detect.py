import os
import io
import json
import base64
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE_DIR, 'calorie_map.json'), 'r') as _f:
    calorie_map = json.load(_f)

INDIAN_FOOD_LABELS = list(calorie_map.keys())

# Lazy-loaded ML components — only available in the full local install
_yolo_model = None
_nlp = None
_clip_model = None
_clip_processor = None
_clip_available = None


def _get_yolo():
    global _yolo_model
    if _yolo_model is None:
        from ultralytics import YOLO
        model_path = os.environ.get(
            'YOLO_MODEL_PATH',
            os.path.join(BASE_DIR, 'runs', 'detect', 'train2', 'weights', 'best.pt')
        )
        _yolo_model = YOLO(model_path)
    return _yolo_model


def _get_nlp():
    global _nlp
    if _nlp is None:
        try:
            import spacy
            _nlp = spacy.load("en_core_web_sm")
        except Exception:
            pass
    return _nlp


def _try_clip(image_path):
    global _clip_model, _clip_processor, _clip_available
    if _clip_available is False:
        return None
    try:
        if _clip_model is None:
            from transformers import CLIPProcessor, CLIPModel
            _clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
            _clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
            _clip_available = True
        from PIL import Image as PILImage
        import torch
        image = PILImage.open(image_path).convert("RGB")
        text_labels = [f"a photo of {lbl.replace('_', ' ')}" for lbl in INDIAN_FOOD_LABELS]
        inputs = _clip_processor(text=text_labels, images=image, return_tensors="pt", padding=True)
        with torch.no_grad():
            outputs = _clip_model(**inputs)
        probs = outputs.logits_per_image.softmax(dim=1)[0]
        top_idx = int(probs.argmax())
        confidence = float(probs[top_idx])
        if confidence >= 0.12:
            return INDIAN_FOOD_LABELS[top_idx], confidence
    except Exception as e:
        _clip_available = False
        print(f"CLIP unavailable: {e}")
    return None


def _search_description(description):
    """Match food names in description text against the calorie map."""
    desc = description.lower().replace('-', ' ').replace('_', ' ')
    for label in INDIAN_FOOD_LABELS:
        if label.replace('_', ' ') in desc:
            return label
    return None


def _pil_encode(image_path):
    """Encode image to base64 JPEG using Pillow (no cv2 needed)."""
    try:
        from PIL import Image as PILImage
        img = PILImage.open(image_path).convert('RGB')
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=80)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def detect_variant(label, description):
    description = description.lower()
    entry = calorie_map.get(label, {})
    if isinstance(entry, dict):
        variants = entry.get("variants", {})
        for variant in variants:
            if variant.replace("_", " ") in description or variant in description:
                return variants[variant], variant
        return entry.get("base", {}), None
    return entry, None


def get_adjustments(description):
    nlp = _get_nlp()
    adjustments = []
    if nlp:
        doc = nlp(description.lower())
        for token in doc:
            if token.text in ["butter", "ghee", "oil", "sugar"]:
                negated = any(
                    tok.dep_ in ["neg", "det"] and tok.text in ["no", "not", "without"]
                    for tok in list(token.children) + list(token.ancestors)
                )
                if negated:
                    adjustments.append((token.text, -0.3, f"no {token.text}"))
                else:
                    mod, reason = 0.15, f"uses {token.text}"
                    for child in token.children:
                        if child.text in ["extra", "lots", "more"]:
                            mod, reason = 0.3, f"extra {token.text}"
                            break
                        elif child.text in ["less", "little", "light"]:
                            mod, reason = -0.1, f"less {token.text}"
                            break
                    adjustments.append((token.text, mod, reason))

    mapping = {
        "hotel": (0.15, "hotel-style"), "restaurant": (0.15, "restaurant-style"),
        "homemade": (-0.1, "homemade"), "home": (-0.1, "homemade"),
        "boiled": (-0.15, "boiled"), "baked": (-0.15, "baked"),
        "air fried": (-0.2, "air-fried"), "deep fried": (0.25, "deep-fried"),
    }
    for keyword, (mod, reason) in mapping.items():
        if keyword in description.lower():
            adjustments.append((keyword, mod, reason))
    return adjustments


def apply_adjustments(nutrition, description):
    adjustments = get_adjustments(description)
    factor = 1.0
    reasons = []
    for _, mod, reason in adjustments:
        factor += mod
        reasons.append(reason)
    factor = max(0.5, min(factor, 1.5))
    return {k: round(v * factor, 2) for k, v in nutrition.items()}, factor, reasons


def detect_and_calculate(image_path, description=""):
    """
    Returns (image_b64_or_none, detected_items, total_nutrition, explanations).

    When the ML stack (cv2 / ultralytics / torch) is not installed — as is the
    case on Vercel — falls back to matching food names in the description text.
    The image is returned unmodified (no bounding-box annotations).
    """
    label_counts = defaultdict(int)
    total = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0, "fiber": 0}
    explanations = []
    detected_items = []
    image_b64 = None
    ml_source = "Text Match"

    # Try full ML pipeline (only works when cv2 / ultralytics / torch are installed)
    try:
        import cv2  # noqa: PLC0415

        image = cv2.imread(image_path)
        if image is not None:
            # YOLO detection
            try:
                model = _get_yolo()
                results = model(image_path, conf=0.25)[0]
                for box in results.boxes:
                    cls_id = int(box.cls[0])
                    conf = float(box.conf[0])
                    label = results.names[cls_id]
                    label_counts[label] += 1
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    cv2.rectangle(image, (x1, y1), (x2, y2), (0, 107, 255), 3)
                    cv2.putText(
                        image,
                        f"{label.replace('_', ' ').title()} {conf:.0%}",
                        (x1, max(y1 - 12, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 107, 255), 2,
                    )
                ml_source = "Custom Model"
            except Exception as e:
                print(f"YOLO not available: {e}")

            # CLIP fallback
            if not label_counts:
                clip_result = _try_clip(image_path)
                if clip_result:
                    label, conf = clip_result
                    label_counts[label] = 1
                    h, w = image.shape[:2]
                    cv2.rectangle(image, (10, 10), (w - 10, h - 10), (53, 200, 100), 3)
                    ml_source = "AI Vision"

            # Encode annotated image
            from PIL import Image as PILImage
            image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            pil_img = PILImage.fromarray(image_rgb)
            buf = io.BytesIO()
            pil_img.save(buf, format='JPEG', quality=80)
            image_b64 = base64.b64encode(buf.getvalue()).decode()

    except ImportError:
        # cv2 not installed — encode original image with PIL only
        image_b64 = _pil_encode(image_path)

    # If no visual detection, try matching the description
    if not label_counts and description:
        matched = _search_description(description)
        if matched:
            label_counts[matched] = 1

    # Calculate nutrition for each detected label
    for label, count in label_counts.items():
        base_nutrition, _ = detect_variant(label, description)
        if isinstance(base_nutrition, (int, float)):
            base_nutrition = {"calories": base_nutrition, "protein": 0,
                              "fat": 0, "carbs": 0, "fiber": 0}
        if not base_nutrition:
            base_nutrition = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0, "fiber": 0}

        adj, factor, reasons = apply_adjustments(base_nutrition, description)

        for k in total:
            total[k] += round(adj.get(k, 0) * count, 2)

        name = label.replace('_', ' ').title()
        detected_items.append(f"{name} x{count} ({adj['calories'] * count:.0f} kcal)")
        reason_text = ", ".join(reasons) if reasons else "standard portion"
        explanations.append(
            f"**{name}** (x{count}) via *{ml_source}*: "
            f"Adjusted x{factor:.2f} for {reason_text}. "
            f"Total: **{adj['calories'] * count:.0f} kcal**"
        )

    return image_b64, detected_items, total, explanations
