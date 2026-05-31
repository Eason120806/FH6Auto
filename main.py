import sys
import os
# ====== 【修复 OMP 冲突的核心代码】 ======
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
# =======================================
import json
import time
import shutil
import ctypes
import subprocess
import webbrowser
# ====== 【新增】：启动前置环境检测 (防闪退机制) ======
def check_windows_dependencies():
    if sys.platform != "win32":
        return
    missing_dlls = []
    required_dlls = ["vcruntime140.dll", "msvcp140.dll", "vcruntime140_1.dll"]
    for dll in required_dlls:
        try:
            ctypes.WinDLL(dll)
        except OSError:
            missing_dlls.append(dll)
    if missing_dlls:
        msg = (f"警告：系统缺失以下关键运行库，大概率会导致程序闪退或图像识别失败：\n\n"
               f"{', '.join(missing_dlls)}\n\n"
               f"这是因为您的电脑缺少微软 C++ 运行环境。\n"
               f"请搜索下载【微软常用运行库合集】或【VC++ 2015-2022】安装后重试。\n\n"
               f"点击“确定”强行继续运行（如果闪退请安装运行库）。")
        ctypes.windll.user32.MessageBoxW(0, msg, "缺少运行库拦截提示", 0x30 | 0x0)
check_windows_dependencies()
# ===================================================
# 【极其关键】：必须在任何 UI 库导入之前设置 DPI 感知
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import customtkinter as ctk
ctk.deactivate_automatic_dpi_awareness()
ctk.set_widget_scaling(1.0)
ctk.set_window_scaling(1.0)
import cv2
import numpy as np
import pyautogui
import pydirectinput
import requests
from pynput import keyboard
from PIL import Image, ImageGrab
import win32gui
import pickle
import threading

# ==========================================
# --- 路径与资源策略 ---
# ==========================================
def get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_internal_dir():
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return get_app_dir()

APP_DIR = get_app_dir()
INTERNAL_DIR = get_internal_dir()
CONFIG_DIR = os.path.join(APP_DIR, "config")
USER_CONFIG_FILE = os.path.join(APP_DIR, "config.json")
LOG_FILE = os.path.join(APP_DIR, "bot_log.txt")
CACHE_DIR = os.path.join(APP_DIR, "cache")
TEMPLATE_CACHE_FILE = os.path.join(CACHE_DIR, "template_cache.pkl")
TEMPLATE_META_FILE = os.path.join(CACHE_DIR, "template_meta.json")
CURRENT_VERSION = "1.1.6"

def auto_extract_configs():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    old_configs = [
        os.path.join(APP_DIR, "bot_config.json"),
        os.path.join(APP_DIR, "bot-config.json"),
        os.path.join(CONFIG_DIR, "bot-config.json"),
        os.path.join(CONFIG_DIR, "bot_config.json"),
        os.path.join(CONFIG_DIR, "config.json")
    ]
    for old_path in old_configs:
        if os.path.exists(old_path):
            try:
                if not os.path.exists(USER_CONFIG_FILE):
                    shutil.move(old_path, USER_CONFIG_FILE)
                else:
                    os.remove(old_path)
            except Exception:
                pass

def auto_extract_images(folder_name="images"):
    internal_dir = os.path.join(INTERNAL_DIR, folder_name)
    external_dir = os.path.join(APP_DIR, folder_name)
    if not os.path.isdir(internal_dir):
        return
    try:
        os.makedirs(external_dir, exist_ok=True)
        for root, dirs, files in os.walk(internal_dir):
            rel_path = os.path.relpath(root, internal_dir)
            target_root = external_dir if rel_path == "." else os.path.join(external_dir, rel_path)
            os.makedirs(target_root, exist_ok=True)
            for file in files:
                src_file = os.path.join(root, file)
                dst_file = os.path.join(target_root, file)
                if not os.path.exists(dst_file):
                    shutil.copy2(src_file, dst_file)
    except Exception as e:
        print(f"[auto_extract_images] 失败: {e}")

def get_img_path(filename):
    basename = os.path.basename(filename)
    ext_path = os.path.join(APP_DIR, "images", basename)
    if os.path.exists(ext_path):
        return ext_path
    int_path = os.path.join(INTERNAL_DIR, "images", basename)
    if os.path.exists(int_path):
        return int_path
    return filename

def get_asset_path(*parts):
    asset_path = os.path.join(INTERNAL_DIR, "assets", *parts)
    if os.path.exists(asset_path):
        return asset_path
    dev_asset_path = os.path.join(get_app_dir(), "assets", *parts)
    if os.path.exists(dev_asset_path):
        return dev_asset_path
    return None

def parse_version(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except Exception:
        return (0, 0, 0)

# ==========================================
# --- Ctypes 硬件级键盘模拟结构体 ---
# ==========================================
SendInput = ctypes.windll.user32.SendInput
PUL = ctypes.POINTER(ctypes.c_ulong)

class KeyBdInput(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", PUL)]

class HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_short),
                ("wParamH", ctypes.c_ushort)]

class MouseInput(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]

class Input_I(ctypes.Union):
    _fields_ = [("ki", KeyBdInput), ("mi", MouseInput), ("hi", HardwareInput)]

class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", Input_I)]

DIK_CODES = {
    "esc": (0x01, False), "enter": (0x1C, False), "space": (0x39, False),
    "backspace": (0x0E, False), "tab": (0x0F, False), "lshift": (0x2A, False),
    "rshift": (0x36, False), "lctrl": (0x1D, False), "rctrl": (0x1D, True),
    "lalt": (0x38, False), "ralt": (0x38, True), "capslock": (0x3A, False),
    "a": (0x1E, False), "b": (0x30, False), "c": (0x2E, False), "d": (0x20, False),
    "e": (0x12, False), "f": (0x21, False), "g": (0x22, False), "h": (0x23, False),
    "i": (0x17, False), "j": (0x24, False), "k": (0x25, False), "l": (0x26, False),
    "m": (0x32, False), "n": (0x31, False), "o": (0x18, False), "p": (0x19, False),
    "q": (0x10, False), "r": (0x13, False), "s": (0x1F, False), "t": (0x14, False),
    "u": (0x16, False), "v": (0x2F, False), "w": (0x11, False), "x": (0x2D, False),
    "y": (0x15, False), "z": (0x2C, False), "1": (0x02, False), "2": (0x03, False),
    "3": (0x04, False), "4": (0x05, False), "5": (0x06, False), "6": (0x07, False),
    "7": (0x08, False), "8": (0x09, False), "9": (0x0A, False), "0": (0x0B, False),
    "up": (0xC8, True), "down": (0xD0, True), "left": (0xCB, True), "right": (0xCD, True),
    "pageup": (0xC9, True), "pagedown": (0xD1, True), "home": (0xC7, True),
    "end": (0xCF, True), "insert": (0xD2, True), "delete": (0xD3, True),
    "f1": (0x3B, False), "f2": (0x3C, False), "f3": (0x3D, False), "f4": (0x3E, False),
    "f5": (0x3F, False), "f6": (0x40, False), "f7": (0x41, False), "f8": (0x42, False),
    "f9": (0x43, False), "f10": (0x44, False), "f11": (0x57, False), "f12": (0x58, False),
}

# --- 全局配置 ---
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")
MATCH_THRESHOLD = 0.8
pyautogui.FAILSAFE = False

