# Codex Integration Handoff: OmniParser As UI Parsing Layer

This document is for a future Codex task that integrates this local OmniParser setup into another project as the UI parsing layer.

## Goal

Use OmniParser as an external UI parsing service.

The consuming project should send a screenshot image to OmniParser and receive:

- A labeled output image.
- A structured list of parsed UI elements.
- Per-element indexes, normalized bounding boxes, type, content, interactivity, and source.

Recommended integration style:

```text
Consumer project -> HTTP POST -> OmniParser FastAPI service -> JSON response
```

Avoid importing OmniParser directly into the consumer process unless there is a strong reason. The dependency stack is large and GPU-heavy.

## Current Repository

Workspace:

```text
F:\OmniParser
```

Main Python environment:

```text
F:\OmniParser\.venv
```

PaddleOCR side environment:

```text
C:\Users\86180\.conda\envs\omni-paddleocr
```

GPU:

```text
NVIDIA GeForce RTX 4070 SUPER
PyTorch CUDA 13.0
```

## Important Local Changes

This repository is not a clean upstream clone. It has been adapted for this Windows GPU machine.

Important changed/added files:

```text
omnitool/omniparserserver/omniparserserver.py
util/omniparser.py
util/utils.py
gradio_demo.py
examples/call_omniparser_api.py
tools/paddleocr_worker.py
tools/paddleocr_smoke.py
requirements.txt
requirements-paddleocr.txt
docs/FastAPI_Usage.md
```

Key changes:

- FastAPI `/parse/` now accepts parameters similar to the Gradio UI:
  - `box_threshold`
  - `iou_threshold`
  - `use_paddleocr`
  - `imgsz`
- `parsed_content_list` elements now include `idx`.
- `uvicorn.run(..., reload=True)` was changed to no reload:

```python
uvicorn.run(app, host=args.host, port=args.port)
```

Reason: `reload=True` on Windows spawned extra Python processes and sometimes used `D:\Anaconda3\python.exe`, causing old code and environment confusion.

- PaddleOCR is run via a separate worker process in the `omni-paddleocr` Conda environment.
- Main `.venv` runs PyTorch CUDA for YOLO and Florence.

## Why PaddleOCR Is Separate

Do not put PyTorch CUDA and PaddlePaddle GPU in the same Windows Python process.

Observed issue:

```text
CUDA/cuDNN DLL conflicts between PyTorch and PaddlePaddle on Windows.
```

Current architecture:

```text
FastAPI / OmniParser main process
  - PyTorch CUDA
  - YOLO icon detection
  - Florence captioning

PaddleOCR worker process
  - Conda env: omni-paddleocr
  - PaddlePaddle GPU
  - Called only when use_paddleocr=true
```

Worker file:

```text
tools/paddleocr_worker.py
```

The main OmniParser code calls it through `util/utils.py`.

## Start OmniParser Service

Use port `8001` by default. Port `8000` previously had stale/old-process confusion on this machine.

PowerShell:

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

## API Contract

Endpoint:

```text
POST http://127.0.0.1:8001/parse/
```

Content type:

```text
application/json
```

Request body:

```json
{
  "base64_image": "...",
  "box_threshold": 0.05,
  "iou_threshold": 0.7,
  "use_paddleocr": true,
  "imgsz": 640
}
```

Fields:

| Field | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `base64_image` | string | Yes | None | Input image bytes encoded as base64. |
| `box_threshold` | number or null | No | Startup `--BOX_TRESHOLD` | YOLO icon confidence threshold. Lower returns more detections. |
| `iou_threshold` | number | No | `0.7` | Overlap filtering threshold. |
| `use_paddleocr` | boolean | No | `false` | Calls external PaddleOCR worker if true. |
| `imgsz` | integer or null | No | Image size | YOLO image size. `640` is fast; larger values can catch smaller UI elements. |

Response body:

```json
{
  "som_image_base64": "...",
  "parsed_content_list": [
    {
      "type": "text",
      "bbox": [0.0143, 0.0066, 0.1085, 0.0214],
      "interactivity": false,
      "content": "Example text",
      "source": "box_ocr_content_ocr",
      "idx": 0
    }
  ],
  "latency": 3.12
}
```

Response fields:

| Field | Type | Notes |
| --- | --- | --- |
| `som_image_base64` | string | PNG image with labels and boxes, base64 encoded. |
| `parsed_content_list` | array | UI elements. Each should contain `idx`. |
| `latency` | number | Server-side parse time in seconds. |

Element fields:

