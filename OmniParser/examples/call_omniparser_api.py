import argparse
import base64
import json
from datetime import datetime
from pathlib import Path

import requests


def encode_image(path: Path) -> str:
    with path.open("rb") as file:
        return base64.b64encode(file.read()).decode("utf-8")


def main():
    parser = argparse.ArgumentParser(description="Call the OmniParser FastAPI /parse/ endpoint.")
    parser.add_argument("image", type=Path, help="Path to the input image.")
    parser.add_argument("--url", default="http://127.0.0.1:8000/parse/", help="OmniParser /parse/ URL.")
    parser.add_argument("--results-dir", type=Path, default=Path("results"), help="Directory for saved outputs.")
    parser.add_argument("--timeout", type=float, default=300, help="Request timeout in seconds.")
    parser.add_argument("--box-threshold", type=float, default=None, help="Icon detection confidence threshold.")
    parser.add_argument("--iou-threshold", type=float, default=0.7, help="Overlap filtering threshold.")
    parser.add_argument("--use-paddleocr", action="store_true", help="Use PaddleOCR instead of EasyOCR.")
    parser.add_argument("--imgsz", type=int, default=None, help="YOLO icon detection image size.")
    args = parser.parse_args()

    if not args.image.exists():
        raise FileNotFoundError(args.image)

    request_payload = {
        "base64_image": encode_image(args.image),
        "iou_threshold": args.iou_threshold,
        "use_paddleocr": args.use_paddleocr,
    }
    if args.box_threshold is not None:
        request_payload["box_threshold"] = args.box_threshold
    if args.imgsz is not None:
        request_payload["imgsz"] = args.imgsz

    response = requests.post(args.url, json=request_payload, timeout=args.timeout)
    response.raise_for_status()
    payload = response.json()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    image_output_dir = args.results_dir / "imageOutput"
    parsed_output_dir = args.results_dir / "Parsed screen elements"
    image_output_dir.mkdir(parents=True, exist_ok=True)
    parsed_output_dir.mkdir(parents=True, exist_ok=True)

    image_output_path = image_output_dir / f"{timestamp}.png"
    parsed_output_path = parsed_output_dir / f"{timestamp}.json"

    image_output_path.write_bytes(base64.b64decode(payload["som_image_base64"]))
    parsed_output_path.write_text(
        json.dumps(
            {
                "input_image": str(args.image),
                "latency": payload.get("latency"),
                "parsed_content_list": payload["parsed_content_list"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"latency: {payload.get('latency')}")
    print(f"image_output: {image_output_path}")
    print(f"parsed_output: {parsed_output_path}")
    print(f"parsed_items: {len(payload['parsed_content_list'])}")


if __name__ == "__main__":
    main()
