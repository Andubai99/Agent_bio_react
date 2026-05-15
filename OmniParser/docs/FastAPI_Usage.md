# OmniParser FastAPI Usage

This document describes how to run OmniParser as a local FastAPI service and call it from another project.

## 1. Start The Service

Run this in a PowerShell window and keep it open:

```powershell
cd F:\OmniParser\omnitool\omniparserserver

F:\OmniParser\.venv\Scripts\python.exe .\omniparserserver.py `
  --som_model_path F:\OmniParser\weights\icon_detect\model.pt `
  --caption_model_name florence2 `
  --caption_model_path F:\OmniParser\weights\icon_caption_florence `
  --device cuda `
  --BOX_TRESHOLD 0.05 `
  --host 127.0.0.1 `
  --port 8001
```

Health check:

```text
GET http://127.0.0.1:8001/probe/
```

Expected response:

```json
{"message": "Omniparser API ready"}
```

Use port `8001` unless you are sure port `8000` is free and not occupied by an old server process.

## 2. Parse Endpoint

Endpoint:

```text
POST http://127.0.0.1:8001/parse/
```

Content type:

```text
application/json
```

The image must be sent as a base64 string.

## 3. Request Body

Minimal request:

```json
{
  "base64_image": "..."
}
```

Full request:

```json
{
  "base64_image": "...",
  "box_threshold": 0.05,
  "iou_threshold": 0.7,
  "use_paddleocr": true,
  "imgsz": 640
}
```

Parameters:

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `base64_image` | string | Yes | None | Input image encoded as base64. |
| `box_threshold` | number or null | No | Service `--BOX_TRESHOLD` value | Icon detection confidence threshold. Lower values return more boxes; higher values return fewer boxes. |
| `iou_threshold` | number | No | `0.7` | Overlap filtering threshold for merging/removing overlapping boxes. |
| `use_paddleocr` | boolean | No | `false` | Whether to use PaddleOCR. If `true`, the current setup calls the separate `omni-paddleocr` Conda environment. |
| `imgsz` | integer or null | No | Original image size | YOLO icon detection input size, for example `640`, `1280`, or `1920`. Larger values may detect smaller UI elements but are slower. |

## 4. Response Body

Example shape:

```json
{
  "som_image_base64": "...",
  "parsed_content_list": [
    {
      "type": "text",
      "bbox": [0.0143, 0.0066, 0.1085, 0.0214],
      "interactivity": false,
      "content": "Thermo BioPharma Finder 5.1",
      "source": "box_ocr_content_ocr",
      "idx": 0
    },
    {
      "type": "icon",
      "bbox": [0.211, 0.305, 0.234, 0.337],
      "interactivity": true,
      "content": "folder icon",
      "source": "box_yolo_content_yolo",
      "idx": 1
    }
  ],
  "latency": 3.12
}
```

Response fields:

| Field | Type | Description |
| --- | --- | --- |
| `som_image_base64` | string | PNG image with OmniParser labels and bounding boxes, encoded as base64. |
| `parsed_content_list` | array | Parsed UI elements. Each element contains `idx`, `type`, `bbox`, `content`, etc. |
| `latency` | number | Server-side parse time in seconds. |

Element fields:

| Field | Type | Description |
| --- | --- | --- |
| `idx` | integer | Element index, starting from `0`. This index matches labels shown on the output image. |
| `type` | string | Usually `text` or `icon`. |
| `bbox` | array | Normalized `[x1, y1, x2, y2]` coordinates. |
| `interactivity` | boolean | Whether the element is treated as interactable. |
| `content` | string or null | OCR text or icon caption. |
| `source` | string | Internal source tag, such as OCR or YOLO/caption source. |

## 5. Bounding Box Coordinates

`bbox` uses normalized `xyxy` coordinates:

```text
[x1, y1, x2, y2]
```

The values are ratios in the range `0.0` to `1.0`, relative to the original image size.

Convert to pixel coordinates:

```python
x1_px = bbox[0] * image_width
y1_px = bbox[1] * image_height
x2_px = bbox[2] * image_width
y2_px = bbox[3] * image_height
```

Example:

```text
image size: 1920 x 1080
bbox: [0.1, 0.2, 0.3, 0.4]
pixel bbox: [192, 216, 576, 432]
```

## 6. Python Client Example

```python
import base64
import requests


def encode_image(image_path: str) -> str:
    with open(image_path, "rb") as file:
        return base64.b64encode(file.read()).decode("utf-8")


url = "http://127.0.0.1:8001/parse/"
image_path = r"F:\OmniParser\imgs\ScreenShot_2026-05-06_192141_030.png"

payload = {
    "base64_image": encode_image(image_path),
    "box_threshold": 0.05,
    "iou_threshold": 0.7,
    "use_paddleocr": True,
    "imgsz": 640,
}

response = requests.post(url, json=payload, timeout=300)
response.raise_for_status()
result = response.json()

print("latency:", result["latency"])
print("items:", len(result["parsed_content_list"]))
print(result["parsed_content_list"][0])
```

## 7. Save Returned Image And JSON

```python
import base64
import json
from datetime import datetime
from pathlib import Path


timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
image_dir = Path(r"F:\OmniParser\results\imageOutput")
json_dir = Path(r"F:\OmniParser\results\Parsed screen elements")
image_dir.mkdir(parents=True, exist_ok=True)
json_dir.mkdir(parents=True, exist_ok=True)

image_path = image_dir / f"{timestamp}.png"
json_path = json_dir / f"{timestamp}.json"

image_path.write_bytes(base64.b64decode(result["som_image_base64"]))
json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

print(image_path)
print(json_path)
```

## 8. Existing Client Script

This repository includes a ready-to-use example:

```text
F:\OmniParser\examples\call_omniparser_api.py
```

Run:

```powershell
cd F:\OmniParser

.\.venv\Scripts\python.exe .\examples\call_omniparser_api.py .\imgs\ScreenShot_2026-05-06_192141_030.png `
  --url http://127.0.0.1:8001/parse/ `
  --box-threshold 0.05 `
  --iou-threshold 0.7 `
  --use-paddleocr `
  --imgsz 640
```

Outputs are saved to:

```text
F:\OmniParser\results\imageOutput
F:\OmniParser\results\Parsed screen elements
```

## 9. Notes

- The FastAPI service loads GPU models at startup. The first startup can take time.
- Keep the service process running while clients call it.
- Restart the service after changing server-side Python code.
- Use `reload=False` for GPU inference services. Auto-reload can spawn extra Python processes on Windows and may accidentally use a different Python environment.
- If `use_paddleocr` is enabled, the current setup expects the separate Conda environment:

```text
C:\Users\86180\.conda\envs\omni-paddleocr
```
