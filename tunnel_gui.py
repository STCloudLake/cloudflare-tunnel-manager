"""
Cloudflare Tunnel Manager - 图形化管理工具
Python 3.8+, pystray + Pillow 用于系统托盘
双击运行，关闭按钮最小化到托盘
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path

from PIL import Image
import pystray

# ── 路径 ──────────────────────────────────────────
HERE = Path(__file__).resolve().parent if "__file__" in dir() else Path.cwd()
ICON_FILE = HERE / "cloudflare.ico"
CONFIG_DIR = Path.home() / ".cloudflared"
CONFIG_FILE = CONFIG_DIR / "config.yml"


# ── cloudflared 辅助 ──────────────────────────────
def find_cloudflared() -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found
    candidates = [
        Path.home() / "scoop" / "shims" / "cloudflared.exe",
        Path("C:/Program Files (x86)/cloudflared/cloudflared.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "cloudflared" / "cloudflared.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


CLOUDFLARED = find_cloudflared() or "cloudflared"


def run_cmd(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return -1, "", "cloudflared 未安装或不在 PATH 中"
    except subprocess.TimeoutExpired:
        return -1, "", "命令超时"
    except Exception as e:
        return -1, "", str(e)


def list_tunnels() -> list[dict]:
    code, out, _ = run_cmd([CLOUDFLARED, "tunnel", "list", "--output", "json"])
    if code != 0:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


# ── YAML 解析 (缩进感知) ─────────────────────────
def read_config_yaml() -> dict | None:
    if not CONFIG_FILE.exists():
        return None

    text = CONFIG_FILE.read_text(encoding="utf-8")
    result: dict = {}
    ingress_list: list[dict] = []
    current_ingress: dict = {}
    pending_name = ""

    for raw in text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("#"):
            if not stripped.startswith("##"):
                pending_name = stripped.lstrip("#").strip()
            continue

        # 用 raw line 计算缩进，不能用 stripped
        indent = len(raw.rstrip("\n")) - len(raw.lstrip("\n").lstrip())

        if stripped.startswith("- "):
            if current_ingress and "hostname" in current_ingress and "service" in current_ingress:
                if pending_name:
                    current_ingress["name"] = pending_name
                    pending_name = ""
                ingress_list.append(dict(current_ingress))
            current_ingress = {}
            content = stripped[2:].strip()
            key, _, val = content.partition(":")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if val:
                current_ingress[key] = val
        elif indent == 0 and ":" in stripped:
            key, _, val = stripped.partition(":")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if val:
                result[key] = val
        elif indent == 4 and ":" in stripped and current_ingress is not None:
            key, _, val = stripped.partition(":")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if val:
                current_ingress[key] = val

    if current_ingress and "hostname" in current_ingress and "service" in current_ingress:
        if pending_name:
            current_ingress["name"] = pending_name
        ingress_list.append(dict(current_ingress))

    if ingress_list:
        result["ingress"] = ingress_list

    return result if result.get("tunnel") or result.get("ingress") else None


# ── GUI 主窗口 ─────────────────────────────────────
class TunnelManager:
    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.tunnel_id = ""
        self.ingress: list[dict] = []
        self.tray: pystray.Icon | None = None

        self._build_root()
        self.tunnel_name = tk.StringVar()
        self._build_ui()
        self._refresh_tunnels()
        self._load_from_config(silent=True)
        self.root.after(500, self._create_tray)

    # ── 根窗口 ───────────────────────────────────
    def _build_root(self):
        self.root = tk.Tk()
        self.root.title("Cloudflare Tunnel Manager")
        self.root.geometry("640x580")
        self.root.minsize(540, 440)

        # 加载图标
        if ICON_FILE.exists():
            self.root.iconbitmap(str(ICON_FILE))

        # 关闭按钮 → 最小化到托盘
        self.root.protocol("WM_DELETE_WINDOW", self._minimize_to_tray)

    # ── UI ────────────────────────────────────────
    def _build_ui(self):
        # ── 顶部：隧道选择 + 操作 + 状态 ──
        top = ttk.Frame(self.root, padding=(10, 10, 10, 5))
        top.pack(fill=tk.X)
        ttk.Label(top, text="隧道:").pack(side=tk.LEFT)
        self.tunnel_combo = ttk.Combobox(top, textvariable=self.tunnel_name, state="readonly", width=20)
        self.tunnel_combo.pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="刷新", command=self._refresh_tunnels).pack(side=tk.LEFT, padx=1)
        self.btn_start = ttk.Button(top, text="启动", command=self._start_tunnel)
        self.btn_start.pack(side=tk.LEFT, padx=1)
        self.btn_stop = ttk.Button(top, text="停止", command=self._stop_tunnel, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=1)
        ttk.Button(top, text="保存", command=self._save_config).pack(side=tk.LEFT, padx=1)
        self.status_label = ttk.Label(top, text="● 未运行", foreground="#888")
        self.status_label.pack(side=tk.RIGHT, padx=5)

        # ── 映射列表 ──
        mid = ttk.LabelFrame(self.root, text="端口映射", padding=(10, 10, 10, 5))
        mid.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 0))

        cols = ("#", "名称", "域名 (Hostname)", "本地服务 (Service)")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", height=8)
        self.tree.heading("#", text="#")
        self.tree.heading("名称", text="名称")
        self.tree.heading("域名 (Hostname)", text="域名 (Hostname)")
        self.tree.heading("本地服务 (Service)", text="本地服务 (Service)")
        self.tree.column("#", width=30, anchor=tk.CENTER)
        self.tree.column("名称", width=75)
        self.tree.column("域名 (Hostname)", width=175)
        self.tree.column("本地服务 (Service)", width=175)
        self.tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        scroll = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=self.tree.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Delete>", lambda e: self._remove_mapping())

        btn_row = ttk.Frame(self.root)
        btn_row.pack(fill=tk.X, padx=10, pady=(3, 5))
        ttk.Button(btn_row, text="删除", command=self._remove_mapping).pack(side=tk.LEFT, padx=1)
        ttk.Button(btn_row, text="+", command=self._open_add_dialog).pack(side=tk.LEFT, padx=1)
        ttk.Button(btn_row, text="加载", command=self._load_from_config).pack(side=tk.RIGHT, padx=1)
        ttk.Button(btn_row, text="DNS", command=self._route_dns).pack(side=tk.RIGHT, padx=1)

        # ── 日志 ──
        log_frame = ttk.LabelFrame(self.root, text="运行日志", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        log_toolbar = ttk.Frame(log_frame)
        log_toolbar.pack(fill=tk.X, pady=(0, 3))
        ttk.Button(log_toolbar, text="清空", command=self._clear_log).pack(side=tk.RIGHT)

        self.log_text = tk.Text(log_frame, height=6, state=tk.DISABLED, font=("Consolas", 9),
                                bg="#1e1e1e", fg="#d4d4d4", insertbackground="white")
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # ── 数据操作 ─────────────────────────────────
    def _refresh_tunnels(self):
        tunnels = list_tunnels()
        names = [t.get("name", "") for t in tunnels]
        self.tunnel_combo["values"] = names
        if names and not self.tunnel_name.get():
            self.tunnel_name.set(names[0])
            self.tunnel_id = tunnels[0].get("id", "")
        self.tunnel_combo.bind("<<ComboboxSelected>>", lambda e: self._on_tunnel_selected())

    def _on_tunnel_selected(self):
        name = self.tunnel_name.get()
        for t in list_tunnels():
            if t.get("name") == name:
                self.tunnel_id = t.get("id", "")
                break

    def _refresh_tree(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for i, m in enumerate(self.ingress, 1):
            self.tree.insert("", tk.END, values=(i, m.get("name", ""), m["hostname"], m["service"]))

    def _open_add_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("添加/编辑映射")
        dlg.geometry("440x220")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        ttk.Label(dlg, text="名称 (选填):").pack(pady=(15, 0))
        e_name = ttk.Entry(dlg, width=45)
        e_name.pack(pady=3)
        ttk.Label(dlg, text="域名 (Hostname):").pack()
        e_host = ttk.Entry(dlg, width=45)
        e_host.pack(pady=3)
        ttk.Label(dlg, text="本地服务 (如 http://localhost:8080):").pack()
        e_svc = ttk.Entry(dlg, width=45)
        e_svc.pack(pady=3)
        e_svc.insert(0, "http://localhost:8080")

        def do_add():
            n, h, s = e_name.get().strip(), e_host.get().strip(), e_svc.get().strip()
            if not h or not s:
                messagebox.showwarning("提示", "域名和服务为必填项")
                return
            for m in self.ingress:
                if m["hostname"] == h:
                    messagebox.showwarning("提示", f"域名 {h} 已存在")
                    return
            self.ingress.append({"name": n, "hostname": h, "service": s})
            self._refresh_tree()
            lbl = f" ({n})" if n else ""
            self._log(f"[添加] {h}{lbl} → {s}")
            dlg.destroy()

        ttk.Button(dlg, text="添加", command=do_add).pack(pady=10)

    def _remove_mapping(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(self.tree.item(sel[0], "values")[0]) - 1
        removed = self.ingress.pop(idx)
        label = f" ({removed.get('name')})" if removed.get("name") else ""
        self._log(f"[删除] {removed['hostname']}{label}")
        self._refresh_tree()

    def _route_dns(self):
        if not self.tunnel_name.get():
            messagebox.showwarning("提示", "请先选择一个隧道")
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("绑定 DNS 路由")
        dlg.geometry("420x130")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        ttk.Label(dlg, text="子域名 (Hostname):").pack(pady=(15, 0))
        entry = ttk.Entry(dlg, width=45)
        entry.pack(pady=5)

        def do_route():
            hostname = entry.get().strip()
            if not hostname:
                return
            code, out, err = run_cmd([CLOUDFLARED, "tunnel", "route", "dns", self.tunnel_name.get(), hostname])
            if code == 0:
                self._log(f"[DNS] {hostname} → 已绑定")
                messagebox.showinfo("成功", f"DNS 路由已创建:\n{hostname}")
            else:
                self._log(f"[DNS] 失败: {err}")
                messagebox.showerror("失败", err)
            dlg.destroy()

        ttk.Button(dlg, text="创建 DNS 记录", command=do_route).pack(pady=10)

    def _load_from_config(self, silent: bool = False):
        config = read_config_yaml()
        if not config:
            if not silent:
                messagebox.showinfo("提示", "未找到现有配置文件")
            return
        if config.get("tunnel"):
            self.tunnel_name.set(config["tunnel"])
        ingress_data = config.get("ingress")
        if ingress_data:
            filtered = []
            for item in ingress_data:
                if item.get("hostname") and item.get("service"):
                    if item["service"] != "http_status:404":
                        filtered.append({
                            "name": item.get("name", ""),
                            "hostname": item["hostname"],
                            "service": item["service"],
                        })
            if filtered:
                self.ingress = filtered
                self._refresh_tree()
                self._log(f"[配置] 已加载 {len(filtered)} 条映射")
        if not silent:
            messagebox.showinfo("提示", f"已加载 {len(self.ingress)} 条映射")

    # ── 隧道控制 ─────────────────────────────────
    def _start_tunnel(self):
        if self.process and self.process.poll() is None:
            messagebox.showinfo("提示", "隧道已在运行中")
            return
        if not self.tunnel_name.get():
            messagebox.showwarning("提示", "请先选择一个隧道")
            return

        self._save_config(silent=True)

        cmd = [CLOUDFLARED, "tunnel", "run", self.tunnel_name.get()]
        try:
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except FileNotFoundError:
            messagebox.showerror("错误", "找不到 cloudflared")
            return

        self._set_running(True)
        self._log("[系统] 隧道启动中...")
        threading.Thread(target=self._read_output, daemon=True).start()

    def _stop_tunnel(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
        self._set_running(False)
        self._log("[系统] 隧道已停止")

    def _read_output(self):
        if not self.process or not self.process.stdout:
            return
        for line in iter(self.process.stdout.readline, ""):
            if line:
                self._log(line.rstrip())
            if self.process is None or self.process.poll() is not None:
                break
        self.root.after(0, lambda: self._set_running(False))

    def _set_running(self, running: bool):
        if running:
            self.status_label.config(text="● 运行中", foreground="#4caf50")
            self.btn_start.config(state=tk.DISABLED)
            self.btn_stop.config(state=tk.NORMAL)
            if self.tray:
                self.tray.title = f"Cloudflare Tunnel - 运行中 ({self.tunnel_name.get()})"
        else:
            self.status_label.config(text="● 未运行", foreground="#888")
            self.btn_start.config(state=tk.NORMAL)
            self.btn_stop.config(state=tk.DISABLED)
            if self.tray:
                self.tray.title = f"Cloudflare Tunnel - 未运行 ({self.tunnel_name.get()})"

    # ── 配置保存 ─────────────────────────────────
    def _save_config(self, silent: bool = False):
        tunnel = self.tunnel_name.get()
        if not tunnel:
            if not silent:
                messagebox.showwarning("提示", "请先选择一个隧道")
            return

        cred_file = CONFIG_DIR / f"{self.tunnel_id}.json"
        if not cred_file.exists():
            jsons = list(CONFIG_DIR.glob("*.json"))
            if jsons:
                cred_file = jsons[0]

        lines = [
            f"tunnel: {tunnel}",
            f"credentials-file: {str(cred_file)}",
            "",
            "ingress:",
        ]
        for m in self.ingress:
            if m.get("name"):
                lines.append(f"  # {m['name']}")
            lines.append(f"  - hostname: {m['hostname']}")
            lines.append(f"    service: {m['service']}")
        lines.append("  - service: http_status:404")
        lines.append("")

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text("\n".join(lines), encoding="utf-8")
        self._log(f"[配置] 已保存 {len(self.ingress)} 条映射")
        if not silent:
            messagebox.showinfo("提示", "配置已保存")

    # ── 日志 ─────────────────────────────────────
    def _log(self, msg: str):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)

    # ── 托盘 ─────────────────────────────────────
    def _minimize_to_tray(self):
        self.root.withdraw()

    def _create_tray(self):
        if not ICON_FILE.exists():
            # 无图标时生成一个简单的 64x64 蓝底图标
            img = Image.new("RGB", (64, 64), "#f38020")
            self.tray = pystray.Icon("cf-tunnel", img, "Cloudflare Tunnel Manager")
        else:
            img = Image.open(str(ICON_FILE))
            self.tray = pystray.Icon("cf-tunnel", img, "Cloudflare Tunnel Manager")
        self.tray.menu = pystray.Menu(
            pystray.MenuItem("显示窗口", self._show_window, default=True),
            pystray.MenuItem("启动隧道", self._start_tunnel),
            pystray.MenuItem("停止隧道", self._stop_tunnel),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit_app),
        )
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _show_window(self):
        self.root.after(0, self.root.deiconify)

    def _quit_app(self):
        if self.process and self.process.poll() is None:
            if messagebox.askyesno("确认", "隧道正在运行，退出会停止隧道。确定退出?"):
                self._stop_tunnel()
            else:
                return
        if self.tray:
            self.tray.stop()
            self.tray = None
        self.root.after(0, self.root.destroy)

    def run(self):
        self.root.mainloop()


# ── 入口 ──────────────────────────────────────────
if __name__ == "__main__":
    code, _, err = run_cmd([CLOUDFLARED, "--version"])
    if code != 0:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("错误", f"找不到 cloudflared\n\n{err}")
        sys.exit(1)

    app = TunnelManager()
    app.run()
