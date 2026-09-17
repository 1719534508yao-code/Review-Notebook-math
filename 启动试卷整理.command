#!/bin/bash
# 双击本文件即可启动「试卷错题整理」。
# 双击后会自动打开一个终端窗口，跑完会自己弹出浏览器。
# 停止服务：在这个窗口里按 Ctrl+C，或直接关掉窗口。
cd "$(dirname "$0")" || exit 1
export PAUSE_ON_ERROR=1
exec ./run.sh
