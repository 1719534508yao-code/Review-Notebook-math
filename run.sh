#!/bin/bash
# 试卷错题整理 —— 一键启动（macOS / Linux 通用）
#
# 用法一（推荐）：在 Finder 里双击「启动试卷整理.command」
# 用法二：终端里执行 ./run.sh
#
# 首次运行会自动创建虚拟环境并装依赖（约 1~2 分钟），之后启动只要几秒。
# 启动完成后自动打开浏览器；停止服务：在本窗口按 Ctrl+C。
set -u
cd "$(dirname "$0")"

VENV=".venv"
PORT_PREF="${PORT:-8000}"
# 由「启动试卷整理.command」设为 1：出错时停住窗口，方便看清原因
PAUSE_ON_ERROR="${PAUSE_ON_ERROR:-0}"

cyan() { printf '\033[36m%s\033[0m\n' "$1"; }
warn() { printf '\033[33m%s\033[0m\n' "$1"; }
err()  { printf '\033[31m%s\033[0m\n' "$1" >&2; }

# 失败退出：双击启动时停住窗口，否则错误一闪而过看不到
bye() {
  echo
  if [ "$PAUSE_ON_ERROR" = "1" ]; then
    read -n 1 -s -r -p "按任意键关闭窗口…" || true
    echo
  fi
  exit "${1:-1}"
}

open_url() {
  if command -v open >/dev/null 2>&1; then
    open "$1" >/dev/null 2>&1
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$1" >/dev/null 2>&1
  fi
}

echo
cyan "════ 试卷错题整理 · 启动器 ════"
echo

# ── 1. 找到 Python 3 ────────────────────────────────────────────────
PY_BIN="$(command -v python3 || true)"
if [ -z "$PY_BIN" ]; then
  err "✗ 没有找到 python3。"
  echo "  请先安装 Python 3：https://www.python.org/downloads/"
  echo "  或者安装 Xcode 命令行工具后重试：xcode-select --install"
  bye 1
fi

# ── 2. 虚拟环境（首次自动创建）──────────────────────────────────────
if [ ! -x "$VENV/bin/python" ]; then
  cyan "▸ 首次运行：创建虚拟环境（$("$PY_BIN" --version 2>&1)）…"
  if ! "$PY_BIN" -m venv "$VENV"; then
    err "✗ 创建虚拟环境失败。"
    bye 1
  fi
fi
PY="$VENV/bin/python"

# ── 3. 依赖（缺了才装）──────────────────────────────────────────────
if ! "$PY" -c "import uvicorn, fastapi, fitz, openai, dotenv, PIL, multipart" >/dev/null 2>&1; then
  cyan "▸ 首次运行：安装依赖，约 1~2 分钟，请耐心等待…"
  "$PY" -m pip install --upgrade pip -q
  if ! "$PY" -m pip install -r requirements.txt -q; then
    err "✗ 依赖安装失败（多数是网络问题）。"
    echo "  可以重试，或改用国内镜像："
    echo "  $VENV/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
    bye 1
  fi
  cyan "▸ 依赖安装完成。"
fi

# ── 4. 已经在运行？那就只打开浏览器，不重复启动 ──────────────────────
if curl -fsS --max-time 1 "http://127.0.0.1:${PORT_PREF}/api/health" >/dev/null 2>&1; then
  cyan "▸ 服务已经在运行，直接打开浏览器。"
  open_url "http://127.0.0.1:${PORT_PREF}"
  echo "  地址：http://127.0.0.1:${PORT_PREF}"
  echo "  想重启的话：先关闭原来那个启动窗口，再双击本文件。"
  bye 0
fi

# ── 5. 选一个空闲端口（8000 被别的程序占了就往后找）──────────────────
PORT="$("$PY" - "$PORT_PREF" <<'PY'
import socket, sys
start = int(sys.argv[1])
for p in range(start, start + 20):
    s = socket.socket()
    # SO_REUSEADDR：刚关掉上一个实例时端口常处于 TIME_WAIT，没有它会误判「被占用」
    # 而顺延到 8001（地址变了，用户以为启动失败）。注意它不会让「已有服务在监听」被误判为空闲。
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", p))
    except OSError:
        continue
    finally:
        s.close()
    print(p)
    break
PY
)"
if [ -z "${PORT:-}" ]; then
  err "✗ ${PORT_PREF}~$((PORT_PREF + 19)) 端口都被占用了，请先关掉占用的程序。"
  bye 1
fi
[ "$PORT" != "$PORT_PREF" ] && warn "▸ ${PORT_PREF} 被占用，改用端口 ${PORT}。"

URL="http://127.0.0.1:${PORT}"
cyan "▸ 服务地址：${URL}"
echo "  浏览器会自动打开；若没弹出，手动复制上面地址。"
echo "  停止服务：在本窗口按 Ctrl+C。"
echo

# ── 6. 后台等就绪后自动开浏览器（不是死等固定秒数）────────────────────
(
  for _ in $(seq 1 120); do
    if curl -fsS --max-time 1 "${URL}/api/health" >/dev/null 2>&1; then
      open_url "$URL"
      exit 0
    fi
    sleep 0.5
  done
) &

# ── 7. 前台启动服务（Ctrl+C 即停止）─────────────────────────────────
"$PY" -m uvicorn app:app --host 127.0.0.1 --port "$PORT"
STATUS=$?

echo
if [ "$STATUS" -eq 0 ] || [ "$STATUS" -eq 130 ]; then
  cyan "▸ 服务已停止。"
  bye 0
fi
err "✗ 服务异常退出（退出码 $STATUS）。上面的报错信息可以帮助定位问题。"
bye "$STATUS"
