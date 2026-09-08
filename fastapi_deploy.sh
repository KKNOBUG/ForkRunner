#!/bin/bash
# -*- coding: utf-8 -*-
# FastAPI 部署脚本
# 原手动部署流程:
#   cd /zdhgj/python_projects/fastapi-toolbox-runner
#   source .venv/bin/activate
#   pkill -f -9 “backend_main:app”
#   pkill -f -9 celery
#   nohup celery -A celery_scheduler.celery_worker worker -Q 8520_default,8520_autotest -c 4 -l INFO > /zdhgj/python_projects/fastapi-toolbox-runner/output/logs/celery_logs/celery_worker.log 2>&1 &
#   nohup celery -A celery_scheduler.celery_worker beat -l INFO > /zdhgj/python_projects/fastapi-toolbox-runner/output/logs/celery_logs/celery_beat.log 2>&1 &
#   ps aux | grep celery
#   nohup gunicorn -c gunicorn.conf.py backend_main:app > /zdhgj/python_projects/fastapi-toolbox-runner/toolbox-runner.log 2>&1
#   ps aux | grep gunicorn
# ==================== 基础路径 ====================
# 部署约定: 本脚本与.venv虚拟环境同置于项目根目录
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
# 项目约定: 所有依赖均在.venv内, 必须先激活虚拟环境再启动服务
if [ ! -f "${VENV_DIR}/bin/activate" ]; then
    echo "虚拟环境不存在: ${VENV_DIR}"
    exit 1
fi
source "${VENV_DIR}/bin/activate"

# Gunicorn 应用标识(启动参数; 同时作为pgrep/pkill -f的进程匹配模式, 与手动执行pkill -f "backend_main:app"命令保持一致)
GUNICORN_APP="backend_main:app"
GUNICORN_CONFIG_FILE="${PROJECT_ROOT}/gunicorn.conf.py"
FASTAPI_LOG_FILE="${PROJECT_ROOT}/toolbox-runner.log"

# Git配置: 与手动执行git pull origin toolbox-runner/git reset --hard origin/toolbox-runner命令保持一致
GIT_BRANCH="toolbox-runner"
GIT_USERNAME="CS4224"
GIT_PASSWORD='KFuser01@!'

cd "$PROJECT_ROOT" || { echo "无法进入项目目录: $PROJECT_ROOT"; exit 1; }
export PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

# ==================== 输出函数 ====================
print_info()  { echo -e "\033[32m[INFO]\033[0m $1"; }
print_warn()  { echo -e "\033[33m[WARN]\033[0m $1"; }
print_error() { echo -e "\033[31m[ERROR]\033[0m $1"; }
print_step()  { echo -e "\n\033[36m========================================\033[0m"; echo -e "\033[36m$1\033[0m"; echo -e "\033[36m========================================\033[0m"; }

# ==================== 工具函数 ====================
pids_of() {
    pgrep -f "$1" 2> /dev/null | grep -vw "$$" || true
}

is_running() {
    [ -n "$(pids_of "$1")" ]
}

format_pids() {
    echo "$(pids_of "$1")" | tr '\n' ' ' | sed 's/ $//'
}

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

    pids="$(pids_of "$pattern")"
    if [ -n "$pids" ]; then
        print_warn "${name} ${timeout_s}s 内未退出, 强制 kill -9..."
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

# 启动后等待10秒, 进程仍存在即判定成功
wait_gunicorn_alive() {
    sleep 10
    if is_running "$GUNICORN_APP"; then
        print_info "FastAPI(Gunicorn) 启动成功 (PID: $(format_pids "$GUNICORN_APP"))"
        print_info "日志文件: $FASTAPI_LOG_FILE"
        return 0
    fi
    print_error "FastAPI(Gunicorn) 启动失败, 请查看日志: $FASTAPI_LOG_FILE"
    if [ -f "$FASTAPI_LOG_FILE" ]; then
        print_error "----- 最近日志 -----"
        tail -n 50 "$FASTAPI_LOG_FILE" 2> /dev/null || true
    fi
    return 1
}

# ==================== 服务控制 ====================
# 停止旧服务(与手动执行pkill -f "backend_main:app"命令保持一致)
stop_services() {
    print_step "停止旧服务"
    kill_by_pattern "$GUNICORN_APP" "FastAPI(Gunicorn)"
}

