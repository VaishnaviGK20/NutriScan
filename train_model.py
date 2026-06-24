"""
train_model.py — Expand and retrain the YOLOv8 model on more Indian foods.

Usage:
    python train_model.py

Requirements:
    pip install ultralytics roboflow

Steps this script performs:
  1. Downloads a larger Indian food dataset from Roboflow (requires a free API key).
  2. Merges it with the existing local dataset.
  3. Trains YOLOv8n (nano) for speed on mobile, or YOLOv8s for accuracy.
  4. Saves the best model to runs/detect/train_expanded/weights/best.pt
  5. Updates .env to point YOLO_MODEL_PATH at the new model.
"""

import os
import sys
import yaml
import shutil
from pathlib import Path

BASE = Path(__file__).parent

# ── Configuration ─────────────────────────────────────────────────────────────

ROBOFLOW_API_KEY = os.environ.get("ROBOFLOW_API_KEY", "")
"""
To get a free Roboflow API key:
  1. Go to app.roboflow.com and sign up (free)
  2. Go to Account Settings → Roboflow API Key
  3. Set it: set ROBOFLOW_API_KEY=your_key  (Windows)
     or export ROBOFLOW_API_KEY=your_key   (Linux/Mac)
"""

# Roboflow datasets to download (workspace/project/version)
DATASETS = [
    ("indianfood",        "indian_food-pwzlc",          2),  # existing
    ("indian-food-2hgwf", "indian-food-dataset",         1),  # broader dataset
    ("vikas-rajak",       "indian-food-dishes",          1),
]

# Model to finetune: yolov8n = fast/mobile, yolov8s = balanced, yolov8m = accurate
BASE_MODEL   = "yolov8s.pt"
EPOCHS       = 80
IMAGE_SIZE   = 640
OUTPUT_NAME  = "train_expanded"

# ── Download datasets ──────────────────────────────────────────────────────────

def download_datasets():
    try:
        from roboflow import Roboflow
    except ImportError:
        print("❌ Install roboflow:  pip install roboflow")
        sys.exit(1)

    if not ROBOFLOW_API_KEY:
        print("❌ Set ROBOFLOW_API_KEY environment variable.")
        print("   Get a free key at: app.roboflow.com → Account Settings")
        sys.exit(1)

    rf = Roboflow(api_key=ROBOFLOW_API_KEY)
    download_dir = BASE / "datasets"
    download_dir.mkdir(exist_ok=True)

    all_data_dirs = []
    for workspace, project, version in DATASETS:
        print(f"⬇  Downloading {workspace}/{project} v{version}…")
        try:
            proj = rf.workspace(workspace).project(project)
            ds = proj.version(version).download("yolov8", location=str(download_dir / project))
            all_data_dirs.append(Path(ds.location))
            print(f"   ✅ {ds.location}")
        except Exception as e:
            print(f"   ⚠ Could not download {project}: {e}")

    return all_data_dirs