| Field | Type | Notes |
| --- | --- | --- |
| `idx` | integer | 0-based element index. Use this as the stable label for a single parse result. |
| `type` | string | Usually `text` or `icon`. |
| `bbox` | number[4] | Normalized `[x1, y1, x2, y2]`. |
| `interactivity` | boolean | Whether OmniParser treats the element as interactable. |
| `content` | string or null | OCR text or icon caption. |
| `source` | string | Internal source label. |

## Bounding Boxes

`bbox` is normalized `xyxy`:

```text
[x1, y1, x2, y2]
```

Values are ratios relative to original image width/height.

Pixel conversion:

```python
def bbox_to_pixels(bbox, width, height):
    x1, y1, x2, y2 = bbox
    return [
        int(x1 * width),
        int(y1 * height),
        int(x2 * width),
        int(y2 * height),
    ]
```

## Existing Python Client Example

File:

```text
examples/call_omniparser_api.py
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

Outputs:

```text
results/imageOutput/<timestamp>.png
results/Parsed screen elements/<timestamp>.json
```

This script is useful as a reference implementation for a consumer project.

## Minimal Consumer-Side Client

Use this pattern in another Python project:

```python
import base64
import requests


class OmniParserClient:
    def __init__(self, url="http://127.0.0.1:8001/parse/", timeout=300):
        self.url = url
        self.timeout = timeout

    @staticmethod
    def encode_image(path):
        with open(path, "rb") as file:
            return base64.b64encode(file.read()).decode("utf-8")

    def parse(
        self,
        image_path,
        box_threshold=0.05,
        iou_threshold=0.7,
        use_paddleocr=True,
        imgsz=640,
    ):
        payload = {
            "base64_image": self.encode_image(image_path),
            "box_threshold": box_threshold,
            "iou_threshold": iou_threshold,
            "use_paddleocr": use_paddleocr,
            "imgsz": imgsz,
        }
        response = requests.post(self.url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()
```

## Integration Recommendations

For the consuming project:

1. Treat OmniParser as an external local service.
2. Do not import OmniParser modules directly unless necessary.
3. Put the service URL in config, for example:

```text
OMNIPARSER_URL=http://127.0.0.1:8001/parse/
```

4. Add timeout handling. First calls can be slower because models or PaddleOCR worker may warm up.
5. Keep the raw response JSON for debugging.
6. If the consuming project needs pixel boxes, convert normalized `bbox` using the original screenshot size.
7. Use `idx` only within one parse result. It is not stable across different screenshots.

## Suggested Consumer Data Model

Example:

```python
from dataclasses import dataclass


@dataclass
class UIElement:
    idx: int
    type: str
    bbox: list[float]
    content: str | None
    interactivity: bool
    source: str
```

Consumer-side mapping:

```python
elements = [
    UIElement(
        idx=item["idx"],
        type=item["type"],
        bbox=item["bbox"],
        content=item.get("content"),
        interactivity=item.get("interactivity", False),
        source=item.get("source", ""),
    )
    for item in result["parsed_content_list"]
]
```

## Operational Notes

- Restart the FastAPI service after changing server-side code.
- Prefer port `8001` on this machine.
- Avoid `reload=True`.
- Watch GPU memory with:

```powershell
nvidia-smi
```

- If `use_paddleocr=true`, make sure the Conda env exists:

```powershell
conda env list
```

Expected:

```text
omni-paddleocr    C:\Users\86180\.conda\envs\omni-paddleocr
```

Smoke test for PaddleOCR:

```powershell
cd F:\OmniParser
conda run -n omni-paddleocr python tools\paddleocr_smoke.py
```

Expected output includes:

```text
paddle_cuda True 1
result ... GPU test ...
```

## Known Caveats

- `bbox` values are normalized, not pixels.
- `som_image_base64` is a PNG image encoded as base64; clients must decode it before saving.
- PaddleOCR can increase latency because it starts an external Python worker.
- Current OCR language is configured as English in the PaddleOCR worker.
- If Chinese OCR is required, update `tools/paddleocr_worker.py` and the environment/model setup accordingly.
- The upstream OmniParser API did not originally expose all Gradio parameters; this local repo now does.

## Verification Performed

The following image was tested:

```text
F:\OmniParser\imgs\ScreenShot_2026-05-06_192141_030.png
```

Observed output:

```text
latency: about 11.6s with PaddleOCR
parsed_items: 124
JSON includes idx fields
```

Generated files were saved under:

```text
F:\OmniParser\results\imageOutput
F:\OmniParser\results\Parsed screen elements
```