# 拉取最新代码(与手动执行一致: git pull 失败/冲突时 git reset --hard 强制覆盖)
pull_code() {
    print_step "拉取 ${GIT_BRANCH} 分支最新代码"

    if ! command -v git > /dev/null 2>&1; then
        print_error "git 未安装, 请先安装..."
        return 1
    fi
    if ! command -v expect > /dev/null 2>&1; then
        print_error "expect 未安装, 请先安装: yum install expect"
        return 1
    fi

    expect <<EOF
set timeout 60
spawn git pull origin "$GIT_BRANCH"
expect {
    "Username" {
        send "${GIT_USERNAME}\r"
        exp_continue
    }
    "Password" {
        send "${GIT_PASSWORD}\r"
        exp_continue
    }
    eof
}
expect eof
EOF
    pull_rc=$?

    if [ "$pull_rc" -ne 0 ]; then
        print_warn "git pull 失败(可能存在未提交改动或网络问题), 尝试强制对齐远端..."
    fi

    # 与手动部署一致: 放弃本地改动, 由 origin/$GIT_BRANCH 分支代码覆盖
    if ! git reset --hard "origin/${GIT_BRANCH}"; then
        print_error "git reset --hard origin/${GIT_BRANCH} 失败, 请检查远端分支是否存在"
        return 1
    fi
    [ "$pull_rc" -ne 0 ] && print_warn "已强制对齐本地仓库到 origin/${GIT_BRANCH}(如拉取失败请检查网络/凭证后重试)"
    print_info "代码更新成功(${GIT_BRANCH} 分支)"
}

# 启动 FastAPI 应用(与手动一致: nohup gunicorn -c gunicorn.conf.py backend_main:app)
start_fastapi() {
    print_step "启动 FastAPI 应用"

    if is_running "$GUNICORN_APP"; then
        print_warn "FastAPI(Gunicorn) 已在运行(PID: $(format_pids "$GUNICORN_APP")), 跳过启动"
        return 0
    fi

    if [ ! -f "$GUNICORN_CONFIG_FILE" ]; then
        print_error "Gunicorn 配置文件不存在: $GUNICORN_CONFIG_FILE"
        return 1
    fi

    print_info "启动 Gunicorn 服务 (配置文件: $GUNICORN_CONFIG_FILE)"
    print_info "日志文件: $FASTAPI_LOG_FILE"
    nohup gunicorn -c "$GUNICORN_CONFIG_FILE" "$GUNICORN_APP" \
        > "$FASTAPI_LOG_FILE" 2>&1 &

    wait_gunicorn_alive
}

# 查看服务运行状态
show_status() {
    print_step "服务运行状态"

    if is_running "$GUNICORN_APP"; then
        print_info "[✓] FastAPI(Gunicorn): 运行中 (PID: $(format_pids "$GUNICORN_APP"))"
        ps -o pid,ppid,user,etime,command -p "$(format_pids "$GUNICORN_APP" | tr ' ' ',')" | tail -n +2
    else
        print_warn "[×] FastAPI(Gunicorn): 未运行"
    fi

    if [ -f "$FASTAPI_LOG_FILE" ]; then
        echo "  日志: $FASTAPI_LOG_FILE ($(du -h "$FASTAPI_LOG_FILE" 2> /dev/null | cut -f1))"
    fi
}

# ==================== 完整流程 ====================
full_deploy() {
    print_info "开始完整部署流程..."
    print_info "项目目录: $PROJECT_ROOT"
    print_info "Git 分支: $GIT_BRANCH"

    stop_services || exit 1
    pull_code || exit 1
    start_fastapi || exit 1

    show_status
    print_info "部署完成!"
}

# 仅重启服务(不拉取代码)
restart_services() {
    print_step "重启服务(不拉取代码)"
    stop_services || exit 1
    start_fastapi || exit 1

    show_status
    print_info "重启完成!"
}

show_help() {
    echo "==================== ToolBox 项目部署脚本 ===================="
    echo "命令说明:"
    echo "  start         # 完整部署(停止服务 -> 拉取${GIT_BRANCH}分支代码 -> 启动FastAPI服务)"
    echo "  restart       # 仅重启服务(不拉取代码)"
    echo "  stop          # 停止所有服务"
    echo "  status        # 查看服务运行状态"
    echo "  pull          # 拉取${GIT_BRANCH}分支代码"
    echo ""
    echo "使用提示:"
    echo "  1. 首次使用前, 请先通过 requirements.txt 安装依赖"
    echo "  2. 确保gunicorn.conf.py配置文件正确"
    echo "  3. 确保configure.project_config.py配置文件正确"
    echo "  4. 发生改动但未提交的文件会被直接放弃, 由 $GIT_BRANCH 分支代码覆盖"
    echo "==================== ToolBox 项目部署脚本 ===================="
    exit 1
}

# ==================== 主入口 ====================
main() {
    case "${1:-}" in
        start)
            full_deploy
            ;;
        restart)
            restart_services
            ;;
        stop)
            print_step "停止所有服务"
            stop_services
            ;;
        status)
            show_status
            ;;
        pull)
            pull_code
            ;;
        *)
            show_help
            ;;
    esac
}

main "$@"
