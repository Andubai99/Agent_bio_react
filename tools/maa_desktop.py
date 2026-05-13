from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import win32api
import win32con
import win32gui
import win32process
import yaml
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa.toolkit import Toolkit
from PIL import Image

from agent.types import AgentAction, ImageInfo, ToolResult, UIElement


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESKTOP_CONFIG = ROOT / "config" / "settings.yaml"


@dataclass(frozen=True)
class DesktopConfig:
    screencap_method: str = "Background"
    mouse_method: str = "Seize"
    keyboard_method: str = "Seize"
    screenshot_use_raw_size: bool = True
    screenshot_target_long_side: int = 0
    window_sync_timeout_seconds: float = 8.0
    window_sync_poll_interval_seconds: float = 0.25

    @classmethod
    def load(cls, path: Path = DEFAULT_DESKTOP_CONFIG) -> "DesktopConfig":
        data: dict[str, Any] = {}
        if path.exists():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                data = loaded.get("win32", loaded)
                if not isinstance(data, dict):
                    data = {}
        return cls(
            screencap_method=str(
                os.environ.get("BIO_REACT_WIN32_SCREENCAP_METHOD", data.get("screencap_method", cls.screencap_method))
            ),
            mouse_method=str(os.environ.get("BIO_REACT_WIN32_MOUSE_METHOD", data.get("mouse_method", cls.mouse_method))),
            keyboard_method=str(
                os.environ.get("BIO_REACT_WIN32_KEYBOARD_METHOD", data.get("keyboard_method", cls.keyboard_method))
            ),
            screenshot_use_raw_size=_env_bool(
                "BIO_REACT_SCREENSHOT_USE_RAW_SIZE",
                bool(data.get("screenshot_use_raw_size", cls.screenshot_use_raw_size)),
            ),
            screenshot_target_long_side=int(
                os.environ.get(
                    "BIO_REACT_SCREENSHOT_TARGET_LONG_SIDE",
                    str(data.get("screenshot_target_long_side", cls.screenshot_target_long_side)),
                )
            ),
            window_sync_timeout_seconds=float(
                os.environ.get(
                    "BIO_REACT_WINDOW_SYNC_TIMEOUT_SECONDS",
                    str(data.get("window_sync_timeout_seconds", cls.window_sync_timeout_seconds)),
                )
            ),
            window_sync_poll_interval_seconds=float(
                os.environ.get(
                    "BIO_REACT_WINDOW_SYNC_POLL_INTERVAL_SECONDS",
                    str(data.get("window_sync_poll_interval_seconds", cls.window_sync_poll_interval_seconds)),
                )
            ),
        )


