#!/bin/bash
# -*- coding: utf-8 -*-
# Celery Worker/Beat 部署脚本
# 手动部署流程:
#   cd /zdhgj/python_projects/fastapi-toolbox-runner
#   source .venv/bin/activate
#   pkill -f -9 “backend_main:app”
#   pkill -f -9 celery
#   nohup celery -A celery_scheduler.celery_worker worker -Q 8520_default,8520_autotest -c 4 -l INFO > /zdhgj/python_projects/fastapi-toolbox-runner/output/logs/celery_log/celery_worker.log 2>&1 &
#   nohup celery -A celery_scheduler.celery_worker beat -l INFO > /zdhgj/python_projects/fastapi-toolbox-runner/output/logs/celery_log/celery_beat.log 2>&1 &
#   ps aux | grep celery
#   nohup gunicorn -c gunicorn.conf.py backend_main:app > /zdhgj/python_projects/fastapi-toolbox-runner/toolbox-runner.log 2>&1
#   ps aux | grep gunicorn

# ==================== 基础路径 ====================
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
GUNICORN_BIN="${VENV_DIR}/bin/gunicorn"

# Gunicorn 服务(与手动部署一致)
GUNICORN_APP="backend_main:app"
GUNICORN_CONFIG_FILE="${PROJECT_ROOT}/gunicorn.conf.py"
# 手动部署 nohup 重定向日志: 项目根目录 toolbox-runner.log
FASTAPI_LOG_FILE="${PROJECT_ROOT}/toolbox-runner.log"

# Git 配置: 与手动 git pull origin toolbox-runner / git reset --hard origin/toolbox-runner 一致
GIT_BRANCH="${GIT_BRANCH:-toolbox-runner}"
# 可选: 私服账号密码(未配置时使用 git 已存储的凭证)
GIT_USERNAME="${GIT_USERNAME:-}"
GIT_PASSWORD="${GIT_PASSWORD:-}"

# Celery 编排脚本与并发数
CELERY_DEPLOY="${SCRIPT_DIR}/celery_deploy.sh"
CELERY_CONCURRENCY="${CELERY_CONCURRENCY:-4}"

cd "$PROJECT_ROOT" || { echo "无法进入项目目录: $PROJECT_ROOT"; exit 1; }
export PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

# 进程匹配模式(与手动 pkill -f "backend_main:app" 一致)
GUNICORN_PATTERN='backend_main:app'

# ==================== 输出函数 ====================
print_info()  { echo -e "\033[32m[INFO]\033[0m $1"; }
print_warn()  { echo -e "\033[33m[WARN]\033[0m $1"; }
print_error() { echo -e "\033[31m[ERROR]\033[0m $1"; }
print_step()  { echo -e "\n\033[36m========================================\033[0m"; echo -e "\033[36m$1\033[0m"; echo -e "\033[36m========================================\033[0m"; }

# ==================== 工具函数 ====================
activate_venv() {
    if [ -f "${VENV_DIR}/bin/activate" ]; then
        # shellcheck disable=SC1091
        source "${VENV_DIR}/bin/activate"
        return 0
    fi
    print_error "虚拟环境不存在: ${VENV_DIR}"
    return 1
}

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

# 等待进程稳定运行(应用启动含建表/迁移等重逻辑, 连续 3 秒且至少 10 秒才判定成功)
wait_gunicorn_alive() {
    local stable=0 elapsed=0

    while [ "$elapsed" -lt 30 ]; do
        if is_running "$GUNICORN_PATTERN"; then
            stable=$((stable + 1))
            if [ "$stable" -ge 3 ] && [ "$elapsed" -ge 10 ]; then
                print_info "FastAPI(Gunicorn) 启动成功 (PID: $(format_pids "$GUNICORN_PATTERN"))"
                print_info "日志文件: $FASTAPI_LOG_FILE"
                return 0
            fi
        else
            stable=0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done

    print_error "FastAPI(Gunicorn) 启动失败或超时, 请查看日志: $FASTAPI_LOG_FILE"
    if [ -f "$FASTAPI_LOG_FILE" ]; then
        print_error "----- 最近日志 -----"
        tail -n 50 "$FASTAPI_LOG_FILE" 2> /dev/null || true
    fi
    return 1
}

# ==================== 服务控制 ====================
# 步骤1: 停止旧服务(与手动顺序一致: 先 FastAPI 后 Celery)
stop_services() {
    print_step "步骤1: 停止旧服务"
    kill_by_pattern "$GUNICORN_PATTERN" "FastAPI(Gunicorn)" 10

    if [ ! -x "$CELERY_DEPLOY" ]; then
        print_error "Celery 部署脚本不存在: $CELERY_DEPLOY"
        return 1
    fi
    "$CELERY_DEPLOY" stop
    sleep 2
}

