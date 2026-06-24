import os
import cv2
import json
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_yolo_model = None
_nlp = None
_clip_model = None
_clip_processor = None
_clip_available = None

with open(os.path.join(BASE_DIR, 'calorie_map.json'), 'r') as _f:
    calorie_map = json.load(_f)

# All Indian food labels for CLIP zero-shot classification
INDIAN_FOOD_LABELS = list(calorie_map.keys())

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
        import spacy
        _nlp = spacy.load("en_core_web_sm")
    return _nlp

def _try_clip(image_path):
    """Zero-shot food classification via CLIP. Returns (label, confidence) or None."""
    global _clip_model, _clip_processor, _clip_available
    if _clip_available is False:
        return None
    try:
        if _clip_model is None:
            from transformers import CLIPProcessor, CLIPModel
            from PIL import Image as PILImage
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
    doc = nlp(description.lower())
    adjustments = []
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
        "air fried": (-0.2, "air-fried"), "air_fried": (-0.2, "air-fried"),
        "deep fried": (0.25, "deep-fried"), "deep fry": (0.25, "deep-fried"),
    }
    for keyword, (mod, reason) in mapping.items():
        if keyword in description:
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
    return {
        k: round(v * factor, 2) for k, v in nutrition.items()
    }, factor, reasons


def detect_and_calculate(image_path, description=""):
    image = cv2.imread(image_path)
    label_counts = defaultdict(int)
    total = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0, "fiber": 0}
    explanations = []
    detected_items = []
    clip_used = False

    # Primary: YOLO custom model
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
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 107, 255), 2
            )
    except Exception as e:
        print(f"YOLO detection error: {e}")

    # Fallback: CLIP zero-shot if YOLO found nothing
    if not label_counts:
        clip_result = _try_clip(image_path)
        if clip_result:
            label, conf = clip_result
            label_counts[label] = 1
            clip_used = True
            h, w = image.shape[:2]
            cv2.rectangle(image, (10, 10), (w - 10, h - 10), (53, 200, 100), 3)
            cv2.putText(
                image,
                f"AI: {label.replace('_', ' ').title()} {conf:.0%}",
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (53, 200, 100), 2
            )

    # Calculate nutrition for each detected label
    for label, count in label_counts.items():
        base_nutrition, variant = detect_variant(label, description)
        if isinstance(base_nutrition, (int, float)):
            base_nutrition = {"calories": base_nutrition, "protein": 0,
                              "fat": 0, "carbs": 0, "fiber": 0}
        if not base_nutrition:
            base_nutrition = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0, "fiber": 0}

        adj, factor, reasons = apply_adjustments(base_nutrition, description)

        for k in total:
            total[k] += round(adj.get(k, 0) * count, 2)

        name = label.replace('_', ' ').title()
        detected_items.append(f"{name} × {count} ({adj['calories'] * count:.0f} kcal)")
        source = "AI Vision (CLIP)" if clip_used else "Custom Model"
        reason_text = ", ".join(reasons) if reasons else "standard portion"
        explanations.append(
            f"**{name}** (×{count}) via *{source}*: "
            f"Adjusted ×{factor:.2f} for {reason_text}. "
            f"Total: **{adj['calories'] * count:.0f} kcal**"
        )

    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return image_rgb, detected_items, total, explanations