class MaaDesktop:
    def __init__(self, *, config: DesktopConfig | None = None):
        self.config = config or DesktopConfig.load()
        self.controller: Win32Controller | None = None
        self.hwnd: int | None = None
        self.window_title: Optional[str] = None
        self.screenshot_dir = ROOT / "data" / "screenshots"

    def connect_window(self, title_keyword: str) -> ToolResult:
        for window in Toolkit.find_desktop_windows():
            if title_keyword.lower() not in window.window_name.lower():
                continue

            return self._connect_window_handle(
                int(window.hwnd),
                window.window_name,
                success_code="WINDOW_CONNECTED",
                reason="title_keyword",
                keyword=title_keyword,
            )

        return ToolResult(False, "WINDOW_NOT_FOUND", f"No window title contains {title_keyword!r}.")

    def sync_after_action(
        self,
        *,
        expected_title_keywords: list[str] | None = None,
        settle_seconds: float = 0.25,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
        allow_foreground_switch: bool = True,
    ) -> ToolResult:
        if settle_seconds > 0:
            time.sleep(settle_seconds)

        current = {"hwnd": self.hwnd, "window_title": self.window_title}
        keywords = _normalize_title_keywords(expected_title_keywords or [])
        timeout = self.config.window_sync_timeout_seconds if timeout_seconds is None else max(0.0, timeout_seconds)
        interval = (
            self.config.window_sync_poll_interval_seconds
            if poll_interval_seconds is None
            else max(0.05, poll_interval_seconds)
        )
        deadline = time.time() + timeout
        attempts = 0
        last_windows: list[Any] = []

        while True:
            attempts += 1
            windows = list(Toolkit.find_desktop_windows())
            last_windows = windows
            for keyword in keywords:
                window = _find_window_by_title_keyword(windows, keyword)
                if window is None:
                    continue
                hwnd = int(window.hwnd)
                if hwnd == self.hwnd:
                    return ToolResult(
                        True,
                        "WINDOW_CONTEXT_UNCHANGED",
                        f"Current window already matches expected title: {window.window_name}",
                        data={
                            **current,
                            "matched_keyword": keyword,
                            "source": "expected_title",
                            "attempts": attempts,
                            "waited_seconds": round(max(0.0, time.time() - (deadline - timeout)), 3),
                        },
                    )
                switched = self._connect_window_handle(
                    hwnd,
                    window.window_name,
                    success_code="WINDOW_CONTEXT_SWITCHED",
                    reason="expected_title",
                    keyword=keyword,
                    previous=current,
                )
                switched.data["attempts"] = attempts
                switched.data["waited_seconds"] = round(max(0.0, time.time() - (deadline - timeout)), 3)
                return switched

            if not keywords or time.time() >= deadline:
                break
            time.sleep(interval)

        if keywords and not allow_foreground_switch and not _title_matches_any(self.window_title, keywords):
            return ToolResult(
                False,
                "WINDOW_CONTEXT_EXPECTED_NOT_FOUND",
                "Expected task window was not found.",
                data={
                    **current,
                    "expected_title_keywords": keywords,
                    "attempts": attempts,
                    "timeout_seconds": timeout,
                    "poll_interval_seconds": interval,
                    "visible_window_titles": _window_titles_snapshot(last_windows),
                },
            )

        foreground = _foreground_window_info()
        if foreground is not None:
            foreground_hwnd, foreground_title = foreground
            if foreground_hwnd != self.hwnd:
                if allow_foreground_switch:
                    return self._connect_window_handle(
                        foreground_hwnd,
                        foreground_title,
                        success_code="WINDOW_CONTEXT_SWITCHED",
                        reason="foreground",
                        previous=current,
                    )
                if self.hwnd is not None and self.window_title is not None:
                    return self._connect_window_handle(
                        self.hwnd,
                        self.window_title,
                        success_code="WINDOW_CONTEXT_RESTORED",
                        reason="restore_current",
                        previous={
                            **current,
                            "foreground_hwnd": foreground_hwnd,
                            "foreground_title": foreground_title,
                        },
                    )

        return ToolResult(
            True,
            "WINDOW_CONTEXT_UNCHANGED",
            "Window context unchanged after action.",
            data={**current, "expected_title_keywords": keywords, "source": "unchanged"},
        )

    def capture(self, label: str) -> ImageInfo:
        if self.controller is None:
            raise RuntimeError("No Win32 controller connected.")
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self.screenshot_dir / f"{label}_{stamp}.png"

        job = self.controller.post_screencap().wait()
        if job.failed:
            raise RuntimeError("Screencap job failed.")
        image = job.get()
        if image is None:
            raise RuntimeError("Screencap returned no image.")

        _save_image(path, image)
        height = int(image.shape[0])
        width = int(image.shape[1])
        resolution = tuple(int(value) for value in self.controller.resolution)
        if len(resolution) < 2 or resolution[0] <= 0 or resolution[1] <= 0:
            resolution = (width, height)
        return ImageInfo(
            path=str(path),
            width=width,
            height=height,
            resolution=(resolution[0], resolution[1]),
            image=None,
        )

    def execute_action(
        self,
        action: AgentAction,
        *,
        elements: list[UIElement],
        screenshot: ImageInfo,
    ) -> ToolResult:
        if self.controller is None:
            return ToolResult(False, "WINDOW_NOT_CONNECTED", "No Win32 controller connected.")
        action_type = action.type.strip().lower()
        if action_type in {"click", "double_click", "input_text", "drag"}:
            element = self._element_by_idx(elements, action.element_idx)
            if element is None:
                return ToolResult(
                    False,
                    "ELEMENT_NOT_FOUND",
                    f"No current UI element has idx={action.element_idx}.",
                    data={"available_indexes": [item.idx for item in elements]},
                )
            if action_type == "click":
                return self._click_element(element, screenshot, action.args)
            if action_type == "double_click":
                return self._double_click_element(element, screenshot, action.args)
            if action_type == "input_text":
                return self._input_text(element, screenshot, action.args)
            if action_type == "drag":
                return self._drag_element(element, screenshot, action.args)
        if action_type == "press_key":
            return self._press_key(action.args)
        if action_type == "wait":
            return self._wait(action.args)
        if action_type in {"noop", "no_op", "none", "null"}:
            return ToolResult(
                True,
                "NOOP",
                "No desktop action executed.",
                data={"action": action.type, "args": action.args},
            )
        return ToolResult(False, "UNKNOWN_ACTION", f"Unsupported action type: {action.type}")

    def _apply_controller_options(self) -> None:
        if self.controller is None:
            return
        self.controller.set_screenshot_use_raw_size(self.config.screenshot_use_raw_size)
        if self.config.screenshot_target_long_side > 0:
            self.controller.set_screenshot_target_long_side(self.config.screenshot_target_long_side)

    def _connect_window_handle(
        self,
        hwnd: int,
        window_title: str,
        *,
        success_code: str,
        reason: str,
        keyword: str | None = None,
        previous: dict[str, Any] | None = None,
    ) -> ToolResult:
        old_controller = self.controller
        old_hwnd = self.hwnd
        old_window_title = self.window_title
        self.hwnd = int(hwnd)
        self.window_title = str(window_title)

        activated = self._activate_window()
        if not activated.ok:
            self.controller = old_controller
            self.hwnd = old_hwnd
            self.window_title = old_window_title
            return activated

        controller = Win32Controller(
            self.hwnd,
            screencap_method=_enum_value(MaaWin32ScreencapMethodEnum, self.config.screencap_method),
            mouse_method=_enum_value(MaaWin32InputMethodEnum, self.config.mouse_method),
            keyboard_method=_enum_value(MaaWin32InputMethodEnum, self.config.keyboard_method),
        )
        connected = controller.post_connection().wait()
        if connected.failed:
            self.controller = old_controller
            self.hwnd = old_hwnd
            self.window_title = old_window_title
            return ToolResult(False, "WINDOW_CONNECT_FAILED", "Win32 controller connection job failed.")

        self.controller = controller
        self._apply_controller_options()
        return ToolResult(
            True,
            success_code,
            f"Connected to window: {self.window_title}",
            data={
                "hwnd": self.hwnd,
                "window_title": self.window_title,
                "reason": reason,
                "matched_keyword": keyword,
                "previous": previous,
            },
        )

    def _activate_window(self) -> ToolResult:
        if self.hwnd is None:
            return ToolResult(False, "WINDOW_NOT_CONNECTED", "No hwnd connected.")
        errors: list[str] = []
        try:
            if win32gui.IsIconic(self.hwnd):
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(self.hwnd, win32con.SW_SHOW)
        except Exception as exc:
            errors.append(f"ShowWindow failed: {exc}")
        try:
            win32gui.SetForegroundWindow(self.hwnd)
            time.sleep(0.1)
            return ToolResult(True, "WINDOW_ACTIVATED", "Win32 window activated.")
        except Exception as exc:
            errors.append(f"SetForegroundWindow failed: {exc}")

        forced = _force_foreground_window(self.hwnd)
        if forced.ok:
            return forced
        errors.append(forced.message)

        for action in (
            lambda: win32gui.BringWindowToTop(self.hwnd),
            lambda: win32gui.SetWindowPos(
                self.hwnd,
                win32con.HWND_TOPMOST,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
            ),
            lambda: win32gui.SetWindowPos(
                self.hwnd,
                win32con.HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
            ),
        ):
            try:
                action()
                time.sleep(0.05)
            except Exception as exc:
                errors.append(str(exc))

        if _foreground_window_info() and _foreground_window_info()[0] == self.hwnd:
            return ToolResult(
                True,
                "WINDOW_ACTIVATED",
                "Win32 window activated with fallback.",
                data={"fallback": True, "errors": errors},
            )
        return ToolResult(False, "WINDOW_ACTIVATE_FAILED", "; ".join(errors))

    def _click_element(self, element: UIElement, screenshot: ImageInfo, args: dict[str, Any]) -> ToolResult:
        point = _map_screenshot_point_to_controller(element.center, screenshot)
        result = _wait_job(self.controller.post_click(point[0], point[1]))
        if not result.ok:
            return result
        after_window = self._connect_after_action(args)
        return ToolResult(
            True,
            "ELEMENT_CLICKED",
            result.message,
            data=_action_data(element, screenshot, point, after_window),
        )

    def _double_click_element(self, element: UIElement, screenshot: ImageInfo, args: dict[str, Any]) -> ToolResult:
        point = _map_screenshot_point_to_controller(element.center, screenshot)
        first = _wait_job(self.controller.post_click(point[0], point[1]))
        if not first.ok:
            return first
        time.sleep(float(args.get("interval_seconds", 0.08)))
        second = _wait_job(self.controller.post_click(point[0], point[1]))
        if not second.ok:
            return second
        after_window = self._connect_after_action(args)
        return ToolResult(
            True,
            "ELEMENT_DOUBLE_CLICKED",
            second.message,
            data=_action_data(element, screenshot, point, after_window),
        )

    def _input_text(self, element: UIElement, screenshot: ImageInfo, args: dict[str, Any]) -> ToolResult:
        text = str(args.get("text", ""))
        focus_point = _map_screenshot_point_to_controller(element.center, screenshot)
        focus = _wait_job(self.controller.post_click(focus_point[0], focus_point[1]))
        if not focus.ok:
            return focus
        time.sleep(float(args.get("focus_wait_seconds", 0.1)))

        replace = bool(args.get("replace", True))
        if replace:
            select = self._send_hotkey([0x11], 0x41)
            if not select.ok:
                return select
            time.sleep(0.05)

        result = _wait_job(self.controller.post_input_text(text))
        if result.ok:
            return ToolResult(
                True,
                "TEXT_INPUT",
                result.message,
                data={**_action_data(element, screenshot, focus_point, None), "text": text, "replace": replace},
            )
        return result

    def _drag_element(self, element: UIElement, screenshot: ImageInfo, args: dict[str, Any]) -> ToolResult:
        start_offset = _offset(args.get("start_offset"), default=(-10, 0))
        end_offset = _offset(args.get("end_offset"), default=(10, 0))
        start_image = (element.center[0] + start_offset[0], element.center[1] + start_offset[1])
        end_image = (element.center[0] + end_offset[0], element.center[1] + end_offset[1])
        start = _map_screenshot_point_to_controller(start_image, screenshot)
        end = _map_screenshot_point_to_controller(end_image, screenshot)
        result = _wait_job(self.controller.post_swipe(start[0], start[1], end[0], end[1], int(args.get("duration", 600))))
        if result.ok:
            return ToolResult(
                True,
                "ELEMENT_DRAGGED",
                result.message,
                data={
                    "element": _element_data(element),
                    "start_image_point": start_image,
                    "end_image_point": end_image,
                    "start_controller_point": start,
                    "end_controller_point": end,
                    "resolution": screenshot.resolution,
                    "image_size": [screenshot.width, screenshot.height],
                },
            )
        return result

    def _press_key(self, args: dict[str, Any]) -> ToolResult:
        key_value = args.get("keys", args.get("key", 0))
        if isinstance(key_value, str):
            parsed = _parse_key_sequence(key_value)
            if parsed is None:
                return ToolResult(False, "BAD_ARGS", f"Unsupported key sequence: {key_value}")
            modifiers, key = parsed
            if modifiers:
                return self._send_hotkey(modifiers, key, label=key_value)
            result = _wait_job(self.controller.post_click_key(key))
            if result.ok:
                return ToolResult(True, "KEY_PRESSED", result.message, data={"key": key_value})
            return result

        result = _wait_job(self.controller.post_click_key(int(key_value)))
        if result.ok:
            return ToolResult(True, "KEY_PRESSED", result.message, data={"key": int(key_value)})
        return result

    def _send_hotkey(self, modifiers: list[int], key: int, *, label: str = "") -> ToolResult:
        for modifier in modifiers:
            down = _wait_job(self.controller.post_key_down(modifier))
            if not down.ok:
                return down
        key_result = _wait_job(self.controller.post_click_key(key))
        final_result = key_result
        for modifier in reversed(modifiers):
            up = _wait_job(self.controller.post_key_up(modifier))
            if final_result.ok and not up.ok:
                final_result = up
        if final_result.ok:
            return ToolResult(True, "KEY_PRESSED", final_result.message, data={"keys": label or key})
        return final_result

    def _wait(self, args: dict[str, Any]) -> ToolResult:
        seconds = float(args.get("seconds", 1.0))
        if seconds > 0:
            time.sleep(seconds)
        return ToolResult(True, "WAITED", f"Waited {seconds} seconds.", data={"seconds": seconds})

    def _connect_after_action(self, args: dict[str, Any]) -> dict[str, Any] | None:
        title_keyword = args.get("after_window_title_keyword")
        if not title_keyword:
            return None
        wait_seconds = float(args.get("after_window_wait_seconds", 1.0))
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        result = self.connect_window(str(title_keyword))
        return {"ok": result.ok, "code": result.code, "message": result.message, **result.data}

    @staticmethod
    def _element_by_idx(elements: list[UIElement], element_idx: Optional[int]) -> UIElement | None:
        if element_idx is None:
            return None
        return next((item for item in elements if item.idx == element_idx), None)


