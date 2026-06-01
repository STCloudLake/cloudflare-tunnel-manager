# Cloudflare Tunnel Manager

Windows 系统托盘工具，用于管理 Cloudflare Tunnel。

## 功能

- 图形化管理 Cloudflare 隧道
- 添加/删除端口映射（ingress rules）
- 一键启动 / 停止隧道
- DNS 路由绑定
- 系统托盘最小化运行
- 运行日志实时查看

## 依赖

- Python 3.8+
- [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
- 已授权并创建的 Cloudflare Tunnel

## 安装

```bash
pip install pystray Pillow
```

确保 `cloudflared` 已安装并在 PATH 中。

## 使用

```bash
python tunnel_gui.py
```

或双击 `启动隧道管理器.bat`。

关闭窗口会最小化到系统托盘，右键托盘图标可退出程序。

## 编译为 EXE

```bash
build_exe.bat
```

编译后的可执行文件位于 `dist/CloudflareTunnelManager.exe`。

## 配置文件

程序自动管理 `~/.cloudflared/config.yml`，保存隧道和端口映射配置。