def merge_datasets(data_dirs):
    """Merge multiple YOLOv8 datasets into one combined dataset."""
    merged_dir = BASE / "datasets" / "merged"
    for split in ("train", "valid", "test"):
        (merged_dir / split / "images").mkdir(parents=True, exist_ok=True)
        (merged_dir / split / "labels").mkdir(parents=True, exist_ok=True)

    all_classes = []
    class_offsets = {}

    # collect all unique class names
    for d in data_dirs:
        yaml_file = next(d.glob("*.yaml"), None) or next(d.glob("data.yaml"), None)
        if yaml_file:
            with open(yaml_file) as f:
                meta = yaml.safe_load(f)
            for cls in meta.get("names", []):
                if cls not in all_classes:
                    all_classes.append(cls)

    # Also include local classes
    local_yaml = BASE / "data.yaml"
    if local_yaml.exists():
        with open(local_yaml) as f:
            meta = yaml.safe_load(f)
        for cls in meta.get("names", []):
            if cls not in all_classes:
                all_classes.append(cls)

    print(f"\n📦 Total classes after merge: {len(all_classes)}")
    for i, c in enumerate(all_classes):
        print(f"  {i:3d}: {c}")

    # Copy images and remap label indices
    for d in data_dirs:
        yaml_file = next(d.glob("*.yaml"), None) or d / "data.yaml"
        if not yaml_file.exists():
            continue
        with open(yaml_file) as f:
            meta = yaml.safe_load(f)
        src_names = meta.get("names", [])
        remap = {i: all_classes.index(n) for i, n in enumerate(src_names) if n in all_classes}

        for split in ("train", "valid", "test"):
            img_dir = d / split / "images"
            lbl_dir = d / split / "labels"
            if not img_dir.exists():
                continue
            for img in img_dir.iterdir():
                shutil.copy2(img, merged_dir / split / "images" / img.name)
                lbl = lbl_dir / (img.stem + ".txt")
                if lbl.exists():
                    dst_lbl = merged_dir / split / "labels" / lbl.name
                    with open(lbl) as f:
                        lines = f.readlines()
                    with open(dst_lbl, "w") as f:
                        for line in lines:
                            parts = line.strip().split()
                            if parts:
                                old_cls = int(parts[0])
                                new_cls = remap.get(old_cls, old_cls)
                                f.write(f"{new_cls} " + " ".join(parts[1:]) + "\n")

    # Also copy the local dataset
    for split in ("train", "valid", "test"):
        local_img = BASE / split / "images"
        local_lbl = BASE / split / "labels"
        if local_img.exists():
            for img in local_img.iterdir():
                shutil.copy2(img, merged_dir / split / "images" / img.name)
        if local_lbl.exists():
            for lbl in local_lbl.iterdir():
                shutil.copy2(lbl, merged_dir / split / "labels" / lbl.name)

    # Write merged data.yaml
    merged_yaml = merged_dir / "data.yaml"
    with open(merged_yaml, "w") as f:
        yaml.dump({
            "train": str(merged_dir / "train" / "images"),
            "val":   str(merged_dir / "valid" / "images"),
            "test":  str(merged_dir / "test"  / "images"),
            "nc":    len(all_classes),
            "names": all_classes,
        }, f, default_flow_style=False, allow_unicode=True)

    print(f"\n✅ Merged dataset at: {merged_dir}")
    return merged_yaml, len(all_classes)


def train(data_yaml, num_classes):
    from ultralytics import YOLO

    print(f"\n🚀 Starting training: {BASE_MODEL} | {num_classes} classes | {EPOCHS} epochs")
    model = YOLO(BASE_MODEL)
    results = model.train(
        data=str(data_yaml),
        epochs=EPOCHS,
        imgsz=IMAGE_SIZE,
        name=OUTPUT_NAME,
        patience=15,
        batch=16,
        device="0" if _has_gpu() else "cpu",
        workers=4,
        amp=True,
    )
    best = Path("runs") / "detect" / OUTPUT_NAME / "weights" / "best.pt"
    if best.exists():
        print(f"\n✅ Training complete! Best model: {best}")
        _update_env(str(best))
    return best


def _has_gpu():
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def _update_env(model_path):
    env_file = BASE / ".env"
    if not env_file.exists():
        shutil.copy(BASE / ".env.example", env_file)

    text = env_file.read_text()
    if "YOLO_MODEL_PATH=" in text:
        lines = [f"YOLO_MODEL_PATH={model_path}" if l.startswith("YOLO_MODEL_PATH=")
                 else l for l in text.splitlines()]
        env_file.write_text("\n".join(lines))
    else:
        with open(env_file, "a") as f:
            f.write(f"\nYOLO_MODEL_PATH={model_path}\n")
    print(f"📝 .env updated: YOLO_MODEL_PATH={model_path}")


if __name__ == "__main__":
    print("=" * 60)
    print("  NutriScan India — Model Training Script")
    print("=" * 60)

    data_dirs = download_datasets()

    if data_dirs:
        data_yaml, nc = merge_datasets(data_dirs)
    else:
        print("\n⚠  No new datasets downloaded. Training on local data only.")
        data_yaml = BASE / "data.yaml"
        with open(data_yaml) as f:
            nc = yaml.safe_load(f).get("nc", 7)

    train(data_yaml, nc)
