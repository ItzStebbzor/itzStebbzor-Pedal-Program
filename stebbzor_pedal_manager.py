import json
import re
import os
import threading
import queue
import sys
import time
import shutil
import zipfile
import tempfile
import subprocess
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from pathlib import Path

try:
    import keyboard
except ImportError:
    keyboard = None

try:
    import psutil
except ImportError:
    psutil = None

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None
    Image = ImageDraw = None

APP_NAME = "Stebbzor Pedal Manager"
APP_VERSION = "1.4.2"
UPDATE_ASSET_NAME = "StebbzorPedalManager-Windows.zip"
APP_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / "StebbzorPedalManager"
PROFILES_FILE = APP_DIR / "profiles.json"
SETTINGS_FILE = APP_DIR / "settings.json"
STARTUP_VBS = Path(os.getenv("APPDATA", str(Path.home()))) / r"Microsoft\Windows\Start Menu\Programs\Startup\StebbzorPedalManager.vbs"

def load_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        data.setdefault("games", [])
        data.setdefault("auto_start", False)
        data.setdefault("auto_detect", True)
        data.setdefault("minimize_to_tray", False)
        data.setdefault("update_repo", "ItzStebbzor/itzStebbzor-Pedal-Program")
        data.setdefault("auto_check_updates", False)
        return data
    except Exception:
        return {"games": [], "auto_start": False, "auto_detect": True, "minimize_to_tray": False, "update_repo": "ItzStebbzor/itzStebbzor-Pedal-Program", "auto_check_updates": False}

def save_settings(settings):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")

def set_windows_startup(enabled, start_minimized=False):
    if enabled:
        target = str(Path(os.path.abspath(__file__))) if not getattr(__import__("sys"), "frozen", False) else str(Path(__import__("sys").executable))
        # Use a hidden VBScript launcher so no console window flashes at sign-in.
        args = " --minimized" if start_minimized else ""
        command = f'CreateObject("Wscript.Shell").Run Chr(34) & "{target}" & Chr(34) & "{args}", 0, False'
        STARTUP_VBS.parent.mkdir(parents=True, exist_ok=True)
        STARTUP_VBS.write_text('Set WshShell = CreateObject("WScript.Shell")\n' + command + '\n', encoding="utf-8")
    else:
        try:
            STARTUP_VBS.unlink()
        except FileNotFoundError:
            pass

DEFAULT_PROFILE = {
    "name": "Default",
    "mappings": [
        {"pedal": "Left", "input": "f13", "output": "ctrl", "mode": "Hold"},
        {"pedal": "Middle", "input": "f14", "output": "alt", "mode": "Hold"},
        {"pedal": "Right", "input": "f15", "output": "v", "mode": "Hold"},
    ],
}

def load_profiles():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if not PROFILES_FILE.exists():
        return {"Default": DEFAULT_PROFILE.copy()}
    try:
        data = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data:
            return data
    except Exception:
        pass
    return {"Default": DEFAULT_PROFILE.copy()}

def save_profiles(profiles):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_FILE.write_text(json.dumps(profiles, indent=2), encoding="utf-8")

class Remapper:
    def __init__(self, status_callback):
        self.status_callback = status_callback
        self.hook = None
        self.block_handles = []
        self.active = False
        self.mapping = {}
        self.down = {}
        self.injected = {}
        self.lock = threading.RLock()

    def set_mapping(self, rows):
        with self.lock:
            self.mapping = {}
            for row in rows:
                src = row["input"].strip().lower()
                dst = row["output"].strip().lower()
                if src and dst:
                    self.mapping[src] = {"output": dst, "mode": row.get("mode", "Hold")}
            self.down.clear()

    def _consume_injected(self, name, event_type):
        key = (name.lower(), event_type)
        count = self.injected.get(key, 0)
        if count:
            if count == 1:
                self.injected.pop(key, None)
            else:
                self.injected[key] = count - 1
            return True
        return False

    def _inject(self, name, event_type):
        key = (name.lower(), event_type)
        with self.lock:
            self.injected[key] = self.injected.get(key, 0) + 1
        try:
            if event_type == "down":
                keyboard.press(name)
            else:
                keyboard.release(name)
        except Exception:
            with self.lock:
                self.injected[key] = max(0, self.injected.get(key, 1) - 1)

    def _on_event(self, event):
        if not self.active or event.event_type not in ("down", "up"):
            return
        name = (event.name or "").lower()
        if not name:
            return
        with self.lock:
            if self._consume_injected(name, event.event_type):
                return
            item = self.mapping.get(name)
        if not item:
            return

        output = item["output"]
        mode = item["mode"]
        if mode == "Toggle":
            if event.event_type == "down":
                with self.lock:
                    if self.down.get(name + "_toggle_held"):
                        return
                    self.down[name + "_toggle_held"] = True
                    is_on = self.down.get(name + "_toggle_on", False)
                    self.down[name + "_toggle_on"] = not is_on
                self._inject(output, "up" if is_on else "down")
            else:
                with self.lock:
                    self.down[name + "_toggle_held"] = False
        else:
            if event.event_type == "down":
                with self.lock:
                    if self.down.get(name):
                        return
                    self.down[name] = True
                self._inject(output, "down")
            else:
                with self.lock:
                    was_down = self.down.pop(name, False)
                if was_down:
                    self._inject(output, "up")

    def start(self):
        if keyboard is None:
            raise RuntimeError("The keyboard package is not installed. Run install.bat first.")
        if self.active:
            return
        self.active = True
        # Observe all events, but block only the configured pedal input keys.
        # This avoids swallowing unrelated keyboard typing while remapping is on.
        self.hook = keyboard.hook(self._on_event, suppress=False)
        try:
            for src in self.mapping:
                self.block_handles.append(keyboard.block_key(src))
        except Exception:
            self.active = False
            if self.hook is not None:
                keyboard.unhook(self.hook)
                self.hook = None
            for handle in self.block_handles:
                try:
                    keyboard.unhook(handle)
                except Exception:
                    pass
            self.block_handles.clear()
            raise
        self.status_callback("ACTIVE — pedal inputs are being remapped")

    def stop(self):
        self.active = False
        if keyboard is not None and self.hook is not None:
            try:
                keyboard.unhook(self.hook)
            except Exception:
                pass
        self.hook = None
        for handle in self.block_handles:
            try:
                keyboard.unhook(handle)
            except Exception:
                pass
        self.block_handles.clear()
        with self.lock:
            held = []
            for source, item in self.mapping.items():
                if item["mode"] == "Hold" and self.down.get(source):
                    held.append(item["output"])
                elif item["mode"] == "Toggle" and self.down.get(source + "_toggle_on"):
                    held.append(item["output"])
            self.down.clear()
        for output in held:
            try:
                self._inject(output, "up")
            except Exception:
                pass
        self.status_callback("STOPPED — keyboard works normally")