def _normalize_title_keywords(values: list[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        keyword = str(value).strip().strip("`'\"“”‘’")
        if not keyword or keyword in {"...", "…"}:
            continue
        if all(not char.isalnum() for char in keyword):
            continue
        if keyword not in output:
            output.append(keyword)
    return output


def _find_window_by_title_keyword(windows: list[Any], keyword: str):
    lowered = keyword.lower()
    for window in windows:
        title = str(getattr(window, "window_name", ""))
        if lowered in title.lower():
            return window
    return None


def _window_titles_snapshot(windows: list[Any], *, limit: int = 80) -> list[str]:
    titles: list[str] = []
    for window in windows[:limit]:
        title = str(getattr(window, "window_name", "")).strip()
        if title:
            titles.append(title)
    return titles


def _title_matches_any(title: str | None, keywords: list[str]) -> bool:
    text = str(title or "").lower()
    return bool(text) and any(keyword.lower() in text for keyword in keywords)


def _force_foreground_window(hwnd: int) -> ToolResult:
    errors: list[str] = []
    attached_threads: list[int] = []
    try:
        current_thread = win32api.GetCurrentThreadId()
        target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
        foreground_hwnd = win32gui.GetForegroundWindow()
        foreground_thread = 0
        if foreground_hwnd:
            foreground_thread, _ = win32process.GetWindowThreadProcessId(foreground_hwnd)

        for thread_id in {target_thread, foreground_thread}:
            if thread_id and thread_id != current_thread:
                try:
                    win32process.AttachThreadInput(current_thread, thread_id, True)
                    attached_threads.append(thread_id)
                except Exception as exc:
                    errors.append(f"AttachThreadInput({thread_id}) failed: {exc}")

        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        try:
            win32gui.SetFocus(hwnd)
        except Exception as exc:
            errors.append(f"SetFocus failed: {exc}")
        time.sleep(0.1)
    except Exception as exc:
        errors.append(f"force foreground failed: {exc}")
    finally:
        current_thread = win32api.GetCurrentThreadId()
        for thread_id in reversed(attached_threads):
            try:
                win32process.AttachThreadInput(current_thread, thread_id, False)
            except Exception as exc:
                errors.append(f"DetachThreadInput({thread_id}) failed: {exc}")

    foreground = _foreground_window_info()
    if foreground and foreground[0] == hwnd:
        return ToolResult(
            True,
            "WINDOW_ACTIVATED",
            "Win32 window activated with thread input fallback.",
            data={"fallback": "attach_thread_input", "warnings": errors},
        )
    return ToolResult(False, "WINDOW_ACTIVATE_FAILED", "; ".join(errors) or "Thread input fallback did not focus window.")


def _foreground_window_info() -> tuple[int, str] | None:
    try:
        hwnd = int(win32gui.GetForegroundWindow() or 0)
        if hwnd <= 0 or not win32gui.IsWindowVisible(hwnd):
            return None
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return None
        return hwnd, title
    except Exception:
        return None


def _map_screenshot_point_to_controller(point: tuple[int, int], screenshot: ImageInfo) -> tuple[int, int]:
    image_width = max(1, int(screenshot.width))
    image_height = max(1, int(screenshot.height))
    controller_width = int(screenshot.resolution[0]) if screenshot.resolution else image_width
    controller_height = int(screenshot.resolution[1]) if screenshot.resolution else image_height
    if controller_width <= 0 or controller_height <= 0:
        return point
    return (
        round(point[0] * controller_width / image_width),
        round(point[1] * controller_height / image_height),
    )


def _save_image(path: Path, image: np.ndarray) -> None:
    array = np.asarray(image)
    if array.ndim == 3 and array.shape[2] == 4:
        array = array[:, :, [2, 1, 0, 3]]
        output = Image.fromarray(array, mode="RGBA")
    elif array.ndim == 3 and array.shape[2] == 3:
        array = array[:, :, [2, 1, 0]]
        output = Image.fromarray(array, mode="RGB")
    else:
        output = Image.fromarray(array)
    output.save(path)


def _wait_job(job) -> ToolResult:
    try:
        waited = job.wait()
    except Exception as exc:
        return ToolResult(False, "MAA_JOB_FAILED", str(exc))
    if getattr(waited, "failed", False):
        return ToolResult(False, "MAA_JOB_FAILED", "Maa job failed.")
    return ToolResult(True, "MAA_OK", "Controller operation succeeded.")


def _enum_value(enum_cls, value: str) -> int:
    text = str(value).strip()
    if not text:
        return int(enum_cls(0))
    try:
        return int(getattr(enum_cls, text))
    except AttributeError:
        pass
    try:
        return int(enum_cls[text])
    except Exception:
        pass
    return int(enum_cls(int(text, 0)))


def _element_data(element: UIElement) -> dict[str, Any]:
    return {
        "idx": element.idx,
        "type": element.type,
        "content": element.content,
        "bbox": element.bbox,
        "pixel_bbox": element.pixel_bbox,
        "center": element.center,
        "interactive": element.interactive,
        "source": element.source,
    }


def _action_data(
    element: UIElement,
    screenshot: ImageInfo,
    controller_point: tuple[int, int],
    after_window: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "element": _element_data(element),
        "image_point": element.center,
        "controller_point": controller_point,
        "resolution": screenshot.resolution,
        "image_size": [screenshot.width, screenshot.height],
        "after_window": after_window,
    }


def _offset(value: Any, *, default: tuple[int, int]) -> tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), int(value[1])
    return default


def _parse_key_sequence(value: str) -> Optional[tuple[list[int], int]]:
    parts = [part.strip().lower() for part in value.replace("control", "ctrl").split("+")]
    parts = [part for part in parts if part]
    if not parts:
        return None
    key = _key_code(parts[-1])
    if key is None:
        return None
    modifiers: list[int] = []
    for part in parts[:-1]:
        modifier = _key_code(part)
        if modifier is None:
            return None
        modifiers.append(modifier)
    return modifiers, key


def _key_code(value: str) -> Optional[int]:
    aliases = {
        "ctrl": 0x11,
        "shift": 0x10,
        "alt": 0x12,
        "enter": 0x0D,
        "return": 0x0D,
        "tab": 0x09,
        "backspace": 0x08,
        "delete": 0x2E,
        "del": 0x2E,
        "end": 0x23,
        "home": 0x24,
        "left": 0x25,
        "up": 0x26,
        "right": 0x27,
        "down": 0x28,
        "esc": 0x1B,
        "escape": 0x1B,
        "space": 0x20,
    }
    if value in aliases:
        return aliases[value]
    if len(value) == 1 and value.isalnum():
        return ord(value.upper())
    try:
        return int(value, 0)
    except ValueError:
        return None


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}
