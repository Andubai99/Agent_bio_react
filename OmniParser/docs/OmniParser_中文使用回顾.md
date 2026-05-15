# OmniParser 中文使用回顾

这份文档是给以后重新接手这个项目时看的。目标是：即使很久没用 OmniParser，也能快速想起来它是干什么的、现在这台机器上怎么用、我们做过哪些改动、遇到问题该从哪里查。

## 1. 这个东西是干什么的

OmniParser 是一个 UI 截图解析工具。

你给它一张软件界面截图，它会输出两类结果：

1. 一张带编号和框的图片。
2. 一个 JSON 列表，里面是识别出来的界面元素。

每个元素大概长这样：

```json
{
  "type": "text",
  "bbox": [0.0143, 0.0066, 0.1085, 0.0214],
  "interactivity": false,
  "content": "Thermo BioPharma Finder 5.1",
  "source": "box_ocr_content_ocr",
  "idx": 0
}
```

可以理解为：

```text
idx              元素编号
type             text 或 icon
bbox             元素位置
content          文字内容或图标描述
interactivity    是否像是可交互元素
source           这个元素来自 OCR 还是图标检测
```

这个项目准备用作你其他项目里的“UI 解析层”。

也就是说，其他项目可以把截图发给 OmniParser，然后拿到页面里有哪些按钮、文字、图标、位置在哪里。

## 2. 当前推荐用法

推荐把 OmniParser 当成一个本地服务使用。

架构是：

```text
你的项目
  |
  | 发送截图 base64
  v
OmniParser FastAPI 服务
  |
  | 返回标注图 + 元素 JSON
  v
你的项目继续处理
```

不要优先把 OmniParser 直接 import 到你的项目里。

原因是它依赖比较重，涉及 PyTorch、CUDA、PaddleOCR、模型权重等。作为独立服务更稳定，也更容易排查问题。

## 3. 当前目录和环境

项目目录：

```text
F:\OmniParser
```

主虚拟环境：

```text
F:\OmniParser\.venv
```

主环境负责：

```text
PyTorch CUDA
YOLO 图标检测
Florence 图标描述
FastAPI 服务
Gradio 页面
```

PaddleOCR 独立环境：

```text
C:\Users\86180\.conda\envs\omni-paddleocr
```

PaddleOCR 没有放在主 `.venv` 里，而是单独放在 Conda 环境中。

原因后面会解释。

## 4. 怎么启动 FastAPI 服务

以后使用时，优先启动 FastAPI 服务。

打开 PowerShell：

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

注意：

```text
推荐用 8001 端口。
```

之前 8000 端口出现过旧服务残留，导致请求打到旧代码上。所以为了少踩坑，默认用 8001。

启动后，这个 PowerShell 窗口不要关。

## 5. 怎么检查服务是否启动成功

浏览器打开：

```text
http://127.0.0.1:8001/probe/
```

如果看到：

```json
{"message":"Omniparser API ready"}
```

说明服务正常。

## 6. 怎么调用 OmniParser

我们写了一个调用示例脚本：

```text
F:\OmniParser\examples\call_omniparser_api.py
```

用它处理一张图片：

```powershell
cd F:\OmniParser

.\.venv\Scripts\python.exe .\examples\call_omniparser_api.py .\imgs\ScreenShot_2026-05-06_192141_030.png `
  --url http://127.0.0.1:8001/parse/ `
  --box-threshold 0.05 `
  --iou-threshold 0.7 `
  --use-paddleocr `
  --imgsz 640
```

如果不想用 PaddleOCR，就去掉这一行：

```powershell
--use-paddleocr
```

## 7. 输出文件在哪里

调用脚本会自动保存两个文件。

标注后的图片：

```text
F:\OmniParser\results\imageOutput\时间戳.png
```

解析结果 JSON：

```text
F:\OmniParser\results\Parsed screen elements\时间戳.json
```

例如：

```text
F:\OmniParser\results\imageOutput\20260507_165120_686518.png
F:\OmniParser\results\Parsed screen elements\20260507_165120_686518.json
```

## 8. FastAPI 接口长什么样

接口地址：

```text
POST http://127.0.0.1:8001/parse/
```

请求是 JSON：

```json
{
  "base64_image": "图片的base64字符串",
  "box_threshold": 0.05,
  "iou_threshold": 0.7,
  "use_paddleocr": true,
  "imgsz": 640
}
```

返回也是 JSON：

```json
{
  "som_image_base64": "标注后的图片base64",
  "parsed_content_list": [
    {
      "type": "text",
      "bbox": [0.0143, 0.0066, 0.1085, 0.0214],
      "interactivity": false,
      "content": "Thermo BioPharma Finder 5.1",
      "source": "box_ocr_content_ocr",
      "idx": 0
    }
  ],
  "latency": 3.12
}
```

## 9. 可调参数是什么意思

### box_threshold

图标检测置信度阈值。

```text
越低：检测出来的框更多，但可能有误检
越高：框更少，但可能漏掉小图标
```

常用：

```text
0.05
```

### iou_threshold

重叠框过滤阈值。

用于处理多个框重叠的情况。

常用：

```text
0.7
```

### use_paddleocr

是否使用 PaddleOCR。

```text
true   使用 PaddleOCR
false  使用 EasyOCR
```

PaddleOCR 通常对复杂界面、小字、中文更强，但启动更慢。

EasyOCR 更轻、更简单，英文界面通常够用。

### imgsz

YOLO 图标检测的输入尺寸。

```text
640   比较快
1280  更细，但更慢
1920  更细，更慢，占用更多显存
```

常用：

```text
640
```

## 10. bbox 是什么坐标

返回的 `bbox` 是归一化坐标，不是像素坐标。