class RoundedPanel(tk.Frame):
    """A lightweight canvas-backed card with rounded corners."""
    def __init__(self, parent, padding=12, radius=16, **kwargs):
        super().__init__(parent, bg="#111214", bd=0, highlightthickness=0, **kwargs)
        self.radius = radius
        self.canvas = tk.Canvas(self, bg="#111214", bd=0, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.content = ttk.Frame(self.canvas, style="Panel.TFrame", padding=padding)
        self.window_id = self.canvas.create_window(10, 10, anchor="nw", window=self.content)
        self.canvas.bind("<Configure>", self._redraw)
        self.content.bind("<Configure>", self._content_changed)
        self.after_idle(self._redraw)

    def _content_changed(self, _event=None):
        self.after_idle(self._redraw)

    def _redraw(self, _event=None):
        width = max(self.canvas.winfo_width(), self.winfo_width(), 240)
        requested_height = self.content.winfo_reqheight() + 20
        height = max(requested_height, 70)
        if abs(self.canvas.winfo_height() - height) > 2:
            self.canvas.configure(height=height)
        self.canvas.coords(self.window_id, 10, 10)
        self.canvas.itemconfigure(self.window_id, width=max(1, width - 20))
        self.canvas.delete("rounded-card")
        r = min(self.radius, width // 4, height // 4)
        points = [
            10+r, 0, width-10-r, 0, width-10, 0,
            width-10, r, width-10, height-10-r, width-10, height-10,
            width-10-r, height-10, 10+r, height-10, 10, height-10,
            10, height-10-r, 10, r, 10, 0
        ]
        self.canvas.create_polygon(points, smooth=True, splinesteps=18,
                                   fill="#1b1d20", outline="#303438",
                                   width=1, tags="rounded-card")
        self.canvas.tag_lower("rounded-card")

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1040x860")
        self.minsize(940, 800)
        self.configure(bg="#15171b")
        self.profiles = load_profiles()
        self.settings = load_settings()
        self.games = self.settings.get("games", [])
        self.manual_profile = next(iter(self.profiles))
        self.current_profile = self.manual_profile
        self.detected_process = None
        self.tray_icon = None
        self.tray_thread = None
        self.allow_exit = False
        self.rows = []
        self.events = queue.Queue()
        self.remapper = Remapper(self.set_status_threadsafe)
        self._style()
        self._build()
        self.load_profile(self.current_profile)
        self.refresh_games()
        self.auto_start_var.set(bool(self.settings.get("auto_start", False)))
        self.auto_detect_var.set(bool(self.settings.get("auto_detect", True)))
        self.minimize_to_tray_var.set(bool(self.settings.get("minimize_to_tray", False)))
        self.update_repo_var.set(self.settings.get("update_repo", ""))
        self.auto_check_updates_var.set(bool(self.settings.get("auto_check_updates", False)))
        self.after(150, self.process_events)
        self.after(5000, self.check_game_processes)
        if self.auto_check_updates_var.get():
            self.after(2500, self.check_for_updates)
        self.protocol("WM_DELETE_WINDOW", self.on_window_close)
        self.bind("<Unmap>", self.on_window_minimize)
        if "--minimized" in __import__("sys").argv and self.minimize_to_tray_var.get():
            self.after(400, self.minimize_to_tray)

    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        bg = "#111214"
        panel = "#1b1d20"
        field = "#25282c"
        text = "#eceeef"
        muted = "#a7abb0"
        line = "#34383d"
        s.configure(".", background=panel, foreground=text, fieldbackground=field,
                    font=("Segoe UI", 10))
        s.configure("TFrame", background=bg)
        s.configure("Panel.TFrame", background=panel)
        s.configure("TLabel", background=bg, foreground=text, font=("Segoe UI", 10))
        s.configure("Title.TLabel", background=bg, font=("Segoe UI Semibold", 21),
                    foreground="#f5f6f7")
        s.configure("Muted.TLabel", background=bg, foreground=muted,
                    font=("Segoe UI", 9))
        s.configure("TButton", background="#303338", foreground="#f1f2f3",
                    padding=(12, 8), borderwidth=0, focusthickness=0,
                    font=("Segoe UI", 9))
        s.map("TButton", background=[("active", "#41454b"), ("pressed", "#50555c")],
              foreground=[("disabled", "#777b80")])
        s.configure("Accent.TButton", background="#e1e3e5", foreground="#151618",
                    padding=(16, 9), borderwidth=0,
                    font=("Segoe UI Semibold", 10))
        s.map("Accent.TButton", background=[("active", "#ffffff"), ("pressed", "#c5c9cd")],
              foreground=[("active", "#111214")])
        s.configure("Treeview", background=panel, fieldbackground=panel,
                    foreground=text, rowheight=34, borderwidth=0,
                    font=("Segoe UI", 10))
        s.configure("Treeview.Heading", background="#292c30", foreground="#c9cdd1",
                    font=("Segoe UI Semibold", 9), relief="flat", padding=(8, 8))
        s.map("Treeview", background=[("selected", "#4a4f55")],
              foreground=[("selected", "#ffffff")])
        s.configure("TEntry", fieldbackground=field, foreground=text,
                    insertcolor=text, bordercolor=line, lightcolor=line,
                    darkcolor=line, padding=(8, 7), relief="flat")
        s.configure("TCombobox", fieldbackground=field, background=field,
                    foreground=text, arrowcolor="#d9dcdf", padding=(7, 6),
                    bordercolor=line, lightcolor=line, darkcolor=line)
        s.map("TCombobox", fieldbackground=[("readonly", field)],
              foreground=[("readonly", text)], selectbackground=[("readonly", field)],
              selectforeground=[("readonly", text)])
        s.configure("TCheckbutton", background=panel, foreground=text,
                    font=("Segoe UI", 9))
        s.map("TCheckbutton", background=[("active", panel)],
              foreground=[("active", "#ffffff")])

    def _build(self):
        root = ttk.Frame(self, padding=22)
        root.pack(fill="both", expand=True)
        top = ttk.Frame(root)
        top.pack(fill="x")
        ttk.Label(top, text="STEBBZOR PEDAL MANAGER", style="Title.TLabel").pack(anchor="w")
        ttk.Label(top, text=f"A clean, lightweight control panel for your foot-pedal key profiles.  v{APP_VERSION}",
                  style="Muted.TLabel").pack(anchor="w", pady=(3, 16))

        profile_panel = RoundedPanel(root, padding=14)
        profile_panel.pack(fill="x", pady=(0, 14))
        profile_body = profile_panel.content
        ttk.Label(profile_body, text="PROFILE", background="#1b1d20",
                  foreground="#a7abb0", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        pr = ttk.Frame(profile_body, style="Panel.TFrame")
        pr.pack(fill="x", pady=(7, 0))
        self.profile_var = tk.StringVar()
        self.profile_combo = ttk.Combobox(pr, textvariable=self.profile_var, state="readonly", width=28)
        self.profile_combo.pack(side="left")
        self.profile_combo.bind("<<ComboboxSelected>>", self.on_profile_change)
        ttk.Button(pr, text="New profile", command=self.new_profile).pack(side="left", padx=(8, 0))
        ttk.Button(pr, text="Duplicate", command=self.duplicate_profile).pack(side="left", padx=(8, 0))
        ttk.Button(pr, text="Rename", command=self.rename_profile).pack(side="left", padx=(8, 0))
        ttk.Button(pr, text="Delete", command=self.delete_profile).pack(side="left", padx=(8, 0))

        game_panel = RoundedPanel(root, padding=12)
        game_panel.pack(fill="x", pady=(0, 12))
        game_body = game_panel.content
        ttk.Label(game_body, text="AUTOMATIC GAME DETECTION", background="#1b1d20",
                  foreground="#a7abb0", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.game_tree = ttk.Treeview(game_body, columns=("process", "profile"), show="headings", height=3)
        self.game_tree.heading("process", text="GAME PROCESS (.EXE)")
        self.game_tree.heading("profile", text="LOAD PROFILE")
        self.game_tree.column("process", width=330)
        self.game_tree.column("profile", width=300)
        self.game_tree.pack(fill="x", pady=(6, 8))
        game_edit = ttk.Frame(game_body, style="Panel.TFrame")
        game_edit.pack(fill="x")
        self.process_var = tk.StringVar()
        self.game_profile_var = tk.StringVar()
        ttk.Entry(game_edit, textvariable=self.process_var, width=28).pack(side="left")
        ttk.Label(game_edit, text="  Profile:", background="#1b1d20", foreground="#c9cdd1").pack(side="left", padx=(8, 4))
        self.game_profile_combo = ttk.Combobox(game_edit, textvariable=self.game_profile_var,
                                               state="readonly", width=22)
        self.game_profile_combo.pack(side="left")
        ttk.Button(game_edit, text="Add / Update game", command=self.add_game).pack(side="left", padx=8)
        ttk.Button(game_edit, text="Remove selected", command=self.remove_game).pack(side="left")
        self.auto_detect_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(game_body, text="Enable automatic game detection", variable=self.auto_detect_var,
                        command=self.save_game_settings).pack(anchor="w", pady=(8, 0))
        ttk.Label(game_body, text="Enter the process filename, e.g. RainbowSix.exe (not the desktop shortcut name).",
                  background="#1b1d20", foreground="#a7abb0").pack(anchor="w", pady=(4, 0))

        update_panel = RoundedPanel(root, padding=12)
        update_panel.pack(fill="x", pady=(0, 12))
        update_body = update_panel.content
        ttk.Label(update_body, text="APPLICATION UPDATES", background="#1b1d20",
                  foreground="#a7abb0", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        update_row = ttk.Frame(update_body, style="Panel.TFrame")
        update_row.pack(fill="x", pady=(7, 0))
        ttk.Label(update_row, text="GitHub repository:", background="#1b1d20",
                  foreground="#c9cdd1").pack(side="left")
        self.update_repo_var = tk.StringVar()
        ttk.Entry(update_row, textvariable=self.update_repo_var, width=30).pack(side="left", padx=(8, 8))
        ttk.Button(update_row, text="Save", command=self.save_update_settings).pack(side="left")
        ttk.Button(update_row, text="Check for updates", command=self.check_for_updates).pack(side="left", padx=(8, 0))
        self.auto_check_updates_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(update_body, text="Check for updates automatically when the app starts",
                        variable=self.auto_check_updates_var,
                        command=self.save_update_settings).pack(anchor="w", pady=(7, 0))
        ttk.Label(update_body,
                  text="Enter your GitHub repository as owner/repository. Releases must include "
                       + UPDATE_ASSET_NAME + " as a Windows ZIP asset.",
                  background="#1b1d20", foreground="#a7abb0", wraplength=850).pack(anchor="w", pady=(4, 0))

        mapping_panel = RoundedPanel(root, padding=14)
        mapping_panel.pack(fill="both", expand=True)
        mapping_body = mapping_panel.content
        ttk.Label(mapping_body, text="PEDAL MAPPINGS", background="#1b1d20",
                  foreground="#a7abb0", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Label(mapping_body,
                  text="Type a key or use Capture, then choose whether the output is held or toggled.",
                  background="#1b1d20", foreground="#a7abb0").pack(anchor="w", pady=(4, 10))
        columns = ("pedal", "input", "output", "mode")
        self.tree = ttk.Treeview(mapping_body, columns=columns, show="headings", height=7, selectmode="browse")
        for col, label, width in [
            ("pedal", "PEDAL", 130), ("input", "CURRENT INPUT KEY", 210),
            ("output", "REMAP TO", 210), ("mode", "BEHAVIOUR", 150)
        ]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="center")
        self.tree.pack(fill="x")
        editor = ttk.Frame(mapping_body, style="Panel.TFrame")
        editor.pack(fill="x", pady=(12, 4))
        self.pedal_var = tk.StringVar(value="Left")
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="Hold")
        self._field(editor, "Pedal", self.pedal_var, 12, ("Left", "Middle", "Right", "Pedal 4", "Pedal 5"))
        self._key_field(editor, "Current input key", self.input_var, "input")
        self._key_field(editor, "Remap to", self.output_var, "output")
        self._field(editor, "Behaviour", self.mode_var, 10, ("Hold", "Toggle"))
        actions = ttk.Frame(mapping_body, style="Panel.TFrame")
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Add / Update mapping", command=self.add_mapping).pack(side="left")
        ttk.Button(actions, text="Remove selected", command=self.remove_mapping).pack(side="left", padx=8)
        ttk.Button(actions, text="Clear editor", command=self.clear_editor).pack(side="left")

        bottom = ttk.Frame(root)
        bottom.pack(fill="x", pady=(14, 0))
        self.status_var = tk.StringVar(value="STOPPED — keyboard works normally")
        self.status_label = ttk.Label(bottom, textvariable=self.status_var, style="Muted.TLabel")
        self.status_label.pack(anchor="w", pady=(0, 10))
        controls = ttk.Frame(bottom)
        controls.pack(fill="x")
        self.start_btn = ttk.Button(controls, text="▶  ENABLE REMAPPING", style="Accent.TButton", command=self.toggle_remap)
        self.start_btn.pack(side="left")
        ttk.Button(controls, text="Save profile", command=self.save_current).pack(side="left", padx=8)
        ttk.Button(controls, text="Open profiles folder", command=self.open_profiles_folder).pack(side="left")
        self.auto_start_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bottom, text="Launch Stebbzor Pedal Manager when Windows starts",
                        variable=self.auto_start_var, command=self.toggle_windows_startup).pack(anchor="w", pady=(10, 0))
        self.minimize_to_tray_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bottom, text="Minimize to system tray when minimized or closed",
                        variable=self.minimize_to_tray_var, command=self.toggle_tray_preference).pack(anchor="w", pady=(5, 0))
        ttk.Label(bottom, text="Tip: Capture lets you press the pedal or target key directly. Remapping must be enabled to activate mappings.",
                  style="Muted.TLabel").pack(anchor="w", pady=(12, 0))

    def save_update_settings(self):
        repo = self.update_repo_var.get().strip().strip("/")
        if repo and (repo.count("/") != 1 or any(part in ("", ".", "..") for part in repo.split("/"))):
            messagebox.showwarning("GitHub repository",
                                   "Enter the repository in owner/repository format, for example: myname/myapp.")
            return
        self.settings["update_repo"] = repo
        self.settings["auto_check_updates"] = bool(self.auto_check_updates_var.get())
        save_settings(self.settings)
        self.status_var.set("Update settings saved")

    @staticmethod
    def _version_tuple(value):
        value = str(value).strip().lstrip("vV")
        nums = re.findall(r"\d+", value)
        return tuple(int(x) for x in nums[:4]) if nums else (0,)

    def check_for_updates(self):
        self.save_update_settings()
        repo = self.settings.get("update_repo", "").strip()
        if not repo:
            messagebox.showinfo(
                "Update checker setup",
                "To check for updates, first enter the GitHub repository that publishes this app's releases.\\n\\n"
                "Use owner/repository format. The app does not have an update server configured yet."
            )
            return
        self.status_var.set("Checking for updates…")
        threading.Thread(target=self._check_updates_worker, args=(repo,), daemon=True).start()

    def _check_updates_worker(self, repo):
        try:
            api_url = f"https://api.github.com/repos/{repo}/releases/latest"
            request = urllib.request.Request(
                api_url,
                headers={"User-Agent": "Stebbzor-Pedal-Manager", "Accept": "application/vnd.github+json"}
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                release = json.loads(response.read().decode("utf-8"))
            tag = release.get("tag_name", "")
            if not tag:
                raise RuntimeError("The GitHub repository has no published latest release.")
            if self._version_tuple(tag) <= self._version_tuple(APP_VERSION):
                self.events.put(("update_info", f"You're up to date (v{APP_VERSION})."))
                return
            asset = next((a for a in release.get("assets", [])
                          if a.get("name") == UPDATE_ASSET_NAME), None)
            if not asset:
                raise RuntimeError(
                    f"Release {tag} was found, but it does not contain the required asset "
                    f"'{UPDATE_ASSET_NAME}'."
                )
            self.events.put(("update_available", {
                "tag": tag,
                "url": asset["browser_download_url"],
                "size": asset.get("size", 0),
                "notes": release.get("body", "")[:1200]
            }))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                message = "No published release was found. Check the repository name and publish a GitHub Release."
            else:
                message = f"GitHub returned HTTP {exc.code}."
            self.events.put(("update_error", message))
        except Exception as exc:
            self.events.put(("update_error", f"Update check failed: {exc}"))

    def _download_update(self, info):
        try:
            self.status_var.set(f"Downloading Stebbzor Pedal Manager {info['tag']}…")
            request = urllib.request.Request(info["url"], headers={"User-Agent": "Stebbzor-Pedal-Manager"})
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
            if len(payload) < 1000:
                raise RuntimeError("The downloaded update archive appears incomplete.")
            # Validate the archive and prevent ZIP path traversal.
            stage_root = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "StebbzorPedalManager" / "update-staging"
            if stage_root.exists():
                shutil.rmtree(stage_root, ignore_errors=True)
            stage_root.mkdir(parents=True, exist_ok=True)
            archive_path = stage_root / "update.zip"
            archive_path.write_bytes(payload)
            extract_dir = stage_root / "new"
            extract_dir.mkdir()
            with zipfile.ZipFile(archive_path, "r") as zf:
                root_resolved = extract_dir.resolve()
                for member in zf.infolist():
                    target = (extract_dir / member.filename).resolve()
                    if root_resolved not in target.parents and target != root_resolved:
                        raise RuntimeError("The update archive contains an unsafe file path.")
                zf.extractall(extract_dir)

            # Accept either a ZIP with the PyInstaller onedir contents at root,
            # or one containing a single StebbzorPedalManager directory.
            candidates = [extract_dir]
            sub = extract_dir / "StebbzorPedalManager"
            if sub.is_dir():
                candidates.insert(0, sub)
            source_dir = next((p for p in candidates if (p / "StebbzorPedalManager.exe").exists()), None)
            if source_dir is None:
                raise RuntimeError(
                    "The update ZIP must contain StebbzorPedalManager.exe and its PyInstaller support files."
                )
            if not getattr(sys, "frozen", False):
                message = f"Downloaded v{info['tag']}, but automatic replacement is available only in the compiled EXE. Build and install the release package on Windows."
                self.events.put(("update_info", message))
                return

            install_dir = Path(sys.executable).resolve().parent
            batch_path = stage_root / "apply_update.bat"
            # Wait for this app to exit, copy the new onedir contents over the old,
            # then relaunch the updated application.
            bat = [
                "@echo off",
                "setlocal",
                "timeout /t 2 /nobreak >nul",
                f'robocopy "{source_dir}" "{install_dir}" /E /R:3 /W:1 /NFL /NDL /NJH /NJS /NC /NS /NP',
                "if %ERRORLEVEL% GEQ 8 exit /b 1",
                f'start "" "{install_dir / "StebbzorPedalManager.exe"}"',
                f'rmdir /s /q "{stage_root}"',
                "del \"%~f0\""
            ]
            batch_path.write_text("\r\n".join(bat), encoding="utf-8")
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", str(batch_path)],
                cwd=str(stage_root), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            self.events.put(("update_info", f"Update {info['tag']} downloaded. The app will close, update, and restart."))
            self.after(1200, self.exit_app)
        except Exception as exc:
            self.events.put(("update_error", f"Could not install update: {exc}"))

    def refresh_games(self):
        for item in self.game_tree.get_children():
            self.game_tree.delete(item)
        for game in self.games:
            self.game_tree.insert("", "end", values=(game.get("process", ""), game.get("profile", "")))
        self.game_profile_combo["values"] = list(self.profiles.keys())
        if self.profiles and self.game_profile_var.get() not in self.profiles:
            self.game_profile_var.set(next(iter(self.profiles)))

    def add_game(self):
        process = self.process_var.get().strip()
        profile = self.game_profile_var.get().strip()
        if not process or not profile:
            messagebox.showwarning("Missing information", "Enter a process filename and choose a profile.")
            return
        process = Path(process).name.lower()
        if not process.endswith(".exe"):
            messagebox.showwarning("Process filename", "Use the game's executable filename ending in .exe.")
            return
        self.games = [g for g in self.games if g.get("process", "").lower() != process]
        self.games.append({"process": process, "profile": profile})
        self.save_game_settings()
        self.refresh_games()

    def remove_game(self):
        selected = self.game_tree.selection()
        if not selected:
            return
        process = self.game_tree.item(selected[0], "values")[0]
        self.games = [g for g in self.games if g.get("process") != process]
        if self.detected_process == process:
            self.detected_process = None
        self.save_game_settings()
        self.refresh_games()

    def save_game_settings(self):
        self.settings["games"] = self.games
        self.settings["auto_detect"] = bool(self.auto_detect_var.get())
        self.settings["auto_start"] = bool(self.auto_start_var.get())
        self.settings["minimize_to_tray"] = bool(self.minimize_to_tray_var.get())
        save_settings(self.settings)

    def toggle_tray_preference(self):
        self.save_game_settings()
        if self.auto_start_var.get():
            try:
                set_windows_startup(True, bool(self.minimize_to_tray_var.get()))
            except Exception as exc:
                messagebox.showerror("Startup setting update failed", str(exc))

    def toggle_windows_startup(self):
        try:
            set_windows_startup(bool(self.auto_start_var.get()), bool(self.minimize_to_tray_var.get()))
            self.save_game_settings()
            self.status_var.set("Windows startup enabled" if self.auto_start_var.get() else "Windows startup disabled")
        except Exception as exc:
            self.auto_start_var.set(not self.auto_start_var.get())
            messagebox.showerror("Startup setting failed", str(exc))

    def check_game_processes(self):
        try:
            if psutil and self.auto_detect_var.get() and self.games:
                wanted = {g.get("process", "").lower() for g in self.games}
                running = set()
                for proc in psutil.process_iter(["name"]):
                    try:
                        pname = (proc.info.get("name") or "").lower()
                        if pname in wanted:
                            running.add(pname)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                # Use the first matching configured game if more than one is open.
                match = next((g for g in self.games if g.get("process", "").lower() in running), None)
                if match and match["process"] != self.detected_process:
                    self.detected_process = match["process"]
                    if match["profile"] in self.profiles:
                        self.load_profile(match["profile"])
                        self.status_var.set(f"Game detected: {match['process']} — profile: {match['profile']}")
                elif not match and self.detected_process:
                    self.detected_process = None
                    if self.manual_profile in self.profiles:
                        self.load_profile(self.manual_profile)
                        self.status_var.set("Game closed — restored manual profile")
            elif (not self.auto_detect_var.get() or not self.games) and self.detected_process:
                self.detected_process = None
                if self.manual_profile in self.profiles:
                    self.load_profile(self.manual_profile)
        except Exception:
            pass
        self.after(5000, self.check_game_processes)
        if self.auto_check_updates_var.get():
            self.after(2500, self.check_for_updates)

    def _field(self, parent, label, var, width, values=None):
        box = ttk.Frame(parent, style="Panel.TFrame")
        box.pack(side="left", padx=(0, 12))
        ttk.Label(box, text=label, background="#1b1d20", foreground="#c9cdd1").pack(anchor="w")
        if values:
            w = ttk.Combobox(box, textvariable=var, values=values, width=width, state="readonly")
        else:
            w = ttk.Entry(box, textvariable=var, width=width)
        w.pack(anchor="w", pady=(5, 0))
        return w

    def _key_field(self, parent, label, var, target):
        box = ttk.Frame(parent, style="Panel.TFrame")
        box.pack(side="left", padx=(0, 14))
        ttk.Label(box, text=label, background="#1b1d20", foreground="#c9cdd1").pack(anchor="w")
        line = ttk.Frame(box, style="Panel.TFrame")
        line.pack(anchor="w", pady=(5, 0))
        entry = ttk.Entry(line, textvariable=var, width=15)
        entry.pack(side="left")
        ttk.Button(line, text="Capture", command=lambda: self.capture_key(target)).pack(side="left", padx=(5, 0))
        return entry

    def capture_key(self, target):
        if keyboard is None:
            messagebox.showerror("Keyboard capture unavailable",
                                 "The keyboard package is not installed. Run install.bat first.")
            return
        if getattr(self, "_capture_hook", None) is not None:
            return
        variable = self.input_var if target == "input" else self.output_var
        label = "pedal's current input" if target == "input" else "desired output"
        was_active = self.remapper.active
        if was_active:
            self.remapper.stop()
            self.start_btn.configure(text="▶  ENABLE REMAPPING")
        self.status_var.set(f"Listening for {label} — press one key now (Esc cancels).")
        self._capture_hook = None

        def on_key(event):
            if event.event_type != "down" or not event.name:
                return
            key_name = event.name.lower()
            if key_name in ("left windows", "right windows"):
                key_name = "windows"
            try:
                keyboard.unhook(self._capture_hook)
            except Exception:
                pass
            self._capture_hook = None
            def finish():
                if key_name == "esc":
                    self.status_var.set("Key capture cancelled")
                else:
                    variable.set(key_name)
                    self.status_var.set(f"Captured {label}: {key_name}")
                if was_active:
                    try:
                        self.remapper.set_mapping(self.rows)
                        self.remapper.start()
                        self.start_btn.configure(text="■  DISABLE REMAPPING")
                    except Exception as exc:
                        messagebox.showerror("Could not resume remapping", str(exc))
            self.after(0, finish)

        self._capture_hook = keyboard.hook(on_key, suppress=False)

    def set_status_threadsafe(self, message):
        self.events.put(message)

    def process_events(self):
        try:
            while True:
                item = self.events.get_nowait()
                if isinstance(item, tuple):
                    kind, payload = item
                    if kind == "update_info":
                        self.status_var.set(payload)
                        messagebox.showinfo("Stebbzor Pedal Manager", payload)
                    elif kind == "update_error":
                        self.status_var.set("Update check failed")
                        messagebox.showerror("Update checker", payload)
                    elif kind == "update_available":
                        self.status_var.set(f"Update available: {payload['tag']}")
                        answer = messagebox.askyesno(
                            "Update available",
                            f"Stebbzor Pedal Manager {payload['tag']} is available.\n"
                            f"You are running v{APP_VERSION}.\n\n"
                            "Download and install it now?"
                        )
                        if answer:
                            threading.Thread(target=self._download_update, args=(payload,), daemon=True).start()
                else:
                    self.status_var.set(item)
        except queue.Empty:
            pass
        self.after(100, self.process_events)

    def refresh_profile_names(self):
        names = list(self.profiles.keys())
        self.profile_combo["values"] = names
        self.profile_var.set(self.current_profile)
        if hasattr(self, "game_profile_combo"):
            self.game_profile_combo["values"] = names

    def load_profile(self, name):
        if name not in self.profiles:
            return
        self.current_profile = name
        self.profile_var.set(name)
        self.rows = [dict(x) for x in self.profiles[name].get("mappings", [])]
        self.refresh_tree()
        self.refresh_profile_names()
        self.remapper.set_mapping(self.rows)
        if self.remapper.active:
            self.remapper.stop()
            self.remapper.set_mapping(self.rows)
            self.remapper.start()

    def refresh_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in self.rows:
            self.tree.insert("", "end", values=(row["pedal"], row["input"], row["output"], row.get("mode", "Hold")))

    def on_profile_change(self, _=None):
        name = self.profile_var.get()
        if name != self.current_profile:
            if self.remapper.active:
                self.remapper.stop()
            if not self.detected_process:
                self.manual_profile = name
            self.load_profile(name)

    def add_mapping(self):
        pedal = self.pedal_var.get().strip()
        src = self.input_var.get().strip().lower()
        dst = self.output_var.get().strip().lower()
        mode = self.mode_var.get()
        if not src or not dst:
            messagebox.showwarning("Missing keys", "Enter both the current input key and the remapped key.")
            return
        if src == dst:
            messagebox.showwarning("Same key", "The input and output keys are identical, so no remapping is needed.")
            return
        if any(x["input"].lower() == src and x["pedal"] != pedal for x in self.rows):
            messagebox.showwarning("Duplicate input", "That input key is already assigned to another pedal.")
            return
        self.rows = [x for x in self.rows if x["pedal"] != pedal]
        self.rows.append({"pedal": pedal, "input": src, "output": dst, "mode": mode})
        self.refresh_tree()
        was_active = self.remapper.active
        if was_active:
            self.remapper.stop()
        self.remapper.set_mapping(self.rows)
        if was_active:
            self.remapper.start()
            self.start_btn.configure(text="■  DISABLE REMAPPING")

    def remove_mapping(self):
        selected = self.tree.selection()
        if not selected:
            return
        values = self.tree.item(selected[0], "values")
        self.rows = [x for x in self.rows if not (x["pedal"] == values[0] and x["input"] == values[1])]
        self.refresh_tree()
        was_active = self.remapper.active
        if was_active:
            self.remapper.stop()
        self.remapper.set_mapping(self.rows)
        if was_active:
            self.remapper.start()
            self.start_btn.configure(text="■  DISABLE REMAPPING")

    def clear_editor(self):
        self.input_var.set("")
        self.output_var.set("")
        self.mode_var.set("Hold")

    def save_current(self):
        self.profiles[self.current_profile] = {"name": self.current_profile, "mappings": self.rows}
        save_profiles(self.profiles)
        self.status_var.set(f"Saved profile: {self.current_profile}")

    def new_profile(self):
        name = simpledialog.askstring("New profile", "Enter a profile name:", parent=self)
        if not name:
            return
        name = name.strip()
        if not name or name in self.profiles:
            messagebox.showwarning("Profile name", "Enter a unique, non-empty profile name.")
            return
        self.profiles[name] = {"name": name, "mappings": []}
        self.save_current()
        self.load_profile(name)
        self.save_current()

    def duplicate_profile(self):
        name = simpledialog.askstring("Duplicate profile", "Name for the copy:", parent=self)
        if not name:
            return
        name = name.strip()
        if not name or name in self.profiles:
            messagebox.showwarning("Profile name", "Enter a unique, non-empty profile name.")
            return
        self.profiles[name] = {"name": name, "mappings": [dict(x) for x in self.rows]}
        self.load_profile(name)
        self.save_current()

    def rename_profile(self):
        name = simpledialog.askstring("Rename profile", "New profile name:", initialvalue=self.current_profile, parent=self)
        if not name:
            return
        name = name.strip()
        if not name or name in self.profiles:
            messagebox.showwarning("Profile name", "Enter a unique, unused profile name.")
            return
        self.profiles[name] = {"name": name, "mappings": [dict(x) for x in self.rows]}
        del self.profiles[self.current_profile]
        self.current_profile = name
        self.save_current()
        self.refresh_profile_names()

    def delete_profile(self):
        if len(self.profiles) <= 1:
            messagebox.showwarning("Cannot delete", "You must keep at least one profile.")
            return
        if messagebox.askyesno("Delete profile", f"Delete '{self.current_profile}'?"):
            del self.profiles[self.current_profile]
            self.current_profile = next(iter(self.profiles))
            save_profiles(self.profiles)
            self.load_profile(self.current_profile)

    def toggle_remap(self):
        if self.remapper.active:
            self.remapper.stop()
            self.start_btn.configure(text="▶  ENABLE REMAPPING")
        else:
            try:
                self.save_current()
                self.remapper.set_mapping(self.rows)
                self.remapper.start()
                self.start_btn.configure(text="■  DISABLE REMAPPING")
            except Exception as exc:
                self.remapper.stop()
                messagebox.showerror("Could not enable remapping",
                                     f"{exc}\n\nTry closing other key-remapping tools and running this app as Administrator.")

    def open_profiles_folder(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(APP_DIR))

    def _make_tray_image(self):
        image = Image.new("RGBA", (64, 64), (21, 23, 27, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((5, 5, 59, 59), radius=12, fill=(42, 45, 49, 255), outline=(180, 184, 188, 255), width=3)
        draw.text((15, 19), "SP", fill=(240, 242, 244, 255))
        return image

    def _ensure_tray_icon(self):
        if self.tray_icon is not None:
            return True
        if pystray is None or Image is None:
            messagebox.showwarning("System tray unavailable",
                "The system-tray feature needs pystray and Pillow. Re-run install.bat or use the packaged build.")
            return False
        menu = pystray.Menu(
            pystray.MenuItem("Open Stebbzor Pedal Manager", self._tray_show, default=True),
            pystray.MenuItem("Exit", self._tray_exit)
        )
        self.tray_icon = pystray.Icon("StebbzorPedalManager", self._make_tray_image(), APP_NAME, menu)
        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()
        return True

    def _tray_show(self, icon=None, item=None):
        self.after(0, self._restore_from_tray)

    def _tray_exit(self, icon=None, item=None):
        self.after(0, self.exit_app)

    def _restore_from_tray(self):
        self.deiconify()
        self.state("normal")
        self.lift()

    def minimize_to_tray(self):
        if self._ensure_tray_icon():
            self.withdraw()
            self.status_var.set("Running in the system tray")

    def on_window_minimize(self, event=None):
        if self.state() == "iconic" and self.minimize_to_tray_var.get():
            self.after(150, self.minimize_to_tray)

    def on_window_close(self):
        if self.minimize_to_tray_var.get():
            self.minimize_to_tray()
        else:
            self.exit_app()

    def exit_app(self):
        self.allow_exit = True
        self.remapper.stop()
        self.save_game_settings()
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        self.destroy()

    def close(self):
        self.exit_app()

if __name__ == "__main__":
    app = App()
    app.mainloop()