class FH_UltimateBot(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"FH6Auto by YSTO v{CURRENT_VERSION}")
        self.geometry("1800x800")
        self.attributes("-topmost", False)
        self.attributes("-alpha", 0.98)
        self.resizable(False, False)

        try:
            icon_path = get_asset_path("icon.ico")
            if icon_path:
                self.iconbitmap(icon_path)
        except Exception:
            pass

        self.is_running = False
        self.current_thread = None
        self.is_paused = False
        self.debug_mode = False          # 调试模式开关
        self.race_counter = 0
        self.car_counter = 0
        self.cj_counter = 0
        self.sc_count = 0
        self.global_loop_current = 0

        # 缓存
        self.template_cache = {}
        self.scaled_template_cache = {}
        self.file_template_cache = {}
        self.template_gray_cache = {}
        self.template_transparent_cache = {}
        self.last_positions = {}
        self.support_win = None
        # 日志队列
        self.log_lines = []
        # 截图缓存（极短时间复用）
        self._last_screenshot = None
        self._last_screenshot_time = 0
        self._last_screenshot_region = None

        self.init_regions()

        def background_init():
            auto_extract_images()
            self.prepare_template_cache()
        threading.Thread(target=background_init, daemon=True).start()

        auto_extract_configs()
        self.load_config()
        self.setup_ui()
        self.start_hotkey_listener()
        self.update_skill_grid()
        self.center_window()
        # 启动日志刷新定时器
        self.after(200, self._flush_log)

        self.log("免责声明：本脚本仅供 Python 自动化技术交流与学习使用。请勿用于商业盈利或破坏游戏平衡，因使用本脚本造成的账号封禁等损失，由使用者自行承担。")
        self.log("工具运行目录不要有中文")
        self.log("默认刷图车辆：【斯巴鲁Impreza 22B-STi Version】【调校R 913】【保持默认涂装】【收藏车辆】")
        self.log("启动前先将键盘设置为【英文键盘】")
        self.log("游戏设置为【自动转向】【自动挡】，游戏语言设置为【简体中文】")
        self.log("大部分以图像识别作为引导，减少机器盲目操作的风险，但仍无法完全避免，使用前请做好准备")

    # ==========================================
    # --- UI 安全调度与日志队列 ---
    # ==========================================
    def ui_call(self, func, *args, **kwargs):
        try:
            self.after(0, lambda: func(*args, **kwargs))
        except Exception:
            pass

    def log(self, message):
        """将日志加入队列，批量刷新到UI"""
        self.log_lines.append(f"[{time.strftime('%H:%M:%S')}] {message}")

    def _flush_log(self):
        if self.log_lines:
            text = "\n".join(self.log_lines) + "\n"
            try:
                self.log_box.configure(state="normal")
                self.log_box.insert("end", text)
                self.log_box.see("end")
                self.log_box.configure(state="disabled")
                if hasattr(self, "mini_log_box"):
                    self.mini_log_box.configure(state="normal")
                    self.mini_log_box.insert("end", text)
                    self.mini_log_box.see("end")
                    self.mini_log_box.configure(state="disabled")
            except Exception:
                pass
            self.log_lines.clear()
        self.after(200, self._flush_log)

    def center_window(self):
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        gx, gy, gw, gh = self.regions["全界面"]
        x = gx + (gw - w) // 2
        y = gy + (gh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def sync_buy_to_sell(self, event=None):
        try:
            val = "".join(c for c in self.entry_car.get() if c.isdigit())
            if val == "":
                val = "0"
            self.entry_sc.delete(0, "end")
            self.entry_sc.insert(0, val)
        except Exception:
            pass

    def normalize_step_entry(self, entry_widget, default_value):
        try:
            v = "".join(c for c in entry_widget.get() if c.isdigit())
            if v == "":
                v = str(default_value)
            iv = int(v)
            if iv < 1:
                iv = 1
            if iv > 4:
                iv = 4
            entry_widget.delete(0, "end")
            entry_widget.insert(0, str(iv))
        except Exception:
            entry_widget.delete(0, "end")
            entry_widget.insert(0, str(default_value))

    # ==========================================
    # --- 初始化全局 Region ---
    # ==========================================
    def init_regions(self):
        sw, sh = pyautogui.size()
        self.update_regions_by_window(0, 0, sw, sh)

    def update_regions_by_window(self, x, y, w, h):
        self.regions = {
            "全界面": (x, y, w, h),
            "左上": (x, y, w // 2, h // 2),
            "右上": (x + w // 2, y, w // 2, h // 2),
            "左下": (x, y + h // 2, w // 2, h // 2),
            "右下": (x + w // 2, y + h // 2, w // 2, h // 2),
            "上": (x, y, w, h // 2),
            "下": (x, y + h // 2, w, h // 2),
            "左": (x, y, w // 2, h),
            "右": (x + w // 2, y, w // 2, h),
            "中间": (x + w // 4, y + h // 4, w // 2, h // 2),
        }

    # ==========================================
    # --- 配置管理 ---
    # ==========================================
    def load_config(self):
        self.config = {
            "race_count": 99, "buy_count": 30, "cj_count": 30, "sc_count": 30,
            "chk_1": True, "chk_2": True, "chk_3": True, "chk_4": True,
            "next_1": 2, "next_2": 3, "next_3": 1, "next_4": 1,
            "global_loops": 10, "skill_dirs": ["right", "up", "up", "up", "left"],
            "share_code": "890169683", "auto_restart": False,
            "restart_cmd": "start steam://run/2483190", "sell_mode": 1
        }
        if os.path.exists(USER_CONFIG_FILE):
            try:
                with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
                    user_config = json.load(f)
                    self.config.update(user_config)
            except Exception:
                pass
        try:
            with open(USER_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception:
            pass

    def save_config(self):
        try:
            self.config["race_count"] = int(self.entry_race.get())
            self.config["buy_count"] = int(self.entry_car.get())
            self.config["cj_count"] = int(self.entry_cj.get())
            self.config["sc_count"] = int(self.entry_sc.get())
            self.config["global_loops"] = int(self.entry_global_loop.get())
            self.config["share_code"] = "".join(c for c in self.entry_share.get() if c.isdigit())
            self.config["next_1"] = int(self.entry_next1.get())
            self.config["next_2"] = int(self.entry_next2.get())
            self.config["next_3"] = int(self.entry_next3.get())
            self.config["next_4"] = int(self.entry_next4.get())
            if hasattr(self, "opt_sell_mode"):
                val = self.opt_sell_mode.get()
                if "模式1" in val:
                    self.config["sell_mode"] = 1
                else:
                    self.config["sell_mode"] = 2
        except Exception:
            pass
        self.config["chk_1"] = self.var_chk1.get()
        self.config["chk_2"] = self.var_chk2.get()
        self.config["chk_3"] = self.var_chk3.get()
        self.config["chk_4"] = self.var_chk4.get()
        self.config["auto_restart"] = self.var_auto_restart.get()
        self.config["restart_cmd"] = self.le_restart_cmd.get().strip()
        try:
            if hasattr(self, "entry_calc_a"):
                self.config["calc_a"] = self.entry_calc_a.get().strip()
                self.config["calc_b"] = self.entry_calc_b.get().strip()
                self.config["calc_c"] = self.entry_calc_c.get().strip()
        except Exception:
            pass
        try:
            with open(USER_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.log(f"保存配置失败: {e}")

    def auto_calculate_pipeline(self):
        val_a = self.entry_calc_a.get().strip()
        if not val_a:
            self.log("未输入CR，无需计算。")
            return
        try:
            target_cr = int(val_a)
            val_b = self.entry_calc_b.get().strip()
            cost_per_car = int(val_b) if val_b else 81700
            val_c = self.entry_calc_c.get().strip()
            sp_per_car = int(val_c) if val_c else 30
        except Exception:
            self.log("输入格式有误，请确保只输入数字！")
            return
        if cost_per_car <= 0 or sp_per_car <= 0:
            self.log("单车成本或技能点不能为 0！")
            return
        total_cars = target_cr // cost_per_car
        total_races = (total_cars * sp_per_car) // 10
        if total_races <= 0:
            self.log(f"目标金额不足(只够买{total_cars}辆车)，无法产生有效跑图！")
            return
        if total_races <= 99:
            final_loops = 1
            final_races_per_loop = total_races
        else:
            import math
            loops = math.ceil(total_races / 99)
            avg_races = total_races // loops
            if avg_races >= 70:
                final_loops = loops
                final_races_per_loop = avg_races
            else:
                final_races_per_loop = 99
                final_loops = total_races // 99
        cars_per_loop = (final_races_per_loop * 10) // sp_per_car
        if final_loops <= 0:
            self.log("计算后可用大循环次数为0。")
            return
        self.entry_race.delete(0, "end")
        self.entry_race.insert(0, str(final_races_per_loop))
        self.entry_car.delete(0, "end")
        self.entry_car.insert(0, str(cars_per_loop))
        self.entry_cj.delete(0, "end")
        self.entry_cj.insert(0, str(cars_per_loop))
        self.entry_sc.delete(0, "end")
        self.entry_sc.insert(0, str(cars_per_loop))
        self.entry_global_loop.delete(0, "end")
        self.entry_global_loop.insert(0, str(final_loops))
        self.log(f"✅计算完成: 总计需{total_cars}车, 共跑图{total_races}次。分配为: {final_loops} 个大循环, 每轮跑图 {final_races_per_loop} 次, 动作 {cars_per_loop} 辆。")
        self.save_config()

    # ==========================================
    # --- UI 布局设计 ---
    # ==========================================
    def setup_ui(self):
        self.top_container = ctk.CTkFrame(self, fg_color="transparent")
        self.top_container.pack(fill="x", padx=18, pady=(18, 10))
        self.config_frame = ctk.CTkFrame(self.top_container, fg_color="transparent")
        self.config_frame.pack(fill="x")

        def create_box(parent, title, btn_text, btn_cmd, btn_color, def_val):
            frame = ctk.CTkFrame(parent, width=210, height=300, corner_radius=12,
                                 border_width=1, border_color="#2B2B2B")
            frame.pack_propagate(False)
            frame.pack(side="left", padx=8)
            ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(weight="bold", size=20)).pack(pady=(14, 10))
            btn = ctk.CTkButton(frame, text=btn_text, fg_color=btn_color, hover_color=btn_color,
                                command=btn_cmd, width=140, height=38, corner_radius=10)
            btn.pack(pady=8, padx=10)
            entry = ctk.CTkEntry(frame, width=95, height=34, justify="center", corner_radius=8)
            entry.insert(0, str(def_val))
            entry.pack(pady=8)
            lbl = ctk.CTkLabel(frame, text=f"执行: 0 / {def_val}", text_color="#A0A0A0",
                               font=ctk.CTkFont(size=16))
            lbl.pack(pady=8)
            return frame, btn, entry, lbl

        def create_next_step(parent, var_checked, def_step, box_h=300):
            frame = ctk.CTkFrame(parent, width=120, height=box_h, corner_radius=12,
                                 border_width=1, border_color="#2B2B2B")
            frame.pack(side="left", padx=4)
            frame.pack_propagate(False)
            ctk.CTkLabel(frame, text="下一步骤", font=ctk.CTkFont(size=18, weight="bold"),
                         text_color="#5DADE2").pack(pady=(55, 10))
            entry = ctk.CTkEntry(frame, width=60, height=34, justify="center", corner_radius=8)
            entry.insert(0, str(def_step))
            entry.pack(pady=6)
            chk = ctk.CTkCheckBox(frame, text="继续", variable=var_checked, width=60)
            chk.pack(pady=8)
            return frame, entry, chk

        self.var_chk1 = ctk.BooleanVar(value=self.config["chk_1"])
        self.var_chk2 = ctk.BooleanVar(value=self.config["chk_2"])
        self.var_chk3 = ctk.BooleanVar(value=self.config["chk_3"])
        self.var_chk4 = ctk.BooleanVar(value=self.config.get("chk_4", True))

        box_race, self.btn_race, self.entry_race, self.lbl_race = create_box(
            self.config_frame, "1. 循环跑图", "开始", lambda: self.start_pipeline("race"),
            "#1F6AA5", self.config.get("race_count", 99))
        self.entry_share = ctk.CTkEntry(box_race, width=130, justify="center", placeholder_text="蓝图数字代码")
        self.entry_share.insert(0, self.config.get("share_code", "890169683"))
        self.entry_share.pack(pady=4)

        self.next_frame1, self.entry_next1, self.chk1 = create_next_step(
            self.config_frame, self.var_chk1, self.config.get("next_1", 2))

        box_car, self.btn_car, self.entry_car, self.lbl_car = create_box(
            self.config_frame, "2. 批量买车", "开始", lambda: self.start_pipeline("buy"),
            "#2EA043", self.config.get("buy_count", 30))
        self.entry_car.bind("<KeyRelease>", self.sync_buy_to_sell)

        self.next_frame2, self.entry_next2, self.chk2 = create_next_step(
            self.config_frame, self.var_chk2, self.config.get("next_2", 3))

        self.box_cj = ctk.CTkFrame(self.config_frame, width=360, height=300, corner_radius=12,
                                   border_width=1, border_color="#2B2B2B")
        self.box_cj.pack_propagate(False)
        self.box_cj.pack(side="left", padx=8)
        top_cj = ctk.CTkFrame(self.box_cj, fg_color="transparent")
        top_cj.pack(fill="x", pady=10)
        left_cj = ctk.CTkFrame(top_cj, fg_color="transparent")
        left_cj.pack(side="left", padx=10)
        ctk.CTkLabel(left_cj, text="3. 超级抽奖", font=ctk.CTkFont(weight="bold", size=20)).pack(pady=(0, 8))
        self.btn_cj = ctk.CTkButton(left_cj, text="开始", width=120, height=38, corner_radius=10,
                                    fg_color="#8E44AD", hover_color="#8E44AD",
                                    command=lambda: self.start_pipeline("cj"))
        self.btn_cj.pack(pady=5)
        self.entry_cj = ctk.CTkEntry(left_cj, width=95, height=34, justify="center", corner_radius=8)
        self.entry_cj.insert(0, str(self.config.get("cj_count", 30)))
        self.entry_cj.pack(pady=5)
        self.lbl_cj = ctk.CTkLabel(left_cj, text=f"执行: 0 / {self.config.get('cj_count', 30)}",
                                   text_color="#A0A0A0", font=ctk.CTkFont(size=14))
        self.lbl_cj.pack(pady=(2, 8))
        dir_frame = ctk.CTkFrame(left_cj, fg_color="transparent")
        dir_frame.pack(pady=4)
        for text, val in [("↑", "up"), ("↓", "down"), ("←", "left"), ("→", "right")]:
            ctk.CTkButton(dir_frame, text=text, width=30, height=28, corner_radius=8,
                          command=lambda x=val: self.add_skill_dir(x)).pack(side="left", padx=2)
        ctk.CTkButton(left_cj, text="清除矩阵", width=90, height=28, corner_radius=8,
                      fg_color="#C0392B", hover_color="#A93226", command=self.clear_skill_dir).pack(pady=8)

        self.grid_frame = ctk.CTkFrame(top_cj, fg_color="transparent")
        self.grid_frame.pack(side="right", padx=12)
        self.grid_labels = [[None] * 4 for _ in range(4)]
        for r in range(4):
            for c in range(4):
                lbl = ctk.CTkLabel(self.grid_frame, text="", width=28, height=28,
                                   corner_radius=5, fg_color="#444444")
                lbl.grid(row=r, column=c, padx=4, pady=4)
                self.grid_labels[r][c] = lbl
        ctk.CTkLabel(self.grid_frame, text="技能树", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#A0A0A0").grid(row=4, column=0, columnspan=4, pady=(8, 0))

        self.next_frame3, self.entry_next3, self.chk3 = create_next_step(
            self.config_frame, self.var_chk3, self.config.get("next_3", 4))

        box_sc, self.btn_sc, self.entry_sc, self.lbl_sc = create_box(
            self.config_frame, "4. 移除车辆", "！！开始！！", lambda: self.start_pipeline("sell"),
            "#D97706", self.config.get("sc_count", 30))
        self.opt_sell_mode = ctk.CTkOptionMenu(box_sc, values=["模式1: 识图移除模式", "模式2: 移除最近添加"],
                                               width=180, height=28, corner_radius=6,
                                               font=ctk.CTkFont(size=12), fg_color="#D97706",
                                               button_color="#B96705", button_hover_color="#995704")
        saved_mode = self.config.get("sell_mode", 1)
        if str(saved_mode) == "1" or "模式1" in str(saved_mode):
            self.opt_sell_mode.set("模式1: 识图移除模式")
        else:
            self.opt_sell_mode.set("模式2: 移除最近添加")
        self.opt_sell_mode.pack(pady=4)

        self.next_frame4, self.entry_next4, self.chk4 = create_next_step(
            self.config_frame, self.var_chk4, self.config.get("next_4", 1))

        self.global_settings_frame = ctk.CTkFrame(self, fg_color="#2B2B2B", height=45, corner_radius=10)
        self.global_settings_frame.pack(fill="x", padx=18, pady=(15, 0))
        self.global_settings_frame.pack_propagate(False)
        ctk.CTkLabel(self.global_settings_frame, text="⚙️ 循环与守护设置",
                     font=ctk.CTkFont(weight="bold", size=15), text_color="#F1C40F").pack(side="left", padx=(15, 20))
        ctk.CTkLabel(self.global_settings_frame, text="大循环次数:").pack(side="left", padx=(10, 5))
        self.entry_global_loop = ctk.CTkEntry(self.global_settings_frame, width=70, height=28, justify="center")
        self.entry_global_loop.insert(0, str(self.config.get("global_loops", 10)))
        self.entry_global_loop.pack(side="left", padx=(0, 20))
        self.var_auto_restart = ctk.BooleanVar(value=self.config.get("auto_restart", True))
        self.cb_auto_restart = ctk.CTkCheckBox(self.global_settings_frame, text="游戏闪退（爆显存）自动重启",
                                               variable=self.var_auto_restart)
        self.cb_auto_restart.pack(side="left", padx=(10, 20))
        ctk.CTkLabel(self.global_settings_frame, text="启动命令(CMD):").pack(side="left", padx=(10, 5))
        self.le_restart_cmd = ctk.CTkEntry(self.global_settings_frame, width=250, height=28)
        self.le_restart_cmd.insert(0, self.config.get("restart_cmd", "start steam://run/2483190"))
        self.le_restart_cmd.pack(side="left", padx=(0, 20))

        self.calc_frame = ctk.CTkFrame(self, fg_color="#2B2B2B", height=45, corner_radius=10)
        self.calc_frame.pack(fill="x", padx=18, pady=(10, 0))
        self.calc_frame.pack_propagate(False)
        ctk.CTkLabel(self.calc_frame, text="次数计算器", font=ctk.CTkFont(weight="bold", size=15),
                     text_color="#2EA043").pack(side="left", padx=(15, 20))
        ctk.CTkLabel(self.calc_frame, text="CR:").pack(side="left", padx=(0, 5))
        self.entry_calc_a = ctk.CTkEntry(self.calc_frame, width=110, height=28, placeholder_text="留空不计算")
        self.entry_calc_a.insert(0, self.config.get("calc_a", ""))
        self.entry_calc_a.pack(side="left", padx=(0, 15))
        ctk.CTkLabel(self.calc_frame, text="单车成本(CR):").pack(side="left", padx=(0, 5))
        self.entry_calc_b = ctk.CTkEntry(self.calc_frame, width=70, height=28)
        self.entry_calc_b.insert(0, self.config.get("calc_b", "81700"))
        self.entry_calc_b.pack(side="left", padx=(0, 15))
        ctk.CTkLabel(self.calc_frame, text="单车技能点:").pack(side="left", padx=(0, 5))
        self.entry_calc_c = ctk.CTkEntry(self.calc_frame, width=50, height=28)
        self.entry_calc_c.insert(0, self.config.get("calc_c", "30"))
        self.entry_calc_c.pack(side="left", padx=(0, 15))
        ctk.CTkButton(self.calc_frame, text="计算并应用", width=90, height=28,
                      fg_color="#D35400", hover_color="#A04000", command=self.auto_calculate_pipeline).pack(side="left", padx=(0, 15))

        def limit_len(evt, widget, max_l):
            val = "".join(c for c in widget.get() if c.isdigit())
            if len(val) > max_l:
                val = val[:max_l]
            if widget.get() != val:
                widget.delete(0, "end")
                widget.insert(0, val)
        self.entry_calc_a.bind("<KeyRelease>", lambda e: limit_len(e, self.entry_calc_a, 10))
        self.entry_calc_b.bind("<KeyRelease>", lambda e: limit_len(e, self.entry_calc_b, 7))
        self.entry_calc_c.bind("<KeyRelease>", lambda e: limit_len(e, self.entry_calc_c, 2))

        self.entry_next1.bind("<FocusOut>", lambda e: self.normalize_step_entry(self.entry_next1, 2))
        self.entry_next2.bind("<FocusOut>", lambda e: self.normalize_step_entry(self.entry_next2, 3))
        self.entry_next3.bind("<FocusOut>", lambda e: self.normalize_step_entry(self.entry_next3, 4))
        self.entry_next4.bind("<FocusOut>", lambda e: self.normalize_step_entry(self.entry_next4, 1))

        if not self.entry_sc.get().strip():
            self.entry_sc.insert(0, "30")

        # 迷你 UI
        self.mini_frame = ctk.CTkFrame(self, fg_color="#1E1E1E", corner_radius=10)
        self.mini_log_box = ctk.CTkTextbox(self.mini_frame, state="disabled", wrap="word",
                                           font=ctk.CTkFont(size=13), fg_color="#2B2B2B")
        self.mini_log_box.pack(side="left", fill="both", expand=True, padx=(10, 5), pady=10)

        self.mini_info_frame = ctk.CTkFrame(self.mini_frame, fg_color="transparent")
        self.mini_info_frame.pack(side="left", fill="y", padx=5, pady=10)
        self.lbl_mini_task = ctk.CTkLabel(self.mini_info_frame, text="当前任务: 等待中",
                                          font=ctk.CTkFont(size=14, weight="bold"), text_color="#3498DB")
        self.lbl_mini_task.pack(pady=(5, 2), anchor="w")
        self.lbl_mini_prog = ctk.CTkLabel(self.mini_info_frame, text="任务进度: 0 / 0", font=ctk.CTkFont(size=13))
        self.lbl_mini_prog.pack(pady=2, anchor="w")
        self.lbl_mini_loop = ctk.CTkLabel(self.mini_info_frame, text="大循环: 0 / 0", font=ctk.CTkFont(size=13))
        self.lbl_mini_loop.pack(pady=2, anchor="w")
        self.lbl_mini_time = ctk.CTkLabel(self.mini_info_frame, text="总耗时: 00:00:00", font=ctk.CTkFont(size=13))
        self.lbl_mini_time.pack(pady=2, anchor="w")

        self.btn_mini_stop = ctk.CTkButton(self.mini_frame, text="⏸ 停止 (F8)", fg_color="#DA3633",
                                           hover_color="#B02A37", width=90, font=ctk.CTkFont(weight="bold"),
                                           command=self.stop_all)
        self.btn_mini_stop.pack(side="left", fill="y", padx=5, pady=10)
        self.btn_mini_pause = ctk.CTkButton(self.mini_frame, text="⏸ 暂停 (F9)", fg_color="#F1C40F",
                                            hover_color="#D4AC0D", width=90, font=ctk.CTkFont(weight="bold"),
                                            command=self.toggle_pause)
        self.btn_mini_pause.pack(side="left", fill="y", padx=5, pady=10)
        self.btn_mini_support = ctk.CTkButton(self.mini_frame, text="❤ 支持", fg_color="#F97316",
                                              hover_color="#EA580C", width=60, font=ctk.CTkFont(weight="bold"),
                                              command=self.open_support_window)
        self.btn_mini_support.pack(side="left", fill="y", padx=(5, 10), pady=10)

        self.bottom_frame = ctk.CTkFrame(self, fg_color="transparent", height=200)
        self.bottom_frame.pack(fill="both", expand=True, padx=18, pady=(6, 12))
        self.btn_stop = ctk.CTkButton(self.bottom_frame, text="⏸ 等待指令 (F8)", fg_color="#3A3A3A",
                                      hover_color="#4A4A4A", width=180, height=60, corner_radius=12,
                                      font=ctk.CTkFont(size=16, weight="bold"), command=self.stop_all)
        self.btn_stop.pack(side="left", padx=6)
        self.log_box = ctk.CTkTextbox(self.bottom_frame, state="disabled", wrap="word",
                                      corner_radius=12, height=120, font=ctk.CTkFont(size=18))
        self.log_box.pack(side="left", fill="both", expand=True, padx=8)
        self.btn_support = ctk.CTkButton(self, text="❤ 支持作者 / 检查更新", fg_color="#F97316",
                                         hover_color="#EA580C", height=42, corner_radius=12,
                                         font=ctk.CTkFont(weight="bold", size=15), command=self.open_support_window)
        self.btn_support.pack(fill="x", padx=18, pady=(6, 12))
        self.sync_buy_to_sell()

    # ==========================================
    # --- 支持窗口与更新 ---
    # ==========================================
    def open_support_window(self):
        if self.support_win is not None and self.support_win.winfo_exists():
            self.support_win.focus()
            return
        self.support_win = ctk.CTkToplevel(self)
        self.support_win.title("感谢支持 & 更新")
        self.support_win.geometry("340x520")
        self.support_win.attributes("-topmost", True)
        self.support_win.resizable(False, False)
        try:
            icon_path = get_asset_path("icon.ico")
            if icon_path:
                self.support_win.iconbitmap(icon_path)
        except Exception:
            pass
        self.support_win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 340) // 2
        y = self.winfo_y() + (self.winfo_height() - 520) // 2
        self.support_win.geometry(f"+{x}+{y}")
        ctk.CTkLabel(self.support_win, text="感谢您的支持与鼓励",
                     font=ctk.CTkFont(weight="bold", size=18), text_color="#F97316").pack(pady=(20, 6))
        ctk.CTkLabel(self.support_win, text="您的支持是我持续优化的动力！", font=ctk.CTkFont(size=12)).pack(pady=4)
        qr_path = get_asset_path("qrcode.png")
        try:
            if qr_path and os.path.exists(qr_path):
                img = Image.open(qr_path)
                qr_img = ctk.CTkImage(light_image=img, size=(210, 210))
                qr_label = ctk.CTkLabel(self.support_win, text="", image=qr_img)
                qr_label.image = qr_img
                qr_label.pack(pady=10)
            else:
                ctk.CTkLabel(self.support_win, text="（未找到内置 qrcode.png）", text_color="gray").pack(pady=40)
        except Exception:
            ctk.CTkLabel(self.support_win, text="（二维码加载失败）", text_color="gray").pack(pady=40)
        ctk.CTkButton(self.support_win, text="前往 爱发电 赞助主页", fg_color="#8E44AD", hover_color="#7D3C98",
                      command=lambda: webbrowser.open("https://ifdian.net/a/yousto")).pack(pady=5)
        ctk.CTkFrame(self.support_win, height=2, fg_color="#333333").pack(fill="x", padx=20, pady=10)
        self.lbl_version = ctk.CTkLabel(self.support_win, text=f"当前版本: v{CURRENT_VERSION}",
                                        text_color="gray", font=ctk.CTkFont(size=12))
        self.lbl_version.pack()

        def check_update_logic():
            self.ui_call(self.lbl_version.configure, text="正在连接 Github...", text_color="#3498DB")
            try:
                url = "https://raw.githubusercontent.com/YOUSTHEONE/FH6Auto/refs/heads/main/version.json"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    remote_ver = data.get("version", "0.0.0")
                    remote_url = data.get("url", "")
                    if parse_version(remote_ver) > parse_version(CURRENT_VERSION):
                        if remote_url.startswith("https://github.com/YOUSTHEONE/") or remote_url.startswith("https://ifdian.net/"):
                            self.ui_call(self.lbl_version.configure, text=f"发现新版本 v{remote_ver}，已打开浏览器！", text_color="#2EA043")
                            webbrowser.open(remote_url)
                        else:
                            self.ui_call(self.lbl_version.configure, text="发现更新，但链接不可信，已拦截", text_color="#DA3633")
                    else:
                        self.ui_call(self.lbl_version.configure, text=f"当前已是最新版本 (v{CURRENT_VERSION})", text_color="gray")
                else:
                    self.ui_call(self.lbl_version.configure, text="检查更新失败 (服务器异常)", text_color="#DA3633")
            except Exception:
                self.ui_call(self.lbl_version.configure, text="检查更新失败 (网络超时或无法访问)", text_color="#DA3633")

        btn_frame = ctk.CTkFrame(self.support_win, fg_color="transparent")
        btn_frame.pack(pady=6)
        ctk.CTkButton(btn_frame, text="检查更新", width=100, height=30, fg_color="#444444", hover_color="#555555",
                      command=lambda: threading.Thread(target=check_update_logic, daemon=True).start()).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="GitHub", width=100, height=30, fg_color="#2EA043", hover_color="#238636",
                      command=lambda: webbrowser.open("https://github.com/YOUSTHEONE/FH6Auto")).pack(side="left", padx=5)

    def update_timer(self):
        if not self.is_running:
            return
        elapsed = int(time.time() - self.start_time)
        hrs = elapsed // 3600
        mins = (elapsed % 3600) // 60
        secs = elapsed % 60
        time_str = f"总耗时: {hrs:02d}:{mins:02d}:{secs:02d}"
        try:
            self.lbl_mini_time.configure(text=time_str)
        except Exception:
            pass
        if self.is_running:
            self.after(1000, self.update_timer)

    def update_running_ui(self, task_name="", current_val=0, max_val=0):
        try:
            if task_name:
                self.ui_call(self.lbl_mini_task.configure, text=f"当前任务: {task_name}")
            if max_val > 0:
                self.ui_call(self.lbl_mini_prog.configure, text=f"执行进度: {current_val} / {max_val}")
        except Exception:
            pass

    # ==========================================
    # --- 核心操作与流程控制 ---
    # ==========================================
    def hw_key_down(self, key):
        if key not in DIK_CODES:
            return
        scan_code, extended = DIK_CODES[key]
        flags = 0x0008 | (0x0001 if extended else 0)
        extra = ctypes.c_ulong(0)
        ii_ = Input_I()
        ii_.ki = KeyBdInput(0, scan_code, flags, 0, ctypes.pointer(extra))
        x = Input(ctypes.c_ulong(1), ii_)
        SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

    def hw_key_up(self, key):
        if key not in DIK_CODES:
            return
        scan_code, extended = DIK_CODES[key]
        flags = 0x000A | (0x0001 if extended else 0)
        extra = ctypes.c_ulong(0)
        ii_ = Input_I()
        ii_.ki = KeyBdInput(0, scan_code, flags, 0, ctypes.pointer(extra))
        x = Input(ctypes.c_ulong(1), ii_)
        SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

    def hw_press(self, key, delay=0.08):
        self.check_pause()
        if not self.is_running:
            return
        self.hw_key_down(key)
        time.sleep(delay)
        self.hw_key_up(key)

    def hw_mouse_move(self, x, y):
        SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
        left = ctypes.windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        top = ctypes.windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        width = ctypes.windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        height = ctypes.windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if width == 0 or height == 0:
            return
        calc_x = int((x - left) * 65535 / width)
        calc_y = int((y - top) * 65535 / height)
        flags = 0x0001 | 0x8000 | 0x4000
        extra = ctypes.c_ulong(0)
        ii_ = Input_I()
        ii_.mi = MouseInput(calc_x, calc_y, 0, flags, 0, ctypes.pointer(extra))
        cmd = Input(ctypes.c_ulong(0), ii_)
        SendInput(1, ctypes.pointer(cmd), ctypes.sizeof(cmd))

    def game_click(self, pos, double=False):
        self.check_pause()
        if not self.is_running or not pos:
            return
        x, y = int(pos[0]), int(pos[1])
        self.hw_mouse_move(x, y)
        time.sleep(0.2)
        for _ in range(2 if double else 1):
            pydirectinput.mouseDown()
            time.sleep(0.1)
            pydirectinput.mouseUp()
            time.sleep(0.1)
        time.sleep(0.1)
        try:
            gx, gy, gw, gh = self.regions["全界面"]
            self.hw_mouse_move(gx + 5, gy + 5)
        except Exception:
            self.hw_mouse_move(5, 5)
        time.sleep(0.2)

    def move_to_game_coord(self, x, y):
        try:
            gx, gy, gw, gh = self.regions["全界面"]
            abs_x = gx + x
            abs_y = gy + y
            self.hw_mouse_move(abs_x, abs_y)
        except Exception:
            self.hw_mouse_move(x, y)

    def add_skill_dir(self, direction):
        self.config["skill_dirs"].append(direction)
        self.update_skill_grid()
        self.save_config()

    def clear_skill_dir(self):
        self.config["skill_dirs"].clear()
        self.update_skill_grid()
        self.save_config()

    def update_skill_grid(self):
        for r in range(4):
            for c in range(4):
                self.grid_labels[r][c].configure(fg_color="#333333")
        curr_r, curr_c = 3, 0
        self.grid_labels[curr_r][curr_c].configure(fg_color="#3498DB")
        valid_dirs = []
        for d in self.config["skill_dirs"]:
            if d == "up":
                curr_r -= 1
            elif d == "down":
                curr_r += 1
            elif d == "left":
                curr_c -= 1
            elif d == "right":
                curr_c += 1
            if 0 <= curr_r < 4 and 0 <= curr_c < 4:
                self.grid_labels[curr_r][curr_c].configure(fg_color="#3498DB")
                valid_dirs.append(d)
            else:
                break
        self.config["skill_dirs"] = valid_dirs

    def start_pipeline(self, start_step):
        if self.is_running:
            return
        self.is_running = True
        self.save_config()
        # 隐藏大窗
        self.config_frame.pack_forget()
        self.global_settings_frame.pack_forget()
        self.calc_frame.pack_forget()
        self.top_container.pack_forget()
        if hasattr(self, "bottom_frame"):
            self.bottom_frame.pack_forget()
        self.btn_support.pack_forget()
        self.mini_frame.pack(fill="both", expand=True, padx=10, pady=10)

        last_x, last_y, last_w, last_h = self.regions["全界面"]
        if last_w <= 0:
            last_w = self.winfo_screenwidth()
        if last_h <= 0:
            last_h = self.winfo_screenheight()
        calc_w = int(last_w * 0.40)
        calc_h = int(last_h * 0.15)
        calc_w = max(calc_w, 650)
        calc_h = max(calc_h, 150)
        pos_x = last_x + last_w - calc_w - 20
        pos_y = last_y + 20
        self.attributes("-topmost", True)
        self.geometry(f"{calc_w}x{calc_h}+{pos_x}+{pos_y}")

        self.start_time = time.time()
        self.update_timer()
        self.update_running_ui("初始化中...")
        self.race_counter = 0
        self.car_counter = 0
        self.cj_counter = 0
        self.sc_count = 0
        self.global_loop_current = 0

        def runner():
            if not self.check_and_focus_game():
                self.stop_all()
                return
            steps = ["race", "buy", "cj", "sell"]
            curr_idx = steps.index(start_step)
            try:
                total_loops = int(self.entry_global_loop.get())
            except Exception:
                total_loops = self.config.get("global_loops", 10)
            self.global_loop_current = 1
            self.ui_call(self.lbl_mini_loop.configure, text=f"大循环: {self.global_loop_current} / {total_loops}")
            continuous_failures = 0
            MAX_RECOVERIES = 10

            while self.is_running:
                step_name = steps[curr_idx]
                success = False
                try:
                    if step_name == "race":
                        success = self.logic_race(int(self.entry_race.get()))
                    elif step_name == "buy":
                        success = self.logic_buy_car(int(self.entry_car.get()))
                    elif step_name == "cj":
                        success = self.logic_super_wheelspin(int(self.entry_cj.get()))
                    elif step_name == "sell":
                        sell_mode = self.opt_sell_mode.get()
                        if "模式1" in sell_mode:
                            success = self.find_and_remove_consumable_car(int(self.entry_sc.get()))
                        else:
                            success = self.sell_consumable_car(int(self.entry_sc.get()))
                except Exception as e:
                    self.log(f"执行模块 {step_name} 时异常: {e}")
                    success = False

                if not self.is_running:
                    break
                if not success:
                    continuous_failures += 1
                    if continuous_failures > MAX_RECOVERIES:
                        self.log(f"!!! 警告：连续 {continuous_failures} 次触发断点恢复仍未能解决问题！")
                        self.log("为防止游戏陷入死循环，强制终止当前所有任务，请人工检查游戏状态。")
                        break
                    self.log(f"正在进行全局恢复 (第 {continuous_failures}/{MAX_RECOVERIES} 次允许的重试)...")
                    if self.attempt_recovery():
                        continue
                    else:
                        self.log("致命错误：连退回菜单/重启也失败了，彻底停止。")
                        break
                else:
                    continuous_failures = 0

                # 步骤流转
                next_idx = curr_idx + 1
                if curr_idx == 0:
                    if self.var_chk1.get():
                        try:
                            next_idx = max(0, min(3, int(self.entry_next1.get()) - 1))
                        except Exception:
                            next_idx = 1
                    else:
                        break
                elif curr_idx == 1:
                    if self.var_chk2.get():
                        try:
                            next_idx = max(0, min(3, int(self.entry_next2.get()) - 1))
                        except Exception:
                            next_idx = 2
                    else:
                        break
                elif curr_idx == 2:
                    if self.var_chk3.get():
                        try:
                            next_idx = max(0, min(3, int(self.entry_next3.get()) - 1))
                        except Exception:
                            next_idx = 3
                    else:
                        break
                elif curr_idx == 3:
                    if self.var_chk4.get():
                        try:
                            next_idx = max(0, min(3, int(self.entry_next4.get()) - 1))
                        except Exception:
                            next_idx = 0
                    else:
                        break

                if next_idx <= curr_idx:
                    self.global_loop_current += 1
                    if self.global_loop_current > total_loops:
                        self.log("达到设定的总循环次数，任务圆满结束。")
                        break
                    self.log(f"开启新一轮大循环 ({self.global_loop_current}/{total_loops})")
                    self.ui_call(self.lbl_mini_loop.configure, text=f"大循环: {self.global_loop_current} / {total_loops}")
                    self.race_counter = 0
                    self.car_counter = 0
                    self.cj_counter = 0
                    self.sc_count = 0
                curr_idx = next_idx
            self.stop_all()

        self.current_thread = threading.Thread(target=runner, daemon=True)
        self.current_thread.start()

    def stop_all(self):
        if not self.is_running:
            return
        self.is_running = False
        self.is_paused = False
        for key in DIK_CODES.keys():
            self.hw_key_up(key)
        for key in ["w", "e", "y", "enter", "esc", "up", "down", "left", "right", "space", "backspace"]:
            self.hw_key_up(key)
        try:
            pydirectinput.mouseUp()
        except Exception:
            pass

        def restore_ui():
            if hasattr(self, "mini_frame"):
                self.mini_frame.pack_forget()
            self.config_frame.pack_forget()
            self.global_settings_frame.pack_forget()
            self.calc_frame.pack_forget()
            self.top_container.pack(fill="x", padx=18, pady=(18, 10))
            self.config_frame.pack(fill="x")
            self.global_settings_frame.pack(fill="x", pady=(15, 0))
            self.calc_frame.pack(fill="x", pady=(10, 0))
            if hasattr(self, "bottom_frame"):
                self.bottom_frame.pack(fill="both", expand=True, padx=18, pady=(6, 12))
            self.btn_support.pack(fill="x", padx=18, pady=(6, 12))
            self.btn_stop.configure(text="等待指令 (F8)", fg_color="#3A3A3A", hover_color="#4A4A4A")
            self.attributes("-topmost", False)
            self.geometry("1800x800")
            self.center_window()

        self.ui_call(restore_ui)
        self.log("!!! 任务已停止，所有物理按键状态已强制重置")

    def start_test_boot(self):
        if self.is_running:
            self.log("已有任务正在运行，请先点击停止后再测试启动流程！")
            return
        self.is_running = True
        self.save_config()
        self.config_frame.pack_forget()
        self.global_settings_frame.pack_forget()
        self.calc_frame.pack_forget()
        self.top_container.pack_forget()
        if hasattr(self, "bottom_frame"):
            self.bottom_frame.pack_forget()
        self.btn_support.pack_forget()
        self.mini_frame.pack(fill="both", expand=True, padx=10, pady=10)
        self.update_running_ui("测试启动流程...")
        self.start_time = time.time()
        self.update_timer()
        self.log("====== 开始独立测试自动开机与识别流程 ======")

        def test_runner():
            success = self.restart_game_and_boot(force_test=True)
            if success:
                self.log("✅ 测试结束：自动开机、A/B/C状态机识别并到达菜单完美跑通！")
            else:
                self.log("❌ 测试结束：自动开机流程失败，请检查截图或日志。")
            self.stop_all()
        self.current_thread = threading.Thread(target=test_runner, daemon=True)
        self.current_thread.start()

    def toggle_pause(self):
        if not self.is_running:
            return
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.log("⏸ 任务已暂停 (按 F9 或点击按钮恢复)")
            for key in ["w", "e", "y", "enter", "esc", "up", "down", "left", "right", "space", "backspace"]:
                self.hw_key_up(key)
            try:
                pydirectinput.mouseUp()
            except Exception:
                pass
            self.ui_call(self.btn_mini_pause.configure, text="▶ 继续 (F9)", fg_color="#2EA043", hover_color="#238636")
        else:
            self.log("▶ 任务已恢复")
            self.ui_call(self.btn_mini_pause.configure, text="⏸ 暂停 (F9)", fg_color="#F1C40F", hover_color="#D4AC0D")

    def check_pause(self):
        while self.is_paused and self.is_running:
            time.sleep(0.1)

    def start_hotkey_listener(self):
        def hotkey_thread():
            def on_press(k):
                if k == keyboard.Key.f8:
                    self.stop_all()
                elif k == keyboard.Key.f9:
                    self.toggle_pause()
            with keyboard.Listener(on_press=on_press) as listener:
                listener.join()
        threading.Thread(target=hotkey_thread, daemon=True).start()

    # ==========================================
    # --- 逻辑保障 ---
    # ==========================================
    def set_english_input(self):
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return
            hkl = ctypes.windll.user32.LoadKeyboardLayoutW("00000409", 1)
            ctypes.windll.user32.PostMessageW(hwnd, 0x0050, 0, hkl)
            WM_IME_CONTROL = 0x0283
            IMC_SETOPENSTATUS = 0x0006
            ctypes.windll.user32.SendMessageW(hwnd, WM_IME_CONTROL, IMC_SETOPENSTATUS, 0)
            self.log("已自动切换英文键盘/关闭中文输入法状态。")
        except Exception as e:
            self.log(f"自动防中文输入设置失败: {e}")

    def check_and_focus_game(self):
        self.log("检查游戏进程 (forzahorizon6.exe)...")
        try:
            CREATE_NO_WINDOW = 0x08000000
            cmd = 'tasklist /FI "IMAGENAME eq forzahorizon6.exe" /NH /FO CSV'
            output = subprocess.check_output(cmd, shell=True, text=True, creationflags=CREATE_NO_WINDOW)
            if "forzahorizon6.exe" not in output.lower():
                self.log("未发现 forzahorizon6.exe 进程！(请确保游戏已运行)")
                return False
            target_pid = None
            for line in output.strip().split("\n"):
                parts = line.split('","')
                if len(parts) >= 2 and "forzahorizon6.exe" in parts[0].lower():
                    target_pid = int(parts[1].replace('"', ""))
                    break
            if not target_pid:
                self.log("找到进程但无法解析PID！")
                return False
            hwnds = []
            def foreach_window(hwnd, lParam):
                if ctypes.windll.user32.IsWindowVisible(hwnd):
                    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        window_pid = ctypes.c_ulong()
                        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
                        if window_pid.value == target_pid:
                            hwnds.append(hwnd)
                return True
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            ctypes.windll.user32.EnumWindows(EnumWindowsProc(foreach_window), 0)
            if hwnds:
                hwnd = hwnds[0]
                if ctypes.windll.user32.IsIconic(hwnd):
                    ctypes.windll.user32.ShowWindow(hwnd, 9)
                else:
                    ctypes.windll.user32.ShowWindow(hwnd, 5)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                time.sleep(0.5)
                self.set_english_input()
                try:
                    client_rect = win32gui.GetClientRect(hwnd)
                    pt = win32gui.ClientToScreen(hwnd, (0, 0))
                    gx, gy = pt[0], pt[1]
                    gw, gh = client_rect[2], client_rect[3]
                    if gw < 1000 or gh < 600:
                        self.log(f"拦截到过小窗口 ({gw}x{gh})，判定为启动闪屏，等待主窗口加载...")
                        return False
                    self.update_regions_by_window(gx, gy, gw, gh)
                    MONITOR_DEFAULTTONEAREST = 2
                    hMonitor = ctypes.windll.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
                    class RECT(ctypes.Structure):
                        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
                    class MONITORINFO(ctypes.Structure):
                        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                                    ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]
                    mi = MONITORINFO()
                    mi.cbSize = ctypes.sizeof(MONITORINFO)
                    if ctypes.windll.user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
                        mx = mi.rcMonitor.left
                        my = mi.rcMonitor.top
                        mw = mi.rcMonitor.right - mi.rcMonitor.left
                        mh = mi.rcMonitor.bottom - mi.rcMonitor.top
                    else:
                        mx, my, mw, mh = gx, gy, gw, gh
                    def snap_to_game():
                        if self.is_running:
                            calc_w = int(mw * 0.40)
                            calc_h = int(mh * 0.15)
                            calc_w = max(calc_w, 650)
                            calc_h = max(calc_h, 150)
                            pos_x = mx + mw - calc_w - 20
                            pos_y = my + 20
                            self.geometry(f"{calc_w}x{calc_h}+{pos_x}+{pos_y}")
                    self.ui_call(snap_to_game)
                except Exception as e:
                    self.log(f"获取窗口坐标失败: {e}")
                time.sleep(1.0)
                return True
        except Exception as e:
            self.log(f"检查进程异常: {e}")
            return False
        return False

    def restart_game_and_boot(self, force_test=False):
        if not force_test:
            auto_restart = getattr(self, "var_auto_restart", None)
            if auto_restart is None or not auto_restart.get():
                self.log("未开启自动重启，任务结束。")
                return False
        self.log("触发启动机制！正在拉起游戏...")
        try:
            cmd_widget = getattr(self, "le_restart_cmd", None)
            cmd_str = cmd_widget.get() if cmd_widget else self.config.get("restart_cmd", "start steam://run/2483190")
            os.system(cmd_str)
        except Exception as e:
            self.log(f"执行启动命令失败: {e}")
            return False
        self.log("等待游戏进程出现 (最多60秒)...")
        process_found = False
        for _ in range(120):
            if hasattr(self, "check_pause"):
                self.check_pause()
            if not self.is_running:
                return False
            if self.check_and_focus_game():
                process_found = True
                break
            time.sleep(1)
        if not process_found:
            self.log("未检测到游戏进程，启动失败。")
            return False
        self.log("游戏进程已启动，进入动态识别阶段 (限制5分钟)...")
        start_time = time.time()
        passed_screen_1 = False
        last_continue_time = 0
        while self.is_running and time.time() - start_time < 300:
            if hasattr(self, "check_pause"):
                self.check_pause()
            if not passed_screen_1:
                pos_h6 = None
                pos_h6 = self.find_image_transparent("horizon6.png", region=self.regions["全界面"], threshold=0.60, fast_mode=False)
                if not pos_h6:
                    try:
                        screen_bgr = self.capture_region(self.regions["全界面"])
                        tpl_bgr, _ = self.load_template("horizon6.png")
                        if tpl_bgr is not None:
                            screen_edge = self.to_edge_image(screen_bgr)
                            tpl_edge = self.to_edge_image(tpl_bgr)
                            for scale in self.get_scales_to_try(fast_mode=False):
                                t_e = tpl_edge if scale == 1.0 else cv2.resize(tpl_edge, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                                h, w = t_e.shape[:2]
                                if h > screen_edge.shape[0] or w > screen_edge.shape[1] or h < 5 or w < 5:
                                    continue
                                res = cv2.matchTemplate(screen_edge, t_e, cv2.TM_CCOEFF_NORMED)
                                _, max_val, _, max_loc = cv2.minMaxLoc(res)
                                if max_val >= 0.40:
                                    self.log(f"[轮廓黑科技] 无视背景命中！得分: {max_val:.2f} 缩放: {scale:.2f}")
                                    pos_h6 = (max_loc[0] + w//2 + self.regions["全界面"][0],
                                              max_loc[1] + h//2 + self.regions["全界面"][1])
                                    break
                    except Exception:
                        pass
                if pos_h6:
                    self.log("✅ 成功识别到 画面1 (horizon6.png)，按下【回车键】...")
                    time.sleep(1)
                    for _ in range(2):
                        self.hw_press("enter")
                        time.sleep(1)
                    passed_screen_1 = True
                    last_continue_time = time.time()
                    self.log("已确认画面1，强制等待 10 秒等待画面2加载...")
                    time.sleep(10)
                    continue
                else:
                    self.log("未找到画面1。正在使用全比例深度扫描...")
            if passed_screen_1:
                pos_continue = self.find_any_image_gray(["continue-b.png", "continue-w.png"], threshold=0.75)
                if pos_continue:
                    self.log("识别到 画面2 (继续按钮)，进行点击...")
                    self.game_click(pos_continue)
                    last_continue_time = time.time()
                    time.sleep(3.0)
                    continue
                time_since_last_seen = time.time() - last_continue_time
                if time_since_last_seen >= 30.0:
                    self.log("✅ 已经连续 30 秒未再发现继续按钮，判定为漫游载入完毕！开始尝试进入菜单...")
                    if self.enter_menu():
                        self.log("🎉 验证成功：已成功进入游戏主菜单！启动流程完美结束。")
                        return True
                    else:
                        self.log("普通进入菜单失败(可能还在黑屏或有新弹窗)，重置 30秒倒计时，继续观察...")
                        last_continue_time = time.time()
            time.sleep(1.0)
        self.log("自动启动超时(5分钟)，放弃抢救。")
        return False

    def attempt_recovery(self):
        self.log("任务执行异常中断，准备执行断点恢复流程...")
        if not self.check_and_focus_game():
            if not self.restart_game_and_boot():
                return False
        else:
            if not self.advanced_enter_menu():
                self.log("高级动态退回失败(可能游戏卡死或致命报错)，无法继续，请人工干预。")
                return False
        self.log("环境重置成功！即将从中断处继续剩余任务。")
        return True

    def is_in_menu(self):
        return self.find_image_gray("collectionjournal.png", region=self.regions["左"], threshold=0.70, fast_mode=True)

    def enter_menu(self):
        self.log("正在尝试进入主菜单 (按ESC验证)...")
        for i in range(60):
            if not self.is_running:
                return False
            if self.is_in_menu():
                self.log(f"成功定位到菜单锚点！({i + 1}/60)")
                time.sleep(0.5)
                return True
            self.log(f"未在主菜单，按下 ESC... ({i + 1}/60)")
            self.hw_press("esc")
            time.sleep(1.0)
        self.log("60 次 ESC 尝试均未进入菜单，请检查游戏状态。")
        return False

    def advanced_enter_menu(self):
        self.log("正在使用【高级恢复模式】尝试退回主菜单...")
        obstacles_dir = os.path.join("images", "obstacles")
        dynamic_obstacles = []
        if os.path.exists(obstacles_dir):
            for file in os.listdir(obstacles_dir):
                if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                    dynamic_obstacles.append(f"obstacles/{file}")
        if not dynamic_obstacles:
            self.log("提示：images/obstacles/ 文件夹为空或不存在，将只使用 ESC 退回。")
        for i in range(80):
            if hasattr(self, "check_pause"):
                self.check_pause()
            if not self.is_running:
                return False
            if self.is_in_menu():
                self.log(f"成功定位到菜单锚点！(尝试次数: {i + 1})")
                time.sleep(0.5)
                return True
            if self.find_image_gray("VRAMNE.png", region=self.regions["全界面"], threshold=0.75, fast_mode=True):
                self.log("!!! 严重警告: 检测到显存不足 (VRAMNE.png) 报错！")
                self.log("为保护硬件并恢复显存，强制机器冷却 10 分钟 (600秒)...")
                for _ in range(600):
                    if hasattr(self, "check_pause"):
                        self.check_pause()
                    if not self.is_running:
                        return False
                    time.sleep(1)
                self.log("10 分钟冷却完毕！准备继续尝试退回...")
                # 不再杀进程，直接继续尝试
                continue
            pos_obs = self.find_any_image_gray(dynamic_obstacles, region=self.regions["全界面"], threshold=0.75, fast_mode=True)
            if pos_obs:
                self.log(f"退回途中检测到已知图片/弹窗，点击推进... ({i+1}/80)")
                self.game_click(pos_obs)
                time.sleep(1.5)
                continue
            self.log(f"未在主菜单且无已知特定图片，按下 ESC... ({i + 1}/80)")
            self.hw_press("esc")
            time.sleep(1.2)
        self.log("80 次动态尝试均未进入菜单，高级退回失败。")
        return False

    # ==========================================
    # --- 优化后的图像查找核心 ---
    # ==========================================
    def capture_region(self, region=None):
        now = time.time()
        if region == self._last_screenshot_region and self._last_screenshot is not None and (now - self._last_screenshot_time) < 0.05:
            return self._last_screenshot
        try:
            if region:
                x, y, w, h = region
                bbox = (int(x), int(y), int(x + w), int(y + h))
                screen = ImageGrab.grab(bbox=bbox, all_screens=True)
            else:
                screen = ImageGrab.grab(all_screens=True)
        except Exception:
            screen = pyautogui.screenshot(region=region)
        img = cv2.cvtColor(np.array(screen), cv2.COLOR_RGB2BGR)
        self._last_screenshot = img
        self._last_screenshot_time = now
        self._last_screenshot_region = region
        return img

    def get_scales_to_try(self, fast_mode=True):
        full_region = self.regions.get("全界面")
        curr_w = full_region[2] if full_region else pyautogui.size()[0]
        base = curr_w / 2560.0
        scales = [base]
        if not fast_mode:
            scales += [base*0.98, base*1.02, base*0.95, base*1.05]
        scales = [round(s, 3) for s in scales if 0.6 <= s <= 1.4]
        if fast_mode and len(scales) > 4:
            scales = scales[:4]
        return scales

    def load_template(self, template_path):
        actual_path = get_img_path(template_path)
        if actual_path in self.template_cache:
            return self.template_cache[actual_path], actual_path
        tpl = cv2.imread(actual_path, cv2.IMREAD_COLOR)
        if tpl is not None:
            self.template_cache[actual_path] = tpl
        return tpl, actual_path

    def load_template_gray(self, template_path):
        actual_path = get_img_path(template_path)
        key = ("gray", actual_path)
        if key in self.template_gray_cache:
            return self.template_gray_cache[key]
        tpl = cv2.imread(actual_path, cv2.IMREAD_GRAYSCALE)
        if tpl is not None:
            self.template_gray_cache[key] = tpl
        return tpl

    def load_template_transparent(self, template_path):
        actual_path = get_img_path(template_path)
        key = ("transparent", actual_path)
        if key in self.template_transparent_cache:
            return self.template_transparent_cache[key]
        tpl = cv2.imread(actual_path, cv2.IMREAD_UNCHANGED)
        if tpl is not None:
            self.template_transparent_cache[key] = tpl
        return tpl

    def get_scaled_template(self, template_path, scale, mode='color'):
        if mode == 'color':
            tpl_orig, _ = self.load_template(template_path)
        elif mode == 'gray':
            tpl_orig = self.load_template_gray(template_path)
        else:
            tpl_orig = self.load_template_transparent(template_path)
        if tpl_orig is None:
            return None
        if scale == 1.0:
            return tpl_orig
        h, w = tpl_orig.shape[:2]
        new_w, new_h = int(w * scale), int(h * scale)
        if new_w < 5 or new_h < 5:
            return None
        return cv2.resize(tpl_orig, (new_w, new_h), interpolation=cv2.INTER_AREA)

    def _match_template(self, src, tpl, method=cv2.TM_CCOEFF_NORMED, mask=None):
        if mask is not None:
            res = cv2.matchTemplate(src, tpl, method, mask=mask)
        else:
            res = cv2.matchTemplate(src, tpl, method)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        return max_val, max_loc

    def find_image(self, template_path, region=None, threshold=0.75, fast_mode=True):
        if not self.is_running:
            return None
        screen = self.capture_region(region)
        for scale in self.get_scales_to_try(fast_mode):
            tpl = self.get_scaled_template(template_path, scale, 'color')
            if tpl is None:
                continue
            h, w = tpl.shape[:2]
            if h > screen.shape[0] or w > screen.shape[1]:
                continue
            max_val, max_loc = self._match_template(screen, tpl)
            if max_val >= threshold:
                cx = max_loc[0] + w//2 + (region[0] if region else 0)
                cy = max_loc[1] + h//2 + (region[1] if region else 0)
                self.log(f"[ImageMatch] 命中: {template_path} | 得分: {max_val:.3f} (阈值 {threshold}) | 缩放比: {scale:.3f}")
                return (cx, cy)
        return None

    def find_any_image(self, image_list, region=None, threshold=MATCH_THRESHOLD, fast_mode=True):
        if not self.is_running:
            return None
        screen = self.capture_region(region)
        for tpl_path in image_list:
            for scale in self.get_scales_to_try(fast_mode):
                tpl = self.get_scaled_template(tpl_path, scale, 'color')
                if tpl is None:
                    continue
                h, w = tpl.shape[:2]
                if h > screen.shape[0] or w > screen.shape[1]:
                    continue
                max_val, max_loc = self._match_template(screen, tpl)
                if max_val >= threshold:
                    cx = max_loc[0] + w//2 + (region[0] if region else 0)
                    cy = max_loc[1] + h//2 + (region[1] if region else 0)
                    self.log(f"[AnyMatch] 命中: {tpl_path} | 得分: {max_val:.3f} (阈值 {threshold}) | 缩放比: {scale:.3f}")
                    return (cx, cy)
        return None

    def find_image_gray(self, template_path, region=None, threshold=0.75, fast_mode=True):
        if not self.is_running:
            return None
        screen = self.capture_region(region)
        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        for scale in self.get_scales_to_try(fast_mode):
            tpl = self.get_scaled_template(template_path, scale, 'gray')
            if tpl is None:
                continue
            h, w = tpl.shape[:2]
            if h > screen_gray.shape[0] or w > screen_gray.shape[1]:
                continue
            max_val, max_loc = self._match_template(screen_gray, tpl)
            if max_val >= threshold:
                cx = max_loc[0] + w//2 + (region[0] if region else 0)
                cy = max_loc[1] + h//2 + (region[1] if region else 0)
                self.log(f"[GrayMatch] 命中: {template_path} | 灰度得分: {max_val:.3f} (阈值 {threshold}) | 缩放比: {scale:.3f}")
                return (cx, cy)
        return None

    def find_any_image_gray(self, image_list, region=None, threshold=0.75, fast_mode=True):
        if not self.is_running:
            return None
        screen = self.capture_region(region)
        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        for tpl_path in image_list:
            for scale in self.get_scales_to_try(fast_mode):
                tpl = self.get_scaled_template(tpl_path, scale, 'gray')
                if tpl is None:
                    continue
                h, w = tpl.shape[:2]
                if h > screen_gray.shape[0] or w > screen_gray.shape[1]:
                    continue
                max_val, max_loc = self._match_template(screen_gray, tpl)
                if max_val >= threshold:
                    cx = max_loc[0] + w//2 + (region[0] if region else 0)
                    cy = max_loc[1] + h//2 + (region[1] if region else 0)
                    self.log(f"[GrayMatchAny] 命中: {tpl_path} | 灰度得分: {max_val:.3f} (阈值 {threshold}) | 缩放比: {scale:.3f}")
                    return (cx, cy)
        return None

    def find_image_transparent(self, template_path, region=None, threshold=0.70, fast_mode=True):
        if not self.is_running:
            return None
        screen = self.capture_region(region)
        tpl_bgra = self.load_template_transparent(template_path)
        if tpl_bgra is None or tpl_bgra.shape[2] != 4:
            return self.find_image(template_path, region, threshold, fast_mode)
        for scale in self.get_scales_to_try(fast_mode):
            tpl_scaled = self.get_scaled_template(template_path, scale, 'transparent')
            if tpl_scaled is None:
                continue
            h, w = tpl_scaled.shape[:2]
            if h > screen.shape[0] or w > screen.shape[1]:
                continue
            tpl_bgr = tpl_scaled[:, :, :3]
            alpha_mask = tpl_scaled[:, :, 3]
            max_val, max_loc = self._match_template(screen, tpl_bgr, mask=alpha_mask)
            if max_val >= threshold:
                cx = max_loc[0] + w//2 + (region[0] if region else 0)
                cy = max_loc[1] + h//2 + (region[1] if region else 0)
                self.log(f"[AlphaMatch] 命中(无视背景): {template_path} | 得分: {max_val:.3f} (阈值 {threshold}) | 缩放比: {scale:.3f}")
                return (cx, cy)
        return None

    def find_any_image_transparent(self, image_list, region=None, threshold=0.70, fast_mode=True):
        for tpl_path in image_list:
            pos = self.find_image_transparent(tpl_path, region, threshold, fast_mode)
            if pos:
                return pos
        return None

    def find_image_with_element_multi(self, main_path, sub_path, region=None, fast_mode=True,
                                      main_threshold=0.60, like_threshold=0.75, final_threshold=0.72):
        if not self.is_running:
            return None
        screen = self.capture_region(region)
        screen_gray = self.to_gray_image(screen)
        screen_edge = self.to_edge_image(screen)
        for scale in self.get_scales_to_try(fast_mode):
            main_tpl = self.get_scaled_template(main_path, scale, 'color')
            sub_tpl = self.get_scaled_template(sub_path, scale, 'color')
            if main_tpl is None or sub_tpl is None:
                continue
            main_tpl_gray = self.to_gray_image(main_tpl)
            main_tpl_edge = self.to_edge_image(main_tpl)
            h_m, w_m = main_tpl.shape[:2]
            if h_m < 5 or w_m < 5 or h_m > screen.shape[0] or w_m > screen.shape[1]:
                continue
            res_main = cv2.matchTemplate(screen, main_tpl, cv2.TM_CCOEFF_NORMED)
            loc = np.where(res_main >= main_threshold)
            points = list(zip(*loc[::-1]))
            points.sort(key=lambda p: (p[0] // 50, p[1]))
            checked = set()
            for x, y in points:
                key = (x // 10, y // 10)
                if key in checked:
                    continue
                checked.add(key)
                roi_bgr = screen[y:y+h_m, x:x+w_m]
                roi_gray = screen_gray[y:y+h_m, x:x+w_m]
                roi_edge = screen_edge[y:y+h_m, x:x+w_m]
                if roi_bgr.shape[:2] != main_tpl.shape[:2]:
                    continue
                color_score = self.match_template_score(roi_bgr, main_tpl)
                gray_score = self.match_template_score(roi_gray, main_tpl_gray)
                edge_score = self.match_template_score(roi_edge, main_tpl_edge)
                roi_center = self.crop_center_ratio(roi_bgr, 0.6)
                tpl_center = self.crop_center_ratio(main_tpl, 0.6)
                center_score = self.match_template_score(roi_center, tpl_center)
                pad = 5
                sub_roi = screen[max(0, y-pad):min(screen.shape[0], y+h_m+pad),
                                  max(0, x-pad):min(screen.shape[1], x+w_m+pad)]
                like_score = self.match_template_score(sub_roi, sub_tpl)
                if like_score < like_threshold:
                    continue
                final_score = (color_score * 0.30 + gray_score * 0.20 + edge_score * 0.20 +
                               center_score * 0.15 + like_score * 0.15)
                if final_score >= final_threshold:
                    cx = x + w_m//2 + (region[0] if region else 0)
                    cy = y + h_m//2 + (region[1] if region else 0)
                    self.log(f"[MultiMatch] 命中: {main_path}+{sub_path} | 综合: {final_score:.3f} [彩:{color_score:.2f} 灰:{gray_score:.2f} 边:{edge_score:.2f} 中:{center_score:.2f} 标签:{like_score:.2f}] | 缩放比:{scale:.3f}")
                    return (cx, cy)
        return None

    def find_image_ultimate_safe(self, main_path, anti_path, region=None, main_threshold=0.80, anti_threshold=0.65):
        if not self.is_running:
            return None
        screen = self.capture_region(region)
        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        for scale in self.get_scales_to_try(fast_mode=True):
            main_tpl_bgr = self.get_scaled_template(main_path, scale, 'color')
            anti_tpl_bgr = self.get_scaled_template(anti_path, scale, 'color')
            if main_tpl_bgr is None or anti_tpl_bgr is None:
                continue
            main_tpl_gray = self.to_gray_image(main_tpl_bgr)
            h_m, w_m = main_tpl_bgr.shape[:2]
            h_a, w_a = anti_tpl_bgr.shape[:2]
            if h_m < 10 or w_m < 10 or h_m > screen.shape[0] or w_m > screen.shape[1]:
                continue
            res_main = cv2.matchTemplate(screen, main_tpl_bgr, cv2.TM_CCOEFF_NORMED)
            loc = np.where(res_main >= main_threshold)
            points = list(zip(*loc[::-1]))
            points.sort(key=lambda p: (p[0] // 50, p[1]))
            checked = set()
            for x, y in points:
                if (x//10, y//10) in checked:
                    continue
                checked.add((x//10, y//10))
                base_score = res_main[y, x]
                roi_bgr = screen[y:y+h_m, x:x+w_m]
                roi_gray = screen_gray[y:y+h_m, x:x+w_m]
                if roi_bgr.shape[:2] != main_tpl_bgr.shape[:2]:
                    continue
                pad_anti = 10
                roi_y1, roi_y2 = max(0, y-pad_anti), min(screen.shape[0], y+h_m+pad_anti)
                roi_x1, roi_x2 = max(0, x-pad_anti), min(screen.shape[1], x+w_m+pad_anti)
                anti_roi = screen[roi_y1:roi_y2, roi_x1:roi_x2]
                if anti_roi.shape[0] >= h_a and anti_roi.shape[1] >= w_a:
                    res_anti = cv2.matchTemplate(anti_roi, anti_tpl_bgr, cv2.TM_CCOEFF_NORMED)
                    _, anti_score, _, _ = cv2.minMaxLoc(res_anti)
                    if anti_score >= anti_threshold:
                        if self.debug_mode:
                            self.log(f"[Ultimate] rejected by anti image score={anti_score:.2f}")
                        continue
                top_h = int(h_m * 0.25)
                tpl_top = main_tpl_gray[:top_h, :]
                score_top = 0.0
                if top_h > 10 and w_m > 10:
                    tpl_top_core = tpl_top[5:-5, 5:-5]
                    search_top = roi_gray[:int(h_m*0.35), :]
                    if search_top.shape[0] >= tpl_top_core.shape[0] and search_top.shape[1] >= tpl_top_core.shape[1]:
                        res_top = cv2.matchTemplate(search_top, tpl_top_core, cv2.TM_CCOEFF_NORMED)
                        _, score_top, _, _ = cv2.minMaxLoc(res_top)
                bottom_h = int(h_m * 0.25)
                right_w = int(w_m * 0.35)
                tpl_pi_box = main_tpl_bgr[h_m-bottom_h:, w_m-right_w:]
                score_bot = 0.0
                if bottom_h > 10 and right_w > 10:
                    tpl_pi_core = tpl_pi_box[5:-5, 5:-5]
                    search_y1 = h_m - int(h_m*0.35)
                    search_x1 = w_m - int(w_m*0.45)
                    search_bot = roi_bgr[search_y1:, search_x1:]
                    if search_bot.shape[0] >= tpl_pi_core.shape[0] and search_bot.shape[1] >= tpl_pi_core.shape[1]:
                        res_bot = cv2.matchTemplate(search_bot, tpl_pi_core, cv2.TM_CCOEFF_NORMED)
                        _, score_bot, _, _ = cv2.minMaxLoc(res_bot)
                if base_score >= 0.76 and score_top >= 0.75 and score_bot >= 0.85:
                    cx = x + w_m//2 + (region[0] if region else 0)
                    cy = y + h_m//2 + (region[1] if region else 0)
                    self.log(f"[UltimateSafe] 锁定目标: {main_path} | 总分:{base_score:.3f} | 顶部车名:{score_top:.2f} | 右下调校:{score_bot:.2f}")
                    return (cx, cy)
        return None

    def wait_for_image(self, template_path, region=None, threshold=0.75, timeout=30, interval=0.4, fast_mode=True, log_text=None):
        start = time.time()
        while self.is_running and time.time() - start < timeout:
            pos = self.find_image(template_path, region, threshold, fast_mode)
            if pos:
                return pos
            if log_text:
                self.log(log_text)
            time.sleep(interval)
        return None

    def wait_for_any_image(self, image_list, region=None, threshold=0.75, timeout=30, interval=0.4, fast_mode=True, log_text=None):
        start = time.time()
        while self.is_running and time.time() - start < timeout:
            pos = self.find_any_image(image_list, region, threshold, fast_mode)
            if pos:
                return pos
            if log_text:
                self.log(log_text)
            time.sleep(interval)
        return None

    def wait_for_image_gray(self, template_path, region=None, threshold=0.75, timeout=30, interval=0.3, fast_mode=True):
        start = time.time()
        while self.is_running and time.time() - start < timeout:
            pos = self.find_image_gray(template_path, region, threshold, fast_mode)
            if pos:
                return pos
            time.sleep(interval)
        return None

    def wait_for_any_image_gray(self, image_list, region=None, threshold=0.75, timeout=30, interval=0.3, fast_mode=True):
        start = time.time()
        while self.is_running and time.time() - start < timeout:
            pos = self.find_any_image_gray(image_list, region, threshold, fast_mode)
            if pos:
                return pos
            time.sleep(interval)
        return None

    def wait_for_image_transparent(self, template_path, region=None, threshold=0.70, timeout=30, interval=0.4, fast_mode=True):
        start = time.time()
        while self.is_running and time.time() - start < timeout:
            pos = self.find_image_transparent(template_path, region, threshold, fast_mode)
            if pos:
                return pos
            time.sleep(interval)
        return None

    def wait_for_any_image_transparent(self, image_list, region=None, threshold=0.70, timeout=30, interval=0.4, fast_mode=True):
        start = time.time()
        while self.is_running and time.time() - start < timeout:
            pos = self.find_any_image_transparent(image_list, region, threshold, fast_mode)
            if pos:
                return pos
            time.sleep(interval)
        return None

    def wait_for_image_with_element_multi(self, main_path, sub_path, region=None, fast_mode=True,
                                          main_threshold=0.60, like_threshold=0.75, final_threshold=0.72,
                                          timeout=30, interval=0.4):
        start = time.time()
        while self.is_running and time.time() - start < timeout:
            pos = self.find_image_with_element_multi(main_path, sub_path, region, fast_mode,
                                                     main_threshold, like_threshold, final_threshold)
            if pos:
                return pos
            time.sleep(interval)
        return None

    def wait_for_image_ultimate_safe(self, main_path, anti_path, region=None, main_threshold=0.80,
                                     anti_threshold=0.65, timeout=3, interval=0.2):
        start = time.time()
        while self.is_running and time.time() - start < timeout:
            pos = self.find_image_ultimate_safe(main_path, anti_path, region, main_threshold, anti_threshold)
            if pos:
                return pos
            time.sleep(interval)
        return None

    def to_gray_image(self, img):
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    def to_edge_image(self, img):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        edge = cv2.Canny(blur, 50, 150)
        return edge

    def crop_center_ratio(self, img, ratio=0.6):
        h, w = img.shape[:2]
        ch = int(h * ratio)
        cw = int(w * ratio)
        y1 = max(0, (h - ch) // 2)
        x1 = max(0, (w - cw) // 2)
        return img[y1:y1+ch, x1:x1+cw]

    def match_template_score(self, src, tpl):
        try:
            if tpl is None or src is None:
                return 0.0
            th, tw = tpl.shape[:2]
            sh, sw = src.shape[:2]
            if th < 5 or tw < 5 or th > sh or tw > sw:
                return 0.0
            res = cv2.matchTemplate(src, tpl, cv2.TM_CCOEFF_NORMED)
            return cv2.minMaxLoc(res)[1]
        except Exception:
            return 0.0

    # ==========================================
    # --- 模板缓存 ---
    # ==========================================
    def get_images_root_dir(self):
        ext_dir = os.path.join(APP_DIR, "images")
        if os.path.isdir(ext_dir):
            return ext_dir
        int_dir = os.path.join(INTERNAL_DIR, "images")
        if os.path.isdir(int_dir):
            return int_dir
        return None

    def get_template_meta(self):
        images_dir = self.get_images_root_dir()
        meta = {}
        if not images_dir:
            return meta
        for root, _, files in os.walk(images_dir):
            for file in files:
                if not file.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                    continue
                path = os.path.join(root, file)
                rel = os.path.relpath(path, images_dir).replace("\\", "/")
                try:
                    stat = os.stat(path)
                    meta[rel] = {"mtime": stat.st_mtime, "size": stat.st_size}
                except Exception:
                    pass
        return meta

    def is_template_cache_valid(self):
        if not os.path.exists(TEMPLATE_CACHE_FILE) or not os.path.exists(TEMPLATE_META_FILE):
            return False
        try:
            with open(TEMPLATE_META_FILE, "r", encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            return False
        new = self.get_template_meta()
        return old == new

    def build_template_file_cache(self):
        self.log("开始构建模板缓存文件...")
        os.makedirs(CACHE_DIR, exist_ok=True)
        images_dir = self.get_images_root_dir()
        if not images_dir:
            self.log("未找到 images 目录，无法构建模板缓存。")
            return False
        cache_data = {}
        meta = self.get_template_meta()
        scales = self.get_scales_to_try(fast_mode=False)
        for rel_path in meta.keys():
            img_path = os.path.join(images_dir, rel_path)
            tpl = cv2.imread(img_path, cv2.IMREAD_COLOR)
            if tpl is None:
                continue
            cache_data[rel_path] = {}
            for scale in scales:
                try:
                    if scale == 1.0:
                        scaled = tpl.copy()
                    else:
                        scaled = cv2.resize(tpl, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                    cache_data[rel_path][str(round(scale, 3))] = scaled
                except Exception:
                    continue
        try:
            with open(TEMPLATE_CACHE_FILE, "wb") as f:
                pickle.dump(cache_data, f, protocol=pickle.HIGHEST_PROTOCOL)
            with open(TEMPLATE_META_FILE, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            self.log("模板缓存文件构建完成。")
            return True
        except Exception as e:
            self.log(f"写入模板缓存失败: {e}")
            return False

    def load_template_file_cache(self):
        try:
            with open(TEMPLATE_CACHE_FILE, "rb") as f:
                self.file_template_cache = pickle.load(f)
            self.log("模板缓存文件加载成功。")
            return True
        except Exception as e:
            self.log(f"加载模板缓存失败: {e}")
            self.file_template_cache = {}
            return False

    def prepare_template_cache(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        if self.is_template_cache_valid():
            if self.load_template_file_cache():
                return
        self.log("模板缓存不存在或已失效，开始后台重建（这可能需要几秒钟）...")
        if self.build_template_file_cache():
            self.template_cache.clear()
            self.scaled_template_cache.clear()
            self.load_template_file_cache()

    def get_scaled_template(self, template_path, scale, mode='color'):
        actual_path = get_img_path(template_path)
        images_dir = self.get_images_root_dir()
        if images_dir and os.path.exists(actual_path):
            try:
                rel_key = os.path.relpath(actual_path, images_dir).replace("\\", "/")
            except Exception:
                rel_key = os.path.basename(actual_path)
        else:
            rel_key = os.path.basename(actual_path)
        mem_key = (actual_path, round(scale, 3), mode)
        if mem_key in self.scaled_template_cache:
            return self.scaled_template_cache[mem_key]
        scale_key = str(round(scale, 3))
        if mode == 'color' and rel_key in self.file_template_cache:
            tpl = self.file_template_cache[rel_key].get(scale_key)
            if tpl is not None:
                self.scaled_template_cache[mem_key] = tpl
                return tpl
        if mode == 'color':
            tpl_orig, _ = self.load_template(template_path)
        elif mode == 'gray':
            tpl_orig = self.load_template_gray(template_path)
        else:
            tpl_orig = self.load_template_transparent(template_path)
        if tpl_orig is None:
            return None
        if scale == 1.0:
            tpl = tpl_orig
        else:
            tpl = cv2.resize(tpl_orig, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        self.scaled_template_cache[mem_key] = tpl
        return tpl

    # ==========================================
    # --- 模块：跑图 ---
    # ==========================================
    def logic_race(self, target_count):
        if self.race_counter >= target_count:
            return True
        self.update_running_ui("循环跑图", self.race_counter, target_count)
        self.log("准备验证/进入菜单...")
        if not self.enter_menu():
            return False
        self.log("切换到创意中心...")
        for _ in range(4):
            self.hw_press("pagedown", delay=0.15)
            time.sleep(0.3)
        time.sleep(0.8)
        pos_el = self.wait_for_image_gray("eventlab.png", region=self.regions["全界面"], threshold=0.7, timeout=5, interval=0.25, fast_mode=True)
        if not pos_el:
            self.log("未找到 eventlab")
            return False
        self.game_click(pos_el)
        time.sleep(1.2)
        pos_yg = self.wait_for_image_gray("playenent.png", region=self.regions["中间"], threshold=0.75, timeout=40, interval=0.3, fast_mode=True)
        if not pos_yg:
            self.log("未找到游玩赛事")
            return False
        self.game_click(pos_yg)
        time.sleep(1.5)
        self.hw_press("backspace")
        time.sleep(0.8)
        self.hw_press("up")
        time.sleep(0.4)
        self.hw_press("enter")
        time.sleep(0.8)
        code_text = "".join(c for c in self.entry_share.get() if c.isdigit())
        for char in code_text:
            if not self.is_running:
                return False
            if char in DIK_CODES:
                self.hw_press(char, delay=0.05)
                time.sleep(0.05)
        time.sleep(0.4)
        self.hw_press("enter")
        time.sleep(0.8)
        self.hw_press("down")
        time.sleep(0.3)
        self.hw_press("enter")
        time.sleep(1.5)
        pos_ck = self.wait_for_image_gray("VEI.png", region=self.regions["下"], threshold=0.75, timeout=20, interval=1.0, fast_mode=True)
        if not pos_ck:
            self.log("链接超时")
            return False
        self.hw_press("enter")
        time.sleep(2.0)
        self.hw_press("enter")
        time.sleep(2.0)
        pos_target = self.wait_for_image_with_element_multi("skillcar.png", "liketag.png", region=self.regions["全界面"],
                                                            fast_mode=True, main_threshold=0.75, like_threshold=0.7,
                                                            final_threshold=0.7, timeout=2, interval=0.25)
        if not pos_target:
            self.log("未找到带 liketag 的目标车辆，重新选品牌...")
            self.hw_press("backspace")
            time.sleep(1.2)
            found_brand = False
            for _ in range(3):
                if not self.is_running:
                    return False
                pos_brand = self.wait_for_image_gray("skillcarbrand.png", region=self.regions["全界面"], threshold=0.8, timeout=1.2, interval=0.2, fast_mode=True)
                if pos_brand:
                    self.game_click(pos_brand)
                    time.sleep(1.2)
                    found_brand = True
                    break
                self.hw_press("up")
                time.sleep(0.4)
            if not found_brand:
                self.log("三次尝试未找到刷图车辆品牌。")
                return False
            for _ in range(20):
                if not self.is_running:
                    return False
                pos_target = self.wait_for_image_with_element_multi("skillcar.png", "liketag.png", region=self.regions["全界面"],
                                                                    main_threshold=0.75, like_threshold=0.7, final_threshold=0.7,
                                                                    timeout=2, interval=0.25, fast_mode=True)
                if pos_target:
                    break
                for _ in range(4):
                    self.hw_press("right", delay=0.08)
                    time.sleep(0.08)
                time.sleep(0.4)
        if not pos_target:
            self.log("翻页未能找到带有 liketag 的刷图车辆！")
            return False
        self.game_click(pos_target)
        time.sleep(0.5)
        self.hw_press("enter")
        time.sleep(4.0)
        self.log("前置完成，开始循环跑图！")
        while self.race_counter < target_count:
            if not self.is_running:
                return False
            self.log(f"跑图 {self.race_counter + 1}/{target_count}: 找赛事起点...")
            pos = None
            for _ in range(120):
                if not self.is_running:
                    return False
                pos = self.wait_for_any_image_gray(["start.png", "startw.png"], region=self.regions["左下"],
                                                   threshold=0.75, timeout=0.7, interval=0.2, fast_mode=True)
                if pos:
                    break
                self.hw_press("down")
                time.sleep(0.25)
            if not pos:
                self.log("找不到赛事起点，退出跑图。")
                return False
            self.game_click(pos)
            time.sleep(4.0)
            self.hw_key_down("w")
            self.hw_key_down("up")
            race_start_time = time.time()
            last_like_chk = time.time()
            last_chk = 0
            finished = False
            timeout_triggered = False
            driving_keys_held = True
            while self.is_running:
                # 暂停检查
                if self.is_paused:
                    if driving_keys_held:
                        self.hw_key_up("w")
                        self.hw_key_up("up")
                        driving_keys_held = False
                    self.check_pause()
                    if self.is_running:
                        self.hw_key_down("w")
                        self.hw_key_down("up")
                        driving_keys_held = True
                    race_start_time = time.time()
                    last_like_chk = time.time()
                    last_chk = time.time()
                    continue
                now = time.time()
                if now - race_start_time > 120.0:
                    self.log("跑图超时(已超过120秒)！触发强制重开赛事逻辑...")
                    timeout_triggered = True
                    break
                if now - last_like_chk >= 3.0:
                    pos_like = self.find_any_image_gray(["likeauthor.png", "dislikeauthor.png"], region=self.regions["中间"], threshold=0.70)
                    if pos_like:
                        self.log("识别到点赞作界面，执行回车确认！")
                        self.hw_press("enter")
                    last_like_chk = now
                if now - last_chk >= 1.0:
                    found_restart = self.find_image_gray("restart.png", region=self.regions["下"], threshold=0.75, fast_mode=True)
                    if found_restart:
                        finished = True
                        break
                    last_chk = now
                time.sleep(0.3)
            self.hw_key_up("w")
            self.hw_key_up("up")
            if not self.is_running:
                return False
            if timeout_triggered:
                time.sleep(0.5)
                self.hw_press("esc")
                time.sleep(1.5)
                pos_restarta = self.wait_for_image_gray("restarta.png", region=self.regions["全界面"], threshold=0.70, timeout=4.0, interval=0.3, fast_mode=True)
                if pos_restarta:
                    self.log("找到 restarta.png，点击重开赛事...")
                    self.game_click(pos_restarta)
                    time.sleep(1.0)
                    self.hw_press("enter")
                    time.sleep(4.0)
                else:
                    self.log("未找到 restarta.png，尝试直接继续...")
                continue
            if not finished:
                return False
            if self.race_counter == target_count - 1:
                self.hw_press("enter")
                time.sleep(2.0)
            else:
                self.hw_press("x")
                time.sleep(0.8)
                self.hw_press("enter")
                time.sleep(2.0)
            self.race_counter += 1
            self.update_running_ui("循环跑图", self.race_counter, target_count)
        return True

    # ==========================================
    # --- 模块：买车 ---
    # ==========================================
    def logic_buy_car(self, target_count):
        if self.car_counter >= target_count:
            return True
        self.update_running_ui("批量买车", self.car_counter, target_count)
        self.log("准备验证/进入菜单...")
        if not self.enter_menu():
            return False
        pos_collectionjournal = self.wait_for_image_transparent("collectionjournal.png", region=self.regions["左"],
                                                                threshold=0.7, timeout=30, interval=0.4, fast_mode=True)
        if not pos_collectionjournal:
            self.log("未找到收集簿")
            return False
        self.game_click(pos_collectionjournal, double=True)
        time.sleep(1.0)
        pos_masterexplorer = self.wait_for_image("masterexplorer.png", region=self.regions["全界面"],
                                                 threshold=0.75, timeout=30, interval=0.4, fast_mode=True)
        if not pos_masterexplorer:
            self.log("未找到探索")
            return False
        self.game_click(pos_masterexplorer, double=True)
        time.sleep(0.6)
        pos_carcollection = self.wait_for_image_transparent("carcollection.png", region=self.regions["全界面"],
                                                            threshold=0.75, timeout=30, interval=0.3, fast_mode=True)
        if not pos_carcollection:
            self.log("未找到车辆收集")
            return False
        self.game_click(pos_carcollection, double=True)
        time.sleep(1.0)
        self.hw_press("backspace")
        time.sleep(0.5)
        brand_pos = None
        for _ in range(5):
            if not self.is_running:
                return False
            brand_pos = self.wait_for_any_image_gray(["CCbrand.png", "CCbrand-b.png"], region=self.regions["全界面"],
                                                     threshold=0.75, timeout=0.8, interval=0.2, fast_mode=True)
            if brand_pos:
                break
            self.hw_press("up")
            time.sleep(0.25)
        if not brand_pos:
            self.log("未找到品牌")
            return False
        self.game_click(brand_pos)
        time.sleep(0.8)
        self.hw_press("down")
        time.sleep(0.4)
        pos_22b = self.wait_for_image("consumablecar.png", region=self.regions["全界面"], threshold=0.90,
                                      timeout=8, interval=0.3, fast_mode=False)
        if not pos_22b:
            self.log("未找到消耗品车辆")
            return False
        self.game_click(pos_22b, double=True)
        time.sleep(1.0)
        while self.car_counter < target_count:
            if not self.is_running:
                return False
            self.hw_press("space")
            time.sleep(0.6)
            self.move_to_game_coord(5, 5)
            self.hw_press("down")
            time.sleep(0.2)
            self.move_to_game_coord(5, 5)
            self.hw_press("enter")
            time.sleep(0.6)
            self.move_to_game_coord(5, 5)
            self.hw_press("enter")
            time.sleep(0.6)
            self.move_to_game_coord(5, 5)
            self.hw_press("enter")
            time.sleep(0.7)
            self.car_counter += 1
            self.update_running_ui("批量买车", self.car_counter, target_count)
        for _ in range(5):
            if not self.is_running:
                return False
            self.hw_press("esc")
            time.sleep(0.8)
        return True

    # ==========================================
    # --- 模块：抽奖 ---
    # ==========================================
    def logic_super_wheelspin(self, target_count):
        if self.cj_counter >= target_count:
            return True
        self.update_running_ui("超级抽奖", self.cj_counter, target_count)
        if not hasattr(self, 'memory_car_page'):
            self.memory_car_page = 0
        self.log("准备验证/进入菜单...")
        if not self.enter_menu():
            return False
        self.log("进入车辆与收藏...")
        self.hw_press("pagedown", delay=0.15)
        time.sleep(1.0)
        pos_buycar = self.wait_for_image("BNandUC.png", region=self.regions["左"], threshold=0.70,
                                         timeout=15, interval=0.3, fast_mode=True)
        if not pos_buycar:
            self.log("未识别到 购买新车与二手车")
            return False
        self.game_click(pos_buycar)
        time.sleep(0.8)
        self.hw_press("enter")
        time.sleep(5)
        pos_bs = self.wait_for_any_image_gray(["buyandsell-w.png", "buyandsell-b.png"], region=self.regions["左"],
                                              threshold=0.75, timeout=60, interval=0.5, fast_mode=True)
        if not pos_bs:
            self.log("未找到购买与出售")
            return False
        self.game_click(pos_bs)
        time.sleep(1.0)
        self.hw_press("pagedown", delay=0.15)
        self.log("进入车辆界面...")
        time.sleep(0.5)
        while self.cj_counter < target_count:
            if not self.is_running:
                return False
            self.log("进入我的车辆.")
            self.hw_press("enter")
            time.sleep(2.0)
            self.hw_press("backspace")
            time.sleep(1.0)
            brand_pos = None
            for _ in range(30):
                if not self.is_running:
                    return False
                brand_pos = self.wait_for_any_image_gray(["CCbrand.png", "CCbrand-b.png"], region=self.regions["全界面"],
                                                         threshold=0.75, timeout=0.8, interval=0.2, fast_mode=True)
                if brand_pos:
                    break
                self.hw_press("up")
                time.sleep(0.25)
            if not brand_pos:
                self.log("选品牌失败")
                return False
            self.game_click(brand_pos)
            time.sleep(1.0)
            jump_pages = max(0, self.memory_car_page - 1)
            if jump_pages > 0:
                self.log(f"智能记忆触发：快速跳过前 {jump_pages} 页...")
                for _ in range(jump_pages):
                    if not self.is_running:
                        return False
                    for _ in range(4):
                        self.hw_press("right", delay=0.06)
                        time.sleep(0.1)
                    time.sleep(0.15)
            pos_target = None
            found_car = False
            current_page = jump_pages
            for _ in range(85 - jump_pages):
                if not self.is_running:
                    return False
                pos_target = self.wait_for_image_with_element_multi("newCC.png", "newcartag.png", region=self.regions["全界面"],
                                                                    main_threshold=0.75, like_threshold=0.75, final_threshold=0.70,
                                                                    timeout=1.5, interval=0.2, fast_mode=True)
                if pos_target:
                    self.game_click(pos_target)
                    found_car = True
                    self.memory_car_page = current_page
                    self.log(f"锁定目标车辆！已记录当前页码: {current_page}")
                    break
                for _ in range(4):
                    self.hw_press("right", delay=0.06)
                    time.sleep(0.1)
                time.sleep(0.4)
                current_page += 1
            if not found_car:
                self.log("列表中未找到目标车辆，重置记忆页码。")
                self.memory_car_page = 0
                return False
            time.sleep(1.2)
            self.log("尝试寻找'上车'按钮...")
            pos_rc = self.wait_for_image_gray("rc.png", region=self.regions["全界面"], threshold=0.70,
                                              timeout=0.5, interval=0.1, fast_mode=True)
            if pos_rc:
                self.log("点击上车")
                self.game_click(pos_rc)
                time.sleep(2.0)
            else:
                self.log("回车上车")
                self.hw_press("enter")
                time.sleep(1.0)
                self.hw_press("enter")
                time.sleep(1.0)
            pos_sjy = None
            for _ in range(20):
                if not self.is_running:
                    return False
                pos_sjy = self.find_any_image_gray(["UandT-w.png", "UandT-b.png"], region=self.regions["左下"], threshold=0.70)
                if pos_sjy:
                    break
                self.hw_press("esc")
                time.sleep(0.5)
            if not pos_sjy:
                self.log("找不到升级页面")
                return False
            self.game_click(pos_sjy)
            time.sleep(0.5)
            pos_cls = self.wait_for_any_image_gray(["clsldcnw.png", "clsldcnb.png"], region=self.regions["左下"],
                                                   threshold=0.70, timeout=20)
            if not pos_cls:
                self.log("未找到车辆熟练度")
                return False
            self.game_click(pos_cls)
            time.sleep(1.5)
            pos_exp = self.wait_for_any_image(["EXPwU.png"], region=self.regions["左"], threshold=0.75,
                                              timeout=1.5, interval=0.3, fast_mode=True)
            if pos_exp:
                self.log("该车辆技能已点过，跳过计数")
            else:
                time.sleep(1.0)
                self.hw_press("enter")
                time.sleep(1.5)
                for dk in self.config["skill_dirs"]:
                    if not self.is_running:
                        return False
                    self.hw_press(dk)
                    time.sleep(0.2)
                    self.hw_press("enter")
                    time.sleep(1.2)
                spne_found = self.find_image_gray("SPNE.png", region=self.regions["全界面"], threshold=0.70)
                if spne_found:
                    self.log("已无技能点或技能已点完，提前结束抽奖！")
                    time.sleep(1.0)
                    self.hw_press("enter")
                    time.sleep(0.8)
                    self.hw_press("esc")
                    time.sleep(1.0)
                    self.hw_press("esc")
                    time.sleep(1.0)
                    self.hw_press("esc")
                    time.sleep(1.0)
                    return True
                self.cj_counter += 1
                self.update_running_ui("超级抽奖", self.cj_counter, target_count)
            self.hw_press("esc")
            time.sleep(1.2)
            self.hw_press("esc")
            time.sleep(0.8)
            self.hw_press("up", delay=0.15)
            time.sleep(0.8)
        self.hw_press("esc")
        time.sleep(1.2)
        self.hw_press("esc")
        time.sleep(1.2)
        return True

    # ==========================================
    # --- 模块：移除车辆 (模式2) ---
    # ==========================================
    def sell_consumable_car(self, target_count):
        if self.sc_count >= target_count:
            return True
        self.update_running_ui("移除车辆", self.sc_count, target_count)
        self.log("准备验证/进入菜单！！！使用前请人工核验到正常移除车辆再进行自动化移除处理")
        if not self.enter_menu():
            return False
        self.log("进入车辆与收藏！！！使用前请人工核验到正常移除车辆再进行自动化移除处理")
        self.hw_press("pagedown", delay=0.15)
        time.sleep(1.0)
        pos_buycar = self.wait_for_image("BNandUC.png", region=self.regions["左"], threshold=0.70, timeout=12, interval=0.3, fast_mode=True)
        if not pos_buycar:
            self.log("未识别到 购买新车与二手车")
            return False
        self.game_click(pos_buycar)
        time.sleep(0.8)
        self.hw_press("enter")
        time.sleep(5)
        pos_bs = self.wait_for_any_image(["buyandsell-w.png", "buyandsell-b.png"], region=self.regions["上"],
                                         threshold=0.75, timeout=40, interval=0.5, fast_mode=True)
        if not pos_bs:
            self.log("未找到购买与出售")
            return False
        self.game_click(pos_bs)
        time.sleep(1.0)
        self.hw_press("pagedown", delay=0.15)
        time.sleep(1.0)
        self.hw_press("enter")
        time.sleep(2.0)
        self.hw_press("y")
        time.sleep(1.0)
        self.hw_press("enter")
        time.sleep(0.8)
        self.hw_press("esc")
        time.sleep(1.5)
        self.hw_press("enter")
        time.sleep(0.8)
        self.move_to_game_coord(5, 5)
        time.sleep(0.2)
        pos = self.wait_for_image("rc.png", region=self.regions["全界面"], threshold=0.65, timeout=5, interval=0.2, fast_mode=True)
        if pos:
            self.log("找到上车，执行点击")
            self.game_click(pos)
            time.sleep(2.0)
        else:
            self.log("该车辆已经驾驶，或未找到图片，执行两次ESC")
            self.hw_press("esc")
            time.sleep(1.5)
            self.hw_press("esc")
        time.sleep(2.0)
        found = False
        for i in range(60):
            if not self.is_running:
                return False
            pos = self.wait_for_any_image(["buyandsell-b.png", "buyandsell-w.png"], region=self.regions["上"],
                                          threshold=0.70, timeout=0.8, interval=0.2, fast_mode=True)
            if pos:
                self.log(f"第 {i + 1} 次检测到购买与出售，进入车辆界面")
                self.hw_press("enter")
                found = True
                break
            self.log(f"第 {i + 1} 次未检测到购买与出售，等待后重试")
            time.sleep(1.0)
        if not found:
            self.log("60次内未找到购买与出售")
            return False
        time.sleep(1.5)
        self.hw_press("x")
        time.sleep(0.5)
        self.move_to_game_coord(5, 5)
        self.log("切换到 最近获得 的排序...")
        for _ in range(6):
            if not self.is_running:
                return False
            self.hw_press("down")
            time.sleep(0.25)
        time.sleep(0.2)
        self.hw_press("enter")
        time.sleep(1.2)
        self.log("回到最近获得的前面")
        self.hw_press("backspace")
        time.sleep(0.8)
        self.hw_press("enter")
        time.sleep(1.5)
        self.log("开始删除最近获得的车辆！！！请人工确认是否移除")
        while self.sc_count < target_count:
            if not self.is_running:
                return False
            self.hw_press("enter")
            time.sleep(1.2)
            for _ in range(6):
                if not self.is_running:
                    return False
                self.hw_press("down")
                time.sleep(0.2)
            self.hw_press("enter")
            time.sleep(0.5)
            self.hw_press("down")
            time.sleep(0.3)
            self.hw_press("enter")
            time.sleep(0.8)
            self.sc_count += 1
            self.log(f"已尝试删除车辆 {self.sc_count}/{target_count}")
        for _ in range(3):
            if not self.is_running:
                return False
            self.hw_press("esc")
            time.sleep(1.0)
        return True

    # ==========================================
    # --- 模块：移除车辆 (模式1) 带智能识别 ---
    # ==========================================
    def find_and_remove_consumable_car(self, target_count):
        if self.sc_count >= target_count:
            return True
        self.update_running_ui("移除车辆", self.sc_count, target_count)
        self.log("准备验证/进入菜单！！！使用前请人工核验到正常移除车辆再进行自动化移除处理")
        if not self.enter_menu():
            return False
        self.log("进入车辆与收藏！！！使用前请人工核验到正常移除车辆再进行自动化移除处理")
        self.hw_press("pagedown", delay=0.15)
        time.sleep(1.0)
        pos_buycar = self.wait_for_image("BNandUC.png", region=self.regions["左"], threshold=0.70, timeout=12, interval=0.3, fast_mode=True)
        if not pos_buycar:
            self.log("未识别到 购买新车与二手车")
            return False
        self.game_click(pos_buycar)
        time.sleep(0.8)
        self.hw_press("enter")
        time.sleep(5)
        pos_bs = self.wait_for_any_image(["buyandsell-w.png", "buyandsell-b.png"], region=self.regions["上"],
                                         threshold=0.75, timeout=40, interval=0.5, fast_mode=True)
        if not pos_bs:
            self.log("未找到购买与出售")
            return False
        self.game_click(pos_bs)
        time.sleep(1.0)
        self.hw_press("pagedown", delay=0.15)
        time.sleep(1.0)
        self.hw_press("enter")
        time.sleep(2.0)
        self.hw_press("y")
        time.sleep(1.0)
        self.hw_press("enter")
        time.sleep(0.8)
        self.hw_press("esc")
        time.sleep(1.5)
        self.hw_press("enter")
        time.sleep(0.8)
        self.move_to_game_coord(5, 5)
        time.sleep(0.2)
        pos = self.wait_for_image("rc.png", region=self.regions["全界面"], threshold=0.65, timeout=5, interval=0.2, fast_mode=True)
        if pos:
            self.log("找到上车，执行点击")
            self.game_click(pos)
            time.sleep(2.0)
        else:
            self.log("该车辆已经驾驶，或未找到图片，执行两次ESC")
            self.hw_press("esc")
            time.sleep(1.5)
            self.hw_press("esc")
        time.sleep(2.0)
        found = False
        for i in range(30):
            if not self.is_running:
                return False
            pos = self.wait_for_any_image(["buyandsell-b.png", "buyandsell-w.png"], region=self.regions["上"],
                                          threshold=0.70, timeout=0.8, interval=0.2, fast_mode=True)
            if pos:
                self.log(f"第 {i + 1} 次检测到购买与出售，进入车辆界面")
                self.hw_press("enter")
                time.sleep(1.5)
                found = True
                break
            self.log(f"第 {i + 1} 次未检测到购买与出售，等待后重试")
            time.sleep(1.0)
        if not found:
            self.log("30次内未找到购买与出售")
            return False
        self.hw_press("y")
        time.sleep(1.0)
        for _ in range(2):
            self.hw_press("down", delay=0.06)
            time.sleep(0.2)
        time.sleep(0.5)
        self.hw_press("enter")
        time.sleep(1.0)
        self.hw_press("esc")
        time.sleep(1.0)
        self.log("切换到消耗品品牌...")
        self.hw_press("backspace")
        brand_pos = None
        for _ in range(5):
            if not self.is_running:
                return False
            brand_pos = self.wait_for_any_image_gray(["CCbrand.png", "CCbrand-b.png"], region=self.regions["全界面"],
                                                     threshold=0.75, timeout=0.8, interval=0.2, fast_mode=True)
            if brand_pos:
                break
            self.hw_press("up")
            time.sleep(0.25)
        if not brand_pos:
            self.log("未找到品牌")
            return False
        self.game_click(brand_pos)
        time.sleep(0.8)
        self.log("开始删除最近获得的车辆！！！请人工确认是否移除")
        not_found_pages = 0
        while self.sc_count < target_count:
            if not self.is_running:
                return False
            self.log(f"正在使用 3模式 严格扫描当前页面... (连续未找到: {not_found_pages}/5)")
            pos_target = self.wait_for_image_ultimate_safe("removecarobject.png", "newcartag.png",
                                                           region=self.regions["全界面"],
                                                           main_threshold=0.77, anti_threshold=0.65,
                                                           timeout=3.0, interval=0.2)
            if not pos_target:
                not_found_pages += 1
                if not_found_pages >= 2:
                    self.log("=连续翻找 2 页仍未搜索到目标车辆！视为车辆已全部清理完毕。")
                    self.log("主动结束清理任务，准备进入下一步骤...")
                    break
                self.log(f"当前页面未找到，向右翻页寻找... (第 {not_found_pages} 次翻页)")
                for _ in range(4):
                    self.hw_press("right", delay=0.06)
                    time.sleep(0.1)
                time.sleep(0.4)
                continue
            not_found_pages = 0
            self.log("精准锁定目标车辆，执行点击...")
            self.game_click(pos_target)
            time.sleep(1.2)
            self.log("寻找 '从车库移除' 按钮...")
            pos_remove = self.find_image_gray("removecar.png", region=self.regions["全界面"], threshold=0.75, fast_mode=True)
            if pos_remove:
                self.log("直接找到移除按钮，点击...")
                self.game_click(pos_remove)
            else:
                self.log("未直接找到移除按钮，按下 Enter 呼出菜单...")
                self.hw_press("enter")
                time.sleep(0.8)
                pos_remove = self.find_image_gray("removecar.png", region=self.regions["全界面"], threshold=0.75, fast_mode=True)
                if pos_remove:
                    self.log("呼出菜单后找到移除按钮，点击...")
                    self.game_click(pos_remove)
                else:
                    self.log("仍未找到移除按钮，可能点错了/该车无法移除，按 ESC 放弃该车...")
                    self.hw_press("esc")
                    time.sleep(1.0)
                    self.hw_press("right")
                    time.sleep(1.2)
                    continue
            time.sleep(0.8)
            self.log("确认移除...")
            self.hw_press("down")
            time.sleep(0.3)
            self.hw_press("enter")
            time.sleep(1.2)
            self.sc_count += 1
            self.update_running_ui("移除车辆", self.sc_count, target_count)
            self.log(f"成功移除车辆！当前进度: {self.sc_count}/{target_count}")
        for _ in range(3):
            if not self.is_running:
                return False
            self.hw_press("esc")
            time.sleep(1.0)
        return True

if __name__ == "__main__":
    app = FH_UltimateBot()
    app.mainloop()