# -*- coding: utf-8 -*-
import json
import os
import re
import threading
import sys
import subprocess
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from datetime import datetime
from urllib.parse import urlparse

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None
    Image = ImageDraw = None

APP_NAME = "短链接生成器"
APP_VERSION = "1.0.0"
DATA_DIR = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), "ShortLinkGenerator")
CONFIG_FILE = os.path.join(DATA_DIR, "profiles.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def save_json(path, data):
    ensure_data_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_json_path(obj, path):
    """支持 data.shortUrl、data.result[0].url 这类简单 JSON 路径。"""
    cur = obj
    if not path.strip():
        return cur
    parts = path.strip().replace("[", ".").replace("]", "").split(".")
    for part in parts:
        if not part:
            continue
        if isinstance(cur, dict):
            cur = cur[part]
        elif isinstance(cur, list):
            cur = cur[int(part)]
        else:
            raise KeyError(f"无法继续读取字段：{part}")
    return cur


DEFAULT_PROFILE = {
    "name": "我的短链 API",
    "url": "https://api.example.com",
    "method": "POST",
    "content_type": "application/json",
    "headers": [
        {"key": "User-Agent", "value": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"},
        {"key": "Authorization", "value": "Bearer YOUR_API_KEY"}
    ],
    "body": '{\n  "url": "{{longUrl}}",\n  "ttl": "60"\n}',
    "response_path": "short_url",
    "timeout": 30
}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1180x790")
        self.minsize(980, 680)
        self.configure(bg="#f3f6fb")
        ensure_data_dir()

        self.profiles = load_json(CONFIG_FILE, [DEFAULT_PROFILE.copy()])
        if not isinstance(self.profiles, list) or not self.profiles:
            self.profiles = [DEFAULT_PROFILE.copy()]
        self.history = load_json(HISTORY_FILE, [])
        self.current_result = ""
        self.header_rows = []
        self.custom_variables = {}
        self.tray_icon = None
        self._tray_thread = None

        self._build_ui()
        self._load_profile_into_form(0)
        self._refresh_history()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Unmap>", self._on_minimize)

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            style.theme_use("clam")
        style.configure("TLabel", background="#f3f6fb", font=("Microsoft YaHei UI", 10))
        style.configure("TLabelframe", background="#ffffff")
        style.configure("TLabelframe.Label", background="#ffffff", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=6)
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=8)
        style.configure("TCombobox", padding=4)

        top = tk.Frame(self, bg="#ffffff", height=58, highlightbackground="#dbe4f0", highlightthickness=1)
        top.pack(fill="x")
        ttk.Button(top, text=self._autostart_button_text(), command=self._toggle_autostart).pack(side="left", padx=16, pady=10)

        body = tk.Frame(self, bg="#f3f6fb")
        body.pack(fill="both", expand=True, padx=12, pady=12)

        left = tk.Frame(body, bg="#f3f6fb", width=580)
        left.pack(side="left", fill="both", expand=True, padx=(0, 7))
        right = tk.Frame(body, bg="#f3f6fb", width=520)
        right.pack(side="left", fill="both", expand=True, padx=(7, 0))

        # API profile
        profile_box = ttk.LabelFrame(left, text="API 配置", padding=10)
        profile_box.pack(fill="both", expand=True)
        row = tk.Frame(profile_box, bg="#ffffff")
        row.pack(fill="x", pady=(0, 8))
        tk.Label(row, text="配置：", bg="#ffffff").pack(side="left")
        self.profile_var = tk.StringVar()
        self.profile_combo = ttk.Combobox(row, textvariable=self.profile_var, state="readonly", width=22)
        self.profile_combo.pack(side="left", padx=(4, 8))
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)
        ttk.Button(row, text="新建", command=self._new_profile).pack(side="left", padx=2)
        ttk.Button(row, text="保存配置", command=self._save_profile).pack(side="left", padx=2)
        ttk.Button(row, text="删除", command=self._delete_profile).pack(side="left", padx=2)

        self.api_url = tk.StringVar()
        self.method = tk.StringVar(value="POST")
        self.content_type = tk.StringVar(value="application/json")
        self.response_path = tk.StringVar(value="data.shortUrl")
        self.timeout_var = tk.StringVar(value="30")

        self._label_entry(profile_box, "API 地址：", self.api_url)
        row = tk.Frame(profile_box, bg="#ffffff")
        row.pack(fill="x", pady=4)
        tk.Label(row, text="请求方式：", bg="#ffffff").pack(side="left")
        ttk.Combobox(row, textvariable=self.method, values=["POST", "GET", "PUT", "PATCH"],
                     state="readonly", width=10).pack(side="left", padx=(4, 14))
        tk.Label(row, text="Content-Type：", bg="#ffffff").pack(side="left")
        ttk.Combobox(row, textvariable=self.content_type,
                     values=["application/json", "application/x-www-form-urlencoded", "text/plain"],
                     width=28).pack(side="left", padx=4)

        tk.Label(profile_box, text="请求头（Headers）：", bg="#ffffff",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(8, 3))
        self.headers_frame = tk.Frame(profile_box, bg="#ffffff")
        self.headers_frame.pack(fill="x")
        htools = tk.Frame(profile_box, bg="#ffffff")
        htools.pack(fill="x", pady=(3, 7))
        ttk.Button(htools, text="+ 添加请求头", command=self._add_header_row).pack(side="left")

        body_title = tk.Frame(profile_box, bg="#ffffff")
        body_title.pack(fill="x", pady=(3, 3))
        tk.Label(body_title, text="请求参数（Body）：", bg="#ffffff",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        ttk.Button(body_title, text="自定义变量…", command=self._manage_variables).pack(side="right")
        self.body_text = tk.Text(profile_box, height=7, wrap="word", font=("Consolas", 10),
                                 relief="solid", bd=1, padx=7, pady=6)
        self.body_text.pack(fill="x")
        tk.Label(profile_box, text="变量写法：内置{{longUrl}}已绑定长链接输入框； 点击“自定义变量…”管理变量，变量格式:{{变量名}}",
                 bg="#ffffff", fg="#64748b").pack(anchor="w", pady=(3, 7))

        self._label_entry(profile_box, "短链接字段路径：", self.response_path)
        tk.Label(profile_box, text="示例：data.shortUrl、url、data.result.link；支持 JSON 对象和数组路径。",
                 bg="#ffffff", fg="#64748b", wraplength=540, justify="left").pack(anchor="w", pady=(1, 4))
        row = tk.Frame(profile_box, bg="#ffffff")
        row.pack(fill="x", pady=(7, 0))
        tk.Label(row, text="超时（秒）：", bg="#ffffff").pack(side="left")
        ttk.Entry(row, textvariable=self.timeout_var, width=7).pack(side="left", padx=4)
        ttk.Button(row, text="测试接口", command=self._test_api).pack(side="right")

        # Right side input and output
        input_box = ttk.LabelFrame(right, text="输入长链接", padding=10)
        input_box.pack(fill="x", pady=(0, 10))
        self.long_url_text = tk.Text(input_box, height=4, wrap="word", font=("Microsoft YaHei UI", 10),
                                     relief="solid", bd=1, padx=7, pady=6)
        self.long_url_text.pack(fill="x")
        self.long_url_text.insert("1.0", "https://github.com")
        self.generate_btn = ttk.Button(input_box, text="🔗 生成短链接", style="Accent.TButton",
                                       command=self._generate)
        self.generate_btn.pack(fill="x", pady=(9, 0))

        io_box = ttk.LabelFrame(right, text="请求与响应", padding=10)
        io_box.pack(fill="both", expand=True, pady=(0, 10))
        tabs = ttk.Notebook(io_box)
        tabs.pack(fill="both", expand=True)
        req_frame = tk.Frame(tabs, bg="#ffffff")
        resp_frame = tk.Frame(tabs, bg="#ffffff")
        raw_frame = tk.Frame(tabs, bg="#ffffff")
        tabs.add(req_frame, text="请求信息")
        tabs.add(resp_frame, text="解析结果")
        tabs.add(raw_frame, text="原始响应")
        self.request_view = self._readonly_text(req_frame, height=10)
        self.parsed_view = self._readonly_text(resp_frame, height=10)
        self.raw_view = self._readonly_text(raw_frame, height=10)

        result_box = ttk.LabelFrame(right, text="生成结果", padding=10)
        result_box.pack(fill="x")
        self.result_status = tk.Label(result_box, text="等待生成短链接", bg="#ffffff", fg="#64748b",
                                      anchor="w", font=("Microsoft YaHei UI", 10, "bold"))
        self.result_status.pack(fill="x", pady=(0, 7))
        result_row = tk.Frame(result_box, bg="#ffffff")
        result_row.pack(fill="x")
        self.result_var = tk.StringVar()
        ttk.Entry(result_row, textvariable=self.result_var).pack(side="left", fill="x", expand=True)
        ttk.Button(result_row, text="复制", command=self._copy_result).pack(side="left", padx=(7, 0))
        result_actions = tk.Frame(result_box, bg="#ffffff")
        result_actions.pack(fill="x", pady=(7, 0))
        ttk.Button(result_actions, text="打开配置和历史", command=self._open_config_dir).pack(side="left")
        ttk.Button(result_actions, text="打开短链接", command=self._open_result).pack(side="left", padx=6)

        history_box = ttk.LabelFrame(right, text="最近历史记录", padding=8)
        history_box.pack(fill="both", expand=False, pady=(10, 0))
        self.history_list = tk.Listbox(history_box, height=5, font=("Microsoft YaHei UI", 9),
                                       relief="flat", activestyle="dotbox")
        self.history_list.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(history_box, orient="vertical", command=self.history_list.yview)
        scrollbar.pack(side="right", fill="y")
        self.history_list.configure(yscrollcommand=scrollbar.set)
        self.history_list.bind("<Double-Button-1>", self._use_history)

        history_actions = tk.Frame(history_box, bg="#ffffff")
        history_actions.pack(fill="x", pady=(5, 0))
        ttk.Button(history_actions, text="清空历史", command=self._clear_history).pack(side="right")

        self.status_var = tk.StringVar(value="就绪")
        status = tk.Label(self, textvariable=self.status_var, bg="#e8eef7", fg="#334155",
                          anchor="w", padx=12, pady=5)
        status.pack(fill="x", side="bottom")

    def _autostart_path(self):
        return os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"),
                            "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
                            "短链接生成器启动.bat")

    def _is_autostart_enabled(self):
        return os.path.exists(self._autostart_path())

    def _autostart_button_text(self):
        return "开机自启：已开启" if self._is_autostart_enabled() else "开机自启：已关闭"

    def _toggle_autostart(self):
        path = self._autostart_path()
        try:
            if os.path.exists(path):
                os.remove(path)
                self.status_var.set("已关闭开机自启")
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if getattr(sys, "frozen", False):
                    command = f'"{sys.executable}"'
                else:
                    command = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
                with open(path, "w", encoding="utf-8-sig") as f:
                    f.write('@echo off\r\nstart "" ' + command + '\r\n')
                self.status_var.set("已开启开机自启")
        except OSError as e:
            messagebox.showerror("开机自启设置失败", str(e), parent=self)
        for child in self.winfo_children():
            pass
        # Refresh the button label without retaining a platform-specific widget handle.
        self._build_autostart_button_refresh()

    def _build_autostart_button_refresh(self):
        # Locate the header's rightmost button and update its caption.
        try:
            top = self.winfo_children()[0]
            for widget in top.winfo_children():
                if isinstance(widget, ttk.Button):
                    widget.configure(text=self._autostart_button_text())
        except (IndexError, tk.TclError):
            pass

    def _create_tray_image(self):
        if Image is None:
            return None
        img = Image.new("RGB", (64, 64), "#2563eb")
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((5, 5, 59, 59), radius=12, fill="#2563eb")
        draw.text((15, 16), "SL", fill="white")
        return img

    def _ensure_tray_icon(self):
        if pystray is None or self.tray_icon is not None:
            return
        image = self._create_tray_image()
        if image is None:
            return
        menu = pystray.Menu(
            pystray.MenuItem("打开主窗口", lambda icon, item: self.after(0, self._restore_window), default=True),
            pystray.MenuItem("生成器退出", lambda icon, item: self.after(0, self._quit_app))
        )
        self.tray_icon = pystray.Icon("ShortLinkGenerator", image, APP_NAME, menu)
        self._tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self._tray_thread.start()

    def _on_minimize(self, event=None):
        if event is not None and event.widget is not self:
            return
        try:
            if self.state() == "iconic":
                self._ensure_tray_icon()
                if self.tray_icon is not None:
                    self.after(100, self.withdraw)
        except tk.TclError:
            pass

    def _restore_window(self):
        try:
            self.deiconify()
            self.state("normal")
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def _quit_app(self):
        try:
            if self.tray_icon is not None:
                self.tray_icon.stop()
                self.tray_icon = None
        except Exception:
            pass
        # 强制销毁主窗口并退出程序
        try:
            self.quit()
            self.destroy()
        except Exception:
            sys.exit(0)

    def _label_entry(self, parent, label, variable):
        row = tk.Frame(parent, bg="#ffffff")
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg="#ffffff", width=18, anchor="w").pack(side="left")
        ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)

    def _readonly_text(self, parent, height=8):
        box = tk.Text(parent, height=height, wrap="word", font=("Consolas", 9),
                      bg="#172535", fg="#e2edf8", insertbackground="#ffffff",
                      relief="flat", padx=8, pady=8)
        box.pack(fill="both", expand=True)
        box.configure(state="disabled")
        return box

    def _set_text(self, widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _refresh_profile_combo(self, selected=None):
        names = [p.get("name", f"配置{i+1}") for i, p in enumerate(self.profiles)]
        self.profile_combo["values"] = names
        if selected is None:
            selected = min(0, len(names) - 1)
        self.profile_combo.current(selected)

    def _on_profile_selected(self, _event=None):
        idx = self.profile_combo.current()
        if idx >= 0:
            self._load_profile_into_form(idx)

    def _load_profile_into_form(self, idx):
        if idx >= len(self.profiles):
            return
        p = self.profiles[idx]
        self.profile_var.set(p.get("name", "未命名配置"))
        self.api_url.set(p.get("url", ""))
        self.method.set(p.get("method", "POST"))
        self.content_type.set(p.get("content_type", "application/json"))
        self.response_path.set(p.get("response_path", "data.shortUrl"))
        self.timeout_var.set(str(p.get("timeout", 30)))
        self.custom_variables = p.get("variables", {})
        if not isinstance(self.custom_variables, dict):
            self.custom_variables = {}
        self.body_text.delete("1.0", "end")
        self.body_text.insert("1.0", p.get("body", ""))
        for child in self.headers_frame.winfo_children():
            child.destroy()
        self.header_rows = []
        for h in p.get("headers", []):
            self._add_header_row(h.get("key", ""), h.get("value", ""))

    def _add_header_row(self, key="", value=""):
        row = tk.Frame(self.headers_frame, bg="#ffffff")
        row.pack(fill="x", pady=2)
        k = ttk.Entry(row, width=22)
        v = ttk.Entry(row)
        k.insert(0, key)
        v.insert(0, value)
        k.pack(side="left", fill="x", expand=True, padx=(0, 4))
        v.pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(row, text="删除", width=5, command=lambda r=row: self._remove_header_row(r)).pack(side="left")
        self.header_rows.append((row, k, v))

    def _remove_header_row(self, row):
        self.header_rows = [x for x in self.header_rows if x[0] is not row]
        row.destroy()

    def _form_profile(self, name=None):
        headers = []
        for _, k, v in self.header_rows:
            key, value = k.get().strip(), v.get().strip()
            if key:
                headers.append({"key": key, "value": value})
        return {
            "name": name or self.profile_var.get().strip() or "我的 API",
            "url": self.api_url.get().strip(),
            "method": self.method.get().strip().upper(),
            "content_type": self.content_type.get().strip(),
            "headers": headers,
            "body": self.body_text.get("1.0", "end-1c"),
            "variables": dict(getattr(self, "custom_variables", {})),
            "response_path": self.response_path.get().strip(),
            "timeout": int(self.timeout_var.get() or "30")
        }

    def _manage_variables(self):
        """管理当前 API 配置的自定义模板变量。"""
        dialog = tk.Toplevel(self)
        dialog.title("自定义变量")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("570x430")
        dialog.minsize(500, 360)
        dialog.configure(bg="#f3f6fb")

        tk.Label(dialog, text="设置变量名和值，在 URL、Headers、Body 中使用 {{变量名}} 引用。",
                 bg="#f3f6fb", anchor="w", wraplength=530, justify="left").pack(fill="x", padx=12, pady=(12, 8))

        columns = ("name", "value")
        tree = ttk.Treeview(dialog, columns=columns, show="headings", height=10)
        tree.heading("name", text="变量名")
        tree.heading("value", text="变量值")
        tree.column("name", width=150, anchor="w")
        tree.column("value", width=350, anchor="w")
        tree.pack(fill="both", expand=True, padx=12, pady=4)

        form = tk.Frame(dialog, bg="#f3f6fb")
        form.pack(fill="x", padx=12, pady=8)
        tk.Label(form, text="变量名：", bg="#f3f6fb").grid(row=0, column=0, sticky="w", pady=3)
        name_var = tk.StringVar()
        name_entry = ttk.Entry(form, textvariable=name_var, width=22)
        name_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=3)
        tk.Label(form, text="变量值：", bg="#f3f6fb").grid(row=0, column=2, sticky="w", pady=3)
        value_var = tk.StringVar()
        value_entry = ttk.Entry(form, textvariable=value_var)
        value_entry.grid(row=0, column=3, sticky="ew", pady=3)
        form.columnconfigure(3, weight=1)

        def refresh(select_name=None):
            for item in tree.get_children():
                tree.delete(item)
            for key, value in sorted(self.custom_variables.items()):
                iid = tree.insert("", "end", values=(key, value))
                if key == select_name:
                    tree.selection_set(iid)
                    tree.focus(iid)

        def load_selected(_event=None):
            selected = tree.selection()
            if not selected:
                return
            name, value = tree.item(selected[0], "values")
            name_var.set(name)
            value_var.set(value)

        def save_variable():
            name = name_var.get().strip()
            value = value_var.get()
            if not name:
                messagebox.showwarning("变量名不能为空", "请输入变量名，例如 token 或 domain。", parent=dialog)
                return
            if "{{" in name or "}}" in name or any(ch.isspace() for ch in name):
                messagebox.showwarning("变量名格式错误", "变量名不能包含空格或 {{ }}。", parent=dialog)
                return
            selected = tree.selection()
            old_name = tree.item(selected[0], "values")[0] if selected else None
            if name in self.custom_variables and name != old_name:
                if not messagebox.askyesno("覆盖变量", f"变量“{name}”已存在，是否覆盖？", parent=dialog):
                    return
            if old_name and old_name != name:
                self.custom_variables.pop(old_name, None)
            self.custom_variables[name] = value
            refresh(name)
            self.status_var.set(f"变量已更新：{{{{{name}}}}}（记得保存 API 配置）")

        def delete_variable():
            selected = tree.selection()
            if not selected:
                messagebox.showinfo("提示", "请先选择要删除的变量。", parent=dialog)
                return
            name = tree.item(selected[0], "values")[0]
            if messagebox.askyesno("确认删除", f"确定删除变量“{name}”吗？", parent=dialog):
                self.custom_variables.pop(name, None)
                name_var.set("")
                value_var.set("")
                refresh()

        tree.bind("<<TreeviewSelect>>", load_selected)
        buttons = tk.Frame(dialog, bg="#f3f6fb")
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(buttons, text="添加 / 更新", command=save_variable).pack(side="left")
        ttk.Button(buttons, text="删除选中", command=delete_variable).pack(side="left", padx=6)
        ttk.Button(buttons, text="关闭", command=dialog.destroy).pack(side="right")
        refresh()
        name_entry.focus_set()

    def _new_profile(self):
        name = simpledialog.askstring("新建配置", "请输入配置名称：", parent=self)
        if not name:
            return
        name = name.strip()
        
        # 【新增逻辑】检查配置名是否已存在
        if any(p.get("name") == name for p in self.profiles):
            messagebox.showwarning("创建失败", f"配置名称“{name}”已存在，请使用其他名称或直接修改该配置。")
            return

        p = DEFAULT_PROFILE.copy()
        p["name"] = name
        self.profiles.append(p)
        self._refresh_profile_combo(len(self.profiles) - 1)
        self._load_profile_into_form(len(self.profiles) - 1)
        self._save_profiles_file()
        self.status_var.set(f"已创建新配置：{name}")

    def _save_profile(self):
        try:
            p = self._form_profile()
        except ValueError:
            messagebox.showerror("配置错误", "超时秒数必须是数字。")
            return

        current_name = p["name"]
        
        # 查找当前配置名是否已存在于列表中
        target_idx = -1
        combo_idx = self.profile_combo.current()
        
        if 0 <= combo_idx < len(self.profiles):
            if self.profiles[combo_idx].get("name") == current_name:
                target_idx = combo_idx
                
        if target_idx == -1:
            for i, prof in enumerate(self.profiles):
                if prof.get("name") == current_name:
                    target_idx = i
                    break

        if target_idx >= 0:
            # 如果名字相同，则更新已有配置
            self.profiles[target_idx] = p
            idx_to_refresh = target_idx
        else:
            # 如果不存在，则追加保存
            self.profiles.append(p)
            idx_to_refresh = len(self.profiles) - 1

        self._save_profiles_file()
        self._refresh_profile_combo(idx_to_refresh)
        self.status_var.set("API 配置已保存")
        messagebox.showinfo("保存成功", "API 配置已保存。")

    def _delete_profile(self):
        if len(self.profiles) <= 1:
            messagebox.showwarning("无法删除", "至少需要保留一个 API 配置。")
            return
        idx = self.profile_combo.current()
        if idx < 0:
            return
        if messagebox.askyesno("确认删除", f"确定删除配置“{self.profiles[idx].get('name')}”吗？"):
            del self.profiles[idx]
            self._save_profiles_file()
            self._refresh_profile_combo(0)
            self._load_profile_into_form(0)

    def _save_profiles_file(self):
        save_json(CONFIG_FILE, self.profiles)

    def _replace_variables(self, value, variables):
        """将文本中的 {{name}} 替换为变量值；longUrl 为内置变量。"""
        if not isinstance(value, str):
            return value
        pattern = re.compile(r"{{\s*([A-Za-z0-9_.-]+)\s*}}")
        return pattern.sub(lambda match: str(variables.get(match.group(1), match.group(0))), value)

    def _replace_json_variables(self, value, variables):
        """递归替换 JSON 对象/数组中的字符串值，避免变量值中的引号破坏 JSON。"""
        if isinstance(value, str):
            return self._replace_variables(value, variables)
        if isinstance(value, list):
            return [self._replace_json_variables(item, variables) for item in value]
        if isinstance(value, dict):
            return {key: self._replace_json_variables(item, variables) for key, item in value.items()}
        return value

    def _build_request(self, long_url):
        profile = self._form_profile()
        variables = dict(profile.get("variables", {}))
        variables["longUrl"] = long_url
        endpoint = self._replace_variables(profile["url"], variables)
        if not endpoint:
            raise ValueError("请先填写 API 地址。")
        parsed = urlparse(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("API 地址必须以 http:// 或 https:// 开头。")
        headers = {
            self._replace_variables(h["key"], variables): self._replace_variables(h["value"], variables)
            for h in profile["headers"] if h["key"]
        }
        content_type = self._replace_variables(profile["content_type"], variables)
        if content_type and not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = content_type
        method = profile["method"]
        body_text = profile["body"]
        data = None
        if method != "GET":
            if content_type == "application/json":
                try:
                    body_obj = json.loads(body_text)
                    body_obj = self._replace_json_variables(body_obj, variables)
                    data = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
                except json.JSONDecodeError as e:
                    raise ValueError(f"请求 Body 不是有效 JSON：{e}")
            elif content_type == "application/x-www-form-urlencoded":
                from urllib.parse import urlencode
                try:
                    obj = json.loads(body_text)
                    obj = self._replace_json_variables(obj, variables)
                    data = urlencode(obj).encode("utf-8")
                except Exception:
                    data = self._replace_variables(body_text, variables).encode("utf-8")
            else:
                data = self._replace_variables(body_text, variables).encode("utf-8")
        req = urllib.request.Request(endpoint, data=data, headers=headers, method=method)
        summary = f"{method} {endpoint}\\n" + "\\n".join(f"{k}: {v}" for k, v in headers.items())
        if data is not None:
            summary += "\\n\\n" + data.decode("utf-8", errors="replace")
        return profile, req, summary

    def _generate(self):
        long_url = self.long_url_text.get("1.0", "end-1c").strip()
        if not long_url:
            messagebox.showwarning("缺少长链接", "请先输入需要缩短的长链接。")
            return
        if not long_url.startswith(("http://", "https://")):
            messagebox.showwarning("链接格式", "长链接建议以 http:// 或 https:// 开头。")
        try:
            profile, req, summary = self._build_request(long_url)
        except Exception as e:
            messagebox.showerror("配置错误", str(e))
            return
        self._set_text(self.request_view, summary)
        self.generate_btn.configure(state="disabled")
        self.status_var.set("正在请求 API…")
        self.result_status.configure(text="正在生成…", fg="#b45309")
        threading.Thread(target=self._request_worker, args=(profile, req, long_url), daemon=True).start()

    def _request_worker(self, profile, req, long_url):
        status_code = None
        try:
            timeout = max(1, int(profile.get("timeout", 30)))
            with urllib.request.urlopen(req, timeout=timeout) as response:
                status_code = response.status
                raw = response.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
                pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
                short_url = get_json_path(parsed, profile.get("response_path", "data.shortUrl"))
                if isinstance(short_url, (dict, list)):
                    raise ValueError("解析路径对应的是对象/数组，不是短链接文本。")
                short_url = str(short_url).strip()
            except Exception as e:
                self.after(0, lambda: self._show_error(f"接口返回成功，但解析短链接失败：{e}", raw, pretty if 'pretty' in locals() else raw))
                return
            self.after(0, lambda: self._show_success(short_url, raw, pretty, long_url, status_code))
        except urllib.error.HTTPError as e:
            code, reason = e.code, e.reason
            try:
                raw = e.read().decode("utf-8", errors="replace")
            except Exception:
                raw = str(e)
            error_message = f"HTTP 错误：{code} {reason}"
            self.after(0, lambda message=error_message, body=raw: self._show_error(message, body, body))
        except Exception as e:
            self.after(0, lambda: self._show_error(f"请求失败：{e}", "", ""))

    def _show_success(self, short_url, raw, pretty, long_url, status_code):
        self.generate_btn.configure(state="normal")
        self.current_result = short_url
        self.result_var.set(short_url)
        self.result_status.configure(text=f"✓ 短链接生成成功（HTTP {status_code}）", fg="#15803d")
        self._set_text(self.parsed_view, f"提取字段：{self.response_path.get()}\n短链接：{short_url}")
        self._set_text(self.raw_view, pretty)
        self.status_var.set("生成成功")
        self._add_history(long_url, short_url)
        try:
            self.clipboard_clear()
            self.clipboard_append(short_url)
            self.update()
            self.status_var.set("生成成功，短链接已复制到剪贴板")
        except tk.TclError:
            pass

    def _show_error(self, message, raw, parsed):
        self.generate_btn.configure(state="normal")
        self.result_status.configure(text="✕ 生成失败", fg="#b91c1c")
        self._set_text(self.parsed_view, message)
        self._set_text(self.raw_view, raw or "(没有响应内容)")
        self.status_var.set(message)
        messagebox.showerror("生成失败", message)

    def _test_api(self):
        long_url = self.long_url_text.get("1.0", "end-1c").strip() or "https://example.com/test"
        try:
            profile, req, summary = self._build_request(long_url)
        except Exception as e:
            messagebox.showerror("配置错误", str(e))
            return
        self._set_text(self.request_view, summary)
        self.status_var.set("正在测试接口…")
        self.generate_btn.configure(state="disabled")
        threading.Thread(target=self._request_worker, args=(profile, req, long_url), daemon=True).start()

    def _copy_result(self):
        value = self.result_var.get().strip()
        if not value:
            messagebox.showinfo("提示", "目前没有可复制的短链接。")
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self.update()
        self.status_var.set("已复制短链接")

    def _open_result(self):
        import webbrowser
        value = self.result_var.get().strip()
        if value:
            webbrowser.open(value)

    def _add_history(self, long_url, short_url):
        """添加或更新历史记录（去重、置顶、保存、刷新）"""
        # 1. 过滤掉已经存在的相同长链接
        self.history = [h for h in self.history if h.get("long_url") != long_url]
        
        # 2. 将最新记录插入到最前端
        self.history.insert(0, {
            "long_url": long_url,
            "short_url": short_url,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        
        # 3. 限制最多保存 100 条
        if len(self.history) > 100:
            self.history = self.history[:100]
            
        # 4. 写入本地文件并刷新界面
        save_json(HISTORY_FILE, self.history)
        self._refresh_history()

    def _refresh_history(self):
        """刷新右侧历史记录列表框的显示"""
        self.history_list.delete(0, "end")
        for h in self.history:
            time_str = h.get("time", "")
            long_u = h.get("long_url", "")
            short_u = h.get("short_url", "")
            display_text = f"[{time_str}] {short_u}"
            self.history_list.insert("end", display_text)

    def _use_history(self, _event=None):
        sel = self.history_list.curselection()
        if not sel:
            return
        item = self.history[sel[0]]
        self.long_url_text.delete("1.0", "end")
        self.long_url_text.insert("1.0", item.get("long_url", ""))
        self.result_var.set(item.get("short_url", ""))
        self.current_result = item.get("short_url", "")
        self.result_status.configure(text="已载入历史记录", fg="#2563eb")

    def _clear_history(self):
        """清空所有历史记录"""
        if messagebox.askyesno("确认清空", "确定要清空所有的历史记录吗？", parent=self):
            # 1. 清空内存数据
            self.history = []
            # 2. 清空本地 JSON 文件
            save_json(HISTORY_FILE, self.history)
            # 3. 刷新界面
            self._refresh_history()
            self.status_var.set("历史记录已清空")

    def _open_config_dir(self):
        """打开配置文件所在的目录"""
        ensure_data_dir()
        try:
            os.startfile(DATA_DIR)
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开文件夹：{e}", parent=self)

    def _on_close(self):
        """点击右上角 X 关闭时，隐藏窗口并最小化到系统托盘"""
        try:
            if pystray is not None:
                # 确保系统托盘图标已创建并运行
                self._ensure_tray_icon()
                # 隐藏主窗口
                self.withdraw()
                self.status_var.set("程序已最小化到系统托盘")
            else:
                # 如果没有安装 pystray 库，则直接退出程序
                self._quit_app()
        except Exception:
            self._quit_app()
            
if __name__ == "__main__":
    App().mainloop()