# 步骤2: 拉取最新代码(与手动一致: git pull origin $GIT_BRANCH, 失败/冲突时 git reset --hard 强制覆盖)
pull_code() {
    print_step "步骤2: 拉取 ${GIT_BRANCH} 分支最新代码"

    if ! command -v git > /dev/null 2>&1; then
        print_error "git 未安装, 请先安装..."
        return 1
    fi

    local pull_rc=0
    if [ -n "$GIT_USERNAME" ] && [ -n "$GIT_PASSWORD" ]; then
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
    else
        git pull origin "$GIT_BRANCH"
        pull_rc=$?
    fi

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

# 步骤3: 启动 Celery 服务(Worker + Beat)
start_celery() {
    print_step "步骤3: 启动 Celery 服务"
    if [ ! -x "$CELERY_DEPLOY" ]; then
        print_error "Celery 部署脚本不存在: $CELERY_DEPLOY"
        return 1
    fi
    "$CELERY_DEPLOY" start "$CELERY_CONCURRENCY"
}

# 步骤4: 启动 FastAPI 应用(与手动一致: nohup gunicorn -c gunicorn.conf.py backend_main:app)
start_fastapi() {
    print_step "步骤4: 启动 FastAPI 应用"

    if is_running "$GUNICORN_PATTERN"; then
        print_warn "FastAPI(Gunicorn) 已在运行(PID: $(format_pids "$GUNICORN_PATTERN")), 跳过启动"
        return 0
    fi
    activate_venv || return 1

    if [ ! -f "$GUNICORN_CONFIG_FILE" ]; then
        print_error "Gunicorn 配置文件不存在: $GUNICORN_CONFIG_FILE"
        return 1
    fi

    print_info "启动 Gunicorn 服务 (配置文件: $GUNICORN_CONFIG_FILE)"
    print_info "日志文件: $FASTAPI_LOG_FILE"
    nohup "$GUNICORN_BIN" -c "$GUNICORN_CONFIG_FILE" "$GUNICORN_APP" \
        > "$FASTAPI_LOG_FILE" 2>&1 &

    wait_gunicorn_alive
}

# 步骤5: 查看服务运行状态
·show_status() {
    print_step "服务运行状态"

    if is_running "$GUNICORN_PATTERN"; then
        print_info "[✓] FastAPI(Gunicorn): 运行中 (PID: $(format_pids "$GUNICORN_PATTERN"))"
        ps -o pid,ppid,user,etime,command -p "$(format_pids "$GUNICORN_PATTERN" | tr ' ' ',')" | tail -n +2
    else
        print_warn "[×] FastAPI(Gunicorn): 未运行"
    fi

    if [ -f "$FASTAPI_LOG_FILE" ]; then
        echo "  日志: $FASTAPI_LOG_FILE ($(du -h "$FASTAPI_LOG_FILE" 2> /dev/null | cut -f1))"
    fi
    echo ""

    if [ -x "$CELERY_DEPLOY" ]; then
        "$CELERY_DEPLOY" status
    else
        print_warn "[×] Celery 部署脚本不存在: $CELERY_DEPLOY"
    fi
}

# ==================== 完整流程 ====================
full_deploy() {
    print_info "开始完整部署流程..."
    print_info "项目目录: $PROJECT_ROOT"
    print_info "Git 分支: $GIT_BRANCH"
    print_info "Celery 并发: $CELERY_CONCURRENCY"

    stop_services || exit 1
    pull_code || exit 1
    start_celery || exit 1
    start_fastapi || exit 1

    echo ""
    show_status
    print_info "部署完成!"
}

# 仅重启服务(不拉取代码)
restart_services() {
    print_step "重启服务(不拉取代码)"
    stop_services || exit 1
    sleep 2
    start_celery || exit 1
    start_fastapi || exit 1

    echo ""
    show_status
    print_info "重启完成!"
}

show_help() {
    echo "==================== ToolBox 项目部署脚本 ===================="
    echo "命令说明:"
    echo "  start         # 完整部署(停止服务 -> 拉取master分支代码 -> 启动Celery服务 -> 启动FastAPI服务)"
    echo "  restart       # 仅重启服务(不拉取代码)"
    echo "  stop          # 停止所有服务"
    echo "  status        # 查看服务运行状态"
    echo "  pull          # 拉取master分支代码"
    echo ""
    echo "使用提示:"
    echo "  1. 首次使用前, 请确保已安装依赖"
    echo "  2. 确保gunicorn.configuration.py配置文件正确"
    echo "  3. 确保configure.project_config.py配置文件正确"
    echo "  4. 发生改动但未提交的文件会被直接放弃, 由 $GIT_BRANCH 分支代码覆盖"
    echo "==================== ToolBox 项目部署脚本 ===================="
    exit 1
}

# ==================== 主入口 ====================
main() {
    case "${1:-}" in
        start)
            [[ "${2:-}" =~ ^[0-9]+$ ]] && CELERY_CONCURRENCY="$2"
            full_deploy
            ;;
        restart)
            [[ "${2:-}" =~ ^[0-9]+$ ]] && CELERY_CONCURRENCY="$2"
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
