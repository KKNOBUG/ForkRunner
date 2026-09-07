#!/bin/bash
# -*- coding: utf-8 -*-
#
# Celery Worker / Beat 部署脚本
# 完全贴合手动部署流程:
#   cd /zdhgj/python_projects/fastapi-toolbox-runner
#   source .venv/bin/activate
#   nohup celery -A celery_scheduler.celery_worker worker -Q 8518_default,8518_autotest -c 4 -l INFO > output/logs/celery_log/celery_worker.log 2>&1 &
#   nohup celery -A celery_scheduler.celery_worker beat -l INFO > output/logs/celery_log/celery_beat.log 2>&1 &
#   pkill -f -9 celery
# 用法: ./celery_deploy.sh start|stop|restart|status|start-worker|stop-worker|start-beat|stop-beat

# ==================== 基础路径 ====================
# 部署约定: 本脚本与 .venv 同置于项目根目录, 任意工作目录调用均可正确定位
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
VENV_DIR="${PROJECT_ROOT}/.venv"
CELERY_BIN="${VENV_DIR}/bin/celery"

# 激活虚拟环境(启动命令已用绝对路径双保险, 未创建 .venv 时不报错)
if [ -f "${VENV_DIR}/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"
fi

CELERY_APP="celery_scheduler.celery_worker"
# 并发数: 默认 4, 可用环境变量 CELERY_CONCURRENCY 或命令行第二参数覆盖
CONCURRENCY="${CELERY_CONCURRENCY:-4}"

cd "$PROJECT_ROOT" || { echo "无法进入项目目录: $PROJECT_ROOT"; exit 1; }
# 保证 celery 能导入项目模块
export PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

# ==================== 队列名(固定写死, 最稳定) ====================
# 必须与 configure/celery_config.py 的 CeleryConfig 保持一致(端口前缀隔离: {port}_default,{port}_autotest, 当前端口 8518)
# 注意: .env 中无 SERVER_PORT 变量, 全局配置来源为 CeleryConfig.default_queue / CeleryConfig.autotest_queue,
#       若服务端口变更, 此处必须与 CeleryConfig 同步修改
QUEUES="8518_default,8518_autotest"

# ==================== 日志路径 ====================
# nohup 重定向日志(与手动部署路径一致); 同时通过 --logfile 传给 setup_logging 挂 Loguru 文件 sink
CELERY_LOG_DIR="${PROJECT_ROOT}/output/logs/celery_log"
mkdir -p "$CELERY_LOG_DIR"
CELERY_WORKER_LOG="${CELERY_LOG_DIR}/celery_worker.log"
CELERY_BEAT_LOG="${CELERY_LOG_DIR}/celery_beat.log"
# 项目内 Loguru 默认日志目录(configure.celery_config 定义), 一并列出便于排查
CELERY_LOGURU_LOG_DIR="${PROJECT_ROOT}/output/logs/celery_logs"

# 进程匹配模式(与手动 pkill -f 的匹配对象一致, 且能区分 worker/beat)
WORKER_PATTERN='celery_scheduler\.celery_worker[[:space:]]+worker'
BEAT_PATTERN='celery_scheduler\.celery_worker[[:space:]]+beat'
CELERY_PATTERN='celery'

# ==================== 输出函数 ====================
print_info()  { echo -e "\033[32m[INFO]\033[0m $1"; }
print_warn()  { echo -e "\033[33m[WARN]\033[0m $1"; }
print_error() { echo -e "\033[31m[ERROR]\033[0m $1"; }
print_step()  { echo -e "\n\033[36m========== $1 ==========\033[0m"; }

# ==================== 工具函数 ====================

# 查找匹配进程 PID(排除脚本自身, 避免 celery_deploy.sh 自身被 pkill celery 误杀)
pids_of() {
    pgrep -f "$1" 2> /dev/null | grep -vw "$$" || true
}

is_running() {
    [ -n "$(pids_of "$1")" ]
}

format_pids() {
    echo "$(pids_of "$1")" | tr '\n' ' ' | sed 's/ $//'
}

# 停止进程: 先 TERM 温和终止(允许在途任务收尾), 超时后 kill -9 强杀(等价手动 pkill -f -9 的最终效果)
kill_by_pattern() {
    local pattern="$1"
    local name="$2"
    local timeout_s="${3:-10}"
    local pids pid waited

    pids="$(pids_of "$pattern")"
    if [ -z "$pids" ]; then
        print_info "${name} 未运行(跳过)..."
        return 0
    fi

    print_info "停止 ${name}(PID: $(format_pids "$pattern"))..."
    for pid in $pids; do
        kill -TERM "$pid" 2> /dev/null || true
    done

    waited=0
    while [ "$waited" -lt "$timeout_s" ] && is_running "$pattern"; do
        sleep 1
        waited=$((waited + 1))
    done

    # 强杀第一轮: 每次都基于实时进程表重新扫描(不依赖 pid 文件, 不会因 pid 过期而杀空/杀错)
    pids="$(pids_of "$pattern")"
    if [ -n "$pids" ]; then
        print_warn "${name} ${timeout_s}s 内未退出, 强制 kill -9..."
        for pid in $pids; do
            kill -9 "$pid" 2> /dev/null || true
        done
        sleep 1
    fi

    # 第二轮兑底: 重新扫描补杀
    pids="$(pids_of "$pattern")"
    if [ -n "$pids" ]; then
        for pid in $pids; do
            kill -9 "$pid" 2> /dev/null || true
        done
        sleep 1
    fi

    if is_running "$pattern"; then
        print_error "${name} 停止失败, 存活进程: $(format_pids "$pattern")"
        return 1
    fi
    print_info "${name} 已停止..."
    return 0
}

# 等待进程稳定运行(前 8 秒为重度 import 阶段, 连续 3 秒存在才判定成功)
wait_alive() {
    local pattern="$1"
    local log_file="$2"
    local name="$3"
    local stable=0 elapsed=0

    while [ "$elapsed" -lt 20 ]; do
        if is_running "$pattern"; then
            stable=$((stable + 1))
            if [ "$stable" -ge 3 ] && [ "$elapsed" -ge 8 ]; then
                print_info "${name} 启动成功 (PID: $(format_pids "$pattern"))"
                print_info "日志文件: $log_file"
                return 0
            fi
        else
            stable=0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done

    print_error "${name} 启动失败或超时, 请查看日志: $log_file"
    if [ -f "$log_file" ]; then
        print_error "----- 最近日志 -----"
        tail -n 30 "$log_file" 2> /dev/null || true
    fi
    return 1
}

# ==================== 服务控制 ====================
start_celery_worker() {
    if is_running "$WORKER_PATTERN"; then
        print_warn "Celery Worker 已在运行(PID: $(format_pids "$WORKER_PATTERN")), 跳过启动"
        return 0
    fi
    if [ ! -x "$CELERY_BIN" ]; then
        print_error "celery 不存在: $CELERY_BIN, 请先在项目根目录创建虚拟环境(.venv)"
        return 1
    fi

    print_info "启动 Celery Worker (并发数: ${CONCURRENCY}, 队列: ${QUEUES})..."
    print_info "日志文件: ${CELERY_WORKER_LOG}"
    # 导出 CELERY_LOGFILE: prefork 子进程(worker_process_init)重建 Loguru sink 时写同一文件
    export CELERY_LOGFILE="${CELERY_WORKER_LOG}"
    nohup "$CELERY_BIN" -A "$CELERY_APP" worker \
        -Q "$QUEUES" -c "$CONCURRENCY" -l INFO \
        --logfile="$CELERY_WORKER_LOG" \
        > "$CELERY_WORKER_LOG" 2>&1 &

    wait_alive "$WORKER_PATTERN" "$CELERY_WORKER_LOG" "Celery Worker"
}

start_celery_beat() {
    if is_running "$BEAT_PATTERN"; then
        print_warn "Celery Beat 已在运行(PID: $(format_pids "$BEAT_PATTERN")), 跳过启动"
        return 0
    fi
    if [ ! -x "$CELERY_BIN" ]; then
        print_error "celery 不存在: $CELERY_BIN, 请先在项目根目录创建虚拟环境(.venv)"
        return 1
    fi

    print_info "启动 Celery Beat..."
    print_info "日志文件: ${CELERY_BEAT_LOG}"
    export CELERY_LOGFILE="${CELERY_BEAT_LOG}"
    # 调度器(redbeat)由 celery_config.py 配置提供, 不额外传参
    nohup "$CELERY_BIN" -A "$CELERY_APP" beat \
        -l INFO \
        --logfile="$CELERY_BEAT_LOG" \
        > "$CELERY_BEAT_LOG" 2>&1 &

    wait_alive "$BEAT_PATTERN" "$CELERY_BEAT_LOG" "Celery Beat"
}

stop_celery_worker() {
    kill_by_pattern "$WORKER_PATTERN" "Celery Worker" 15
}

stop_celery_beat() {
    kill_by_pattern "$BEAT_PATTERN" "Celery Beat" 10
}

stop_celery_all() {
    stop_celery_beat
    stop_celery_worker
    # 清扫残留 celery 进程(脚本自身已排除, 不会被误杀)
    kill_by_pattern "$CELERY_PATTERN" "Celery 残留进程" 3 || true
}

celery_status() {
    print_step "Celery 进程状态"
    echo "项目目录: $PROJECT_ROOT"
    echo "Celery:   $CELERY_BIN"
    echo "队列:     $QUEUES"
    echo ""

    if is_running "$WORKER_PATTERN"; then
        print_info "[✓] Celery Worker: 运行中 (PID: $(format_pids "$WORKER_PATTERN"))"
        ps -o pid,ppid,user,etime,command -p "$(format_pids "$WORKER_PATTERN" | tr ' ' ',')" | tail -n +2
    else
        print_warn "[×] Celery Worker: 未运行"
    fi
    echo ""

    if is_running "$BEAT_PATTERN"; then
        print_info "[✓] Celery Beat: 运行中 (PID: $(format_pids "$BEAT_PATTERN"))"
        ps -o pid,ppid,user,etime,command -p "$(format_pids "$BEAT_PATTERN" | tr ' ' ',')" | tail -n +2
    else
        print_warn "[×] Celery Beat: 未运行"
    fi

    echo ""
    echo "日志文件(nohup 重定向):"
    for log in "$CELERY_WORKER_LOG" "$CELERY_BEAT_LOG"; do
        if [ -f "$log" ]; then
            echo "  $log ($(du -h "$log" 2> /dev/null | cut -f1))"
        else
            echo "  $log (不存在)"
        fi
    done
    echo "日志文件(Loguru 实际落盘):"
    for log in "${CELERY_LOGURU_LOG_DIR}/celery_worker.log" "${CELERY_LOGURU_LOG_DIR}/celery_beat.log"; do
        if [ -f "$log" ]; then
            echo "  $log ($(du -h "$log" 2> /dev/null | cut -f1))"
        else
            echo "  $log (不存在)"
        fi
    done
    print_step "Celery 进程状态"
}

show_help() {
    echo "==================== Celery 启动脚本说明 ===================="
    echo ""
    echo "命令说明:"
    echo "  start [并发数]         # 启动 Worker + Beat"
    echo "  stop                  # 停止 Worker + Beat"
    echo "  restart [并发数]       # 重启 Worker + Beat"
    echo "  status                # 查看 Worker + Beat"
    echo "  start-worker [并发数]  # 仅启动 Worker"
    echo "  stop-worker           # 仅停止 Worker"
    echo "  start-beat            # 仅启动 Beat"
    echo "  stop-beat             # 仅停止 Beat"
    echo ""
    echo "日志目录: $CELERY_LOG_DIR"
    echo "提示: 自定义 logging 时必须由 setup_logging 接收 --logfile 并挂文件 sink"
    echo "==================== Celery 启动脚本说明 ===================="
    exit 1
}

# ==================== 主入口 ====================
main() {
    case "${1:-}" in
        start)
            [[ "${2:-}" =~ ^[0-9]+$ ]] && CONCURRENCY="$2"
            print_step "启动 Celery 服务 (并发: ${CONCURRENCY})"
            start_celery_worker || exit 1
            start_celery_beat || exit 1
            ;;
        stop)
            print_step "停止 Celery 服务"
            stop_celery_all
            ;;
        restart)
            [[ "${2:-}" =~ ^[0-9]+$ ]] && CONCURRENCY="$2"
            print_step "重启 Celery 服务 (并发: ${CONCURRENCY})"
            stop_celery_all
            sleep 2
            start_celery_worker || exit 1
            start_celery_beat || exit 1
            ;;
        status)
            celery_status
            ;;
        start-worker)
            [[ "${2:-}" =~ ^[0-9]+$ ]] && CONCURRENCY="$2"
            start_celery_worker || exit 1
            ;;
        stop-worker)
            stop_celery_worker
            ;;
        start-beat)
            start_celery_beat || exit 1
            ;;
        stop-beat)
            stop_celery_beat
            ;;
        *)
            show_help
            ;;
    esac
}

main "$@"
