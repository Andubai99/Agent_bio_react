import os
import site

site_packages = next(path for path in site.getsitepackages() if path.endswith("site-packages"))
for dll_dir in (
    os.path.join(site_packages, "nvidia", "cu13", "bin", "x86_64"),
    os.path.join(site_packages, "nvidia", "cudnn", "bin"),
):
    if os.path.isdir(dll_dir):
        os.add_dll_directory(dll_dir)
        os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")

from PIL import Image, ImageDraw
import numpy as np
import paddle
from paddleocr import PaddleOCR


def main():
    print("paddle", paddle.__version__)
    print("paddle_cuda", paddle.device.is_compiled_with_cuda(), paddle.device.cuda.device_count())
    paddle.set_device("gpu:0")
    ocr = PaddleOCR(
        lang="en",
        use_angle_cls=False,
        use_gpu=True,
        show_log=False,
        max_batch_size=1024,
        use_dilation=True,
        det_db_score_mode="slow",
        rec_batch_num=1024,
    )
    img = Image.new("RGB", (320, 120), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 40), "GPU test", fill="black")
    result = ocr.ocr(np.array(img), cls=False)[0]
    print("result", result)


if __name__ == "__main__":
    main()
