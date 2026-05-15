import argparse
import json
import os
import site
import sys


def configure_dll_paths():
    site_packages = next(path for path in site.getsitepackages() if path.endswith("site-packages"))
    for dll_dir in (
        os.path.join(site_packages, "nvidia", "cu13", "bin", "x86_64"),
        os.path.join(site_packages, "nvidia", "cudnn", "bin"),
    ):
        if os.path.isdir(dll_dir):
            os.add_dll_directory(dll_dir)
            os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--text-threshold", type=float, default=0.5)
    args = parser.parse_args()

    configure_dll_paths()

    import numpy as np
    import paddle
    from PIL import Image
    from paddleocr import PaddleOCR

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

    image = Image.open(args.image).convert("RGB")
    result = ocr.ocr(np.array(image), cls=False)[0] or []
    coord = [item[0] for item in result if item[1][1] > args.text_threshold]
    text = [item[1][0] for item in result if item[1][1] > args.text_threshold]
    print(json.dumps({"coord": coord, "text": text}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        raise