格式是：

```text
[x1, y1, x2, y2]
```

范围通常是：

```text
0.0 到 1.0
```

含义是：

```text
x1 = 左边界 / 图片宽度
y1 = 上边界 / 图片高度
x2 = 右边界 / 图片宽度
y2 = 下边界 / 图片高度
```

如果原图是 `1920 x 1080`，bbox 是：

```json
[0.1, 0.2, 0.3, 0.4]
```

那么像素坐标是：

```text
x1 = 192
y1 = 216
x2 = 576
y2 = 432
```

Python 换算：

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

## 11. 我们做过哪些工作

### 1. 下载并安装 OmniParser

原本 `git clone` 直连 GitHub 超时，所以用 GitHub zip 包下载并展开到了：

```text
F:\OmniParser
```

### 2. 创建了主虚拟环境

创建了：

```text
F:\OmniParser\.venv
```

并安装了依赖。

### 3. 安装了 V2 模型权重

模型权重在：

```text
F:\OmniParser\weights\icon_detect
F:\OmniParser\weights\icon_caption_florence
```

### 4. 修复了 transformers 兼容问题

Florence-2 和新版本 `transformers` 有兼容问题，所以固定为：

```text
transformers==4.49.0
```

### 5. 改成 GPU 版 PyTorch

当前主环境使用：

```text
torch==2.11.0+cu130
torchvision==0.26.0+cu130
```

验证过：

```text
torch.cuda.is_available() == True
```

### 6. 处理了 Gradio localhost 代理问题

Windows 系统代理影响了 `127.0.0.1`，导致 Gradio 启动时 502。

后来在代码里设置了：

```text
NO_PROXY=127.0.0.1,localhost
```

### 7. 处理了 PaddleOCR 和 PyTorch CUDA 冲突

PaddleOCR 如果和 PyTorch CUDA 在同一个 Windows Python 进程里跑，会出现 CUDA/cuDNN DLL 冲突。

所以现在做法是：

```text
主 OmniParser 服务：PyTorch CUDA
PaddleOCR：独立 Conda 环境 + worker 进程
```

PaddleOCR 环境：

```text
C:\Users\86180\.conda\envs\omni-paddleocr
```

### 8. 给 FastAPI 增加了可调参数

原始 FastAPI 只能传图片。

现在支持：

```text
box_threshold
iou_threshold
use_paddleocr
imgsz
```

### 9. 给每个元素增加 idx

现在返回的 `parsed_content_list` 每个元素都有：

```json
"idx": 0
```

方便和标注图上的编号对应。

### 10. 写了调用脚本和文档

调用脚本：

```text
examples/call_omniparser_api.py
```

FastAPI 使用说明：

```text
docs/FastAPI_Usage.md
```

给 Codex 接手用的集成文档：

```text
docs/Codex_Integration_Handoff.md
```

这份中文回顾文档：

```text
docs/OmniParser_中文使用回顾.md
```

## 12. 常见问题

### JSON 里没有 idx

大概率是你请求打到了旧服务。

处理方法：

1. 停掉旧服务。
2. 不要用 8000，改用 8001。
3. 重新启动 FastAPI 服务。

检查端口：

```powershell
netstat -ano | Select-String ':8001'
```

### Gradio 或 FastAPI 访问 localhost 出错

可能是系统代理影响了 localhost。

当前代码里已经设置了：

```text
NO_PROXY=127.0.0.1,localhost
```

如果你自己写客户端，也可以手动指定不要走代理。

### PaddleOCR 很慢

正常。

因为 PaddleOCR 是独立进程调用，第一次会加载模型。

后面如果要优化，可以考虑把 PaddleOCR worker 做成常驻服务，而不是每次调用启动一次。

### PaddleOCR 报错

先单独测试：

```powershell
cd F:\OmniParser
conda run -n omni-paddleocr python tools\paddleocr_smoke.py
```

如果正常，会看到类似：

```text
paddle_cuda True 1
result ... GPU test ...
```

### 显存占用太高

查看：

```powershell
nvidia-smi
```

如果有旧服务没关，可能会占显存。

查 Python 进程：

```powershell
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Select-Object ProcessId,CommandLine
```

## 13. 以后接入其他项目时怎么做

最推荐：在你的项目里写一个 OmniParser 客户端类。

示例：

```python
import base64
import requests


class OmniParserClient:
    def __init__(self, url="http://127.0.0.1:8001/parse/", timeout=300):
        self.url = url
        self.timeout = timeout

    def parse(self, image_path, box_threshold=0.05, iou_threshold=0.7, use_paddleocr=True, imgsz=640):
        with open(image_path, "rb") as file:
            image_base64 = base64.b64encode(file.read()).decode("utf-8")

        response = requests.post(
            self.url,
            json={
                "base64_image": image_base64,
                "box_threshold": box_threshold,
                "iou_threshold": iou_threshold,
                "use_paddleocr": use_paddleocr,
                "imgsz": imgsz,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()
```

使用：

```python
client = OmniParserClient()
result = client.parse("screenshot.png")

for item in result["parsed_content_list"]:
    print(item["idx"], item["type"], item["content"], item["bbox"])
```

## 14. 记住这几点就够了

如果很久没用，只要记住：

```text
1. OmniParser 是截图 -> UI 元素 JSON 的解析服务。
2. 启动 FastAPI 服务，用 8001。
3. 调用 /parse/，传 base64 图片和参数。
4. 返回 som_image_base64 和 parsed_content_list。
5. bbox 是归一化 xyxy。
6. idx 是元素编号。
7. PaddleOCR 用独立 omni-paddleocr 环境。
8. 不要开 reload=True。
```
