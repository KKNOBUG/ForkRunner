# ForkRunner 项目说明文档

## 目录

1. [技术栈](#技术栈)
2. [项目结构](#项目结构)
3. [核心架构与实现原理](#核心架构与实现原理)
4. [任务中心架构实现原理](#任务中心架构实现原理)
5. [调度机制](#调度机制)
6. [任务调度模式](#任务调度模式)
7. [配置文件说明](#配置文件说明)
8. [安装依赖](#安装依赖)
9. [手动部署项目](#手动部署项目)
10. [自动部署项目](#自动部署项目)
11. [启动Celery Worker服务](#启动celery-worker服务)
12. [启动Celery Beat服务](#启动celery-beat服务)
13. [API 接口 summary 编写规范](#api-接口-summary-编写规范)

## 技术栈

| 类别       | 技术组合 | 说明 |
|----------|----------|------|
| Web 框架   | FastAPI | 异步 Web 框架，自动 Swagger/ReDoc |
| ORM 框架   | Tortoise ORM + Aerich | 异步 ORM 与数据库迁移 |
| SQL 构建   | pypika-tortoise | 基于 Pypika 的 SQL 构建器 |
| 数据库      | MySQL | 业务数据持久化 |
| 数据库驱动    | aiomysql | MySQL 异步客户端 |
| 缓存 / 队列  | Redis | Celery 任务队列、缓存 |
| 任务调度     | Celery + Celery Beat + RedBeat | 定时 / 周期 / 一次性任务 |
| 认证       | JWT + argon2 | 无状态登录、Token 版本吊销 |
| 配置管理     | pydantic-settings | `.env` → 类型安全配置 |
| HTTP 客户端 | aiohttp | 异步 HTTP 客户端 |
| 日志       | loguru | 简化的日志收集器 |


## 项目结构

```
┌─ForkRunner
│  ├─applications               - 项目下所有子应用存储目录
│  │  ├─子应用 1                 - 内置目录结构请参考base应用
│  │  ├─子应用 2                 - ...
│  │  ├─子应用 N                 - ...
│  │  ├─base                    - 子应用
│  │  │  ├─__init__.py
│  │  │  ├─crud                 - 子应用数据库操作实现文件存放目录
│  │  │  ├─models               - 子应用数据库映射模型文件存放目录
│  │  │  ├─schemas              - 子应用模型数据序列化文件存放目录
│  │  │  ├─services             - 子应用业务逻辑实现文件存放目录
│  │  └─ └─views                - 子应用视图函数实现文件存放目录
│  ├─celery_scheduler           - Celery 实现
│  │  ├─__init__.py
│  │  ├─celery_base.py          - Celery 初始化配置
│  │  ├─celery_worker.py        - Worker 实现
│  │  └─ tasks                  - 各个子应用的任务定义文件存放目录
│  ├─common                     - 总项目中的公共方法、公共组件、公共工具类等实现
│  ├─configure                  - 总项目的各个配置文件存放目录
│  ├─core                       - 核心功能和实现
│  │  ├─__init__.py
│  │  ├─decorators              - 装饰器
│  │  ├─exceptions              - 异常处理
│  │  ├─initialization          - 初始化
│  │  ├─middleware              - 中间件
│  │  └─ response               - 响应处理
│  ├─enums                      - 总项目中的枚举构造
│  ├─output                     - 总项目中的输出文件存储目录
│  │  ├─__init__.py
│  │  ├─datagram                - 业务所需要数据文件模板
│  │  ├─docx                    - 需求/开发/依赖/说明类文档
│  │  ├─download                - 下载文件
│  │  ├─jmx                     - Jmeter脚本
│  │  ├─logs                    - 日志文件
│  │  ├─media                   - 多媒体文件
│  │  ├─upload                  - 上传文件
│  │  └─ xlsx                   - 其他数据文件
│  ├─service                    - 总项目中的公共业务实现、场景实现、业务底座等
│  ├─static                     - OpenAPI文档
│  ├─celery_start.sh            - Celery Linux启动脚本
│  ├─deploy.sh                  - ForkRunner Linux部署脚本
│  ├─backend_main.py            - 项目的启动文件
│  ├─gunicorn.conf.py           - Gunicorn进程管理器的配置文件
│  ├─README.md                  - 项目的说明文档
└─ └─requirements.txt           - 项目的依赖清单
```

## 核心架构与实现原理

### 1. autotest 应用设计架构

autotest 应用采用分层架构设计，主要包括以下几个层次：

1. **表现层（Presentation Layer）**：负责处理 HTTP 请求和响应，包括路由、视图、序列化等
2. **业务逻辑层（Business Logic Layer）**：负责处理业务逻辑，包括服务、业务规则等
3. **数据访问层（Data Access Layer）**：负责数据访问，包括模型、数据库操作等
4. **基础设施层（Infrastructure Layer）**：负责基础设施，包括配置、日志、缓存等

### 2. autotest 应用实现策略

#### 2.1 请求处理流程
1. **接收请求**：FastAPI 接收 HTTP 请求
2. **路由匹配**：根据 URL 和 HTTP 方法匹配路由
3. **中间件处理**：经过中间件处理（如认证、日志等）
4. **视图处理**：调用视图函数处理请求
5. **业务逻辑处理**：调用服务层处理业务逻辑
6. **数据访问**：调用数据访问层访问数据库
7. **响应返回**：返回 HTTP 响应

#### 2.2 任务执行流程
1. **任务提交**：将任务提交到 Celery 队列
2. **任务调度**：Celery Beat 根据调度策略调度任务
3. **任务执行**：Celery Worker 执行任务
4. **结果存储**：将任务执行结果存储到数据库
5. **状态更新**：更新任务状态

### 3. autotest 应用链路

#### 3.1 请求链路
```
用户请求 → FastAPI → 路由 → 中间件 → 视图 → 服务 → 数据访问 → 数据库
```

#### 3.2 任务链路
```
任务提交 → Celery 队列 → Celery Beat → Celery Worker → 任务执行 → 结果存储 → 状态更新
```

## 任务中心架构实现原理

### 1. Celery + Celery Beat + RedBeat 封装实现设计

#### 1.1 Celery 封装
- **作用**：提供分布式任务队列框架
- **特点**：支持异步、定时、重试任务执行
- **优势**：高可用、可扩展、易监控

#### 1.2 Celery Beat 封装
- **作用**：提供定时任务调度器
- **特点**：基于 Redis 实现
- **优势**：支持定时、周期、一次性任务

#### 1.3 RedBeat 封装
- **作用**：提供 Celery 定时任务调度器
- **特点**：基于 Redis 实现
- **优势**：支持定时、周期、一次性任务

### 2. 任务发现、调度、触发机制

#### 2.1 任务发现
1. **任务注册**：将任务注册到 Celery Beat
2. **任务扫描**：Celery Beat 扫描任务定义文件
3. **任务加载**：Celery Beat 加载任务定义

#### 2.2 任务调度
1. **调度策略**：根据调度策略（如定时、间隔等）调度任务
2. **任务分发**：将任务分发到 Celery Worker
3. **任务执行**：Celery Worker 执行任务

#### 2.3 任务触发
1. **事件触发**：根据事件触发任务执行
2. **手动触发**：手动触发任务执行
3. **定时触发**：根据定时策略触发任务执行

## 调度机制

### 1. 调度策略

#### 1.1 定时调度
- **作用**：按照固定时间执行任务
- **实现**：使用 Celery Beat 的 `crontab` 调度器
- **示例**：每天凌晨 2 点执行数据备份任务

#### 1.2 间隔调度
- **作用**：按照固定间隔执行任务
- **实现**：使用 Celery Beat 的 `interval` 调度器
- **示例**：每隔 5 分钟执行一次数据同步任务

#### 1.3 事件调度
- **作用**：根据事件触发任务执行
- **实现**：使用 Celery 的 `task` 装饰器
- **示例**：用户注册成功后发送欢迎邮件

### 2. 调度实现

#### 2.1 Celery Beat 配置
```python
# celery_base.py
from celery import Celery
from celery.schedules import crontab

app = Celery('ForkRunner')
app.config_from_object('configure.celery_config')

# 定时任务配置
app.conf.beat_schedule = {
    'backup-database': {
        'task': 'tasks.backup_database',
        'schedule': crontab(hour=2, minute=0),  # 每天凌晨 2 点执行
    },
    'sync-data': {
        'task': 'tasks.sync_data',
        'schedule': 300.0,  # 每隔 5 分钟执行一次
    },
}
```

#### 2.2 Celery Worker 配置
```python
# celery_worker.py
from celery import Celery

app = Celery('ForkRunner')
app.config_from_object('configure.celery_config')

# 任务定义
@app.task
def backup_database():
    """数据备份任务"""
    # 任务逻辑
    pass

@app.task
def sync_data():
    """数据同步任务"""
    # 任务逻辑
    pass
```

## 任务调度模式

### 1. 单机模式

#### 1.1 特点
- **简单**：部署简单，易于维护
- **性能**：性能有限，适合小规模应用
- **可用性**：单点故障，可用性较低

#### 1.2 适用场景
- 小规模应用
- 开发和测试环境
- 对可用性要求不高的场景

### 2. 分布式模式

#### 2.1 特点
- **复杂**：部署复杂，需要维护多个节点
- **性能**：性能高，适合大规模应用
- **可用性**：高可用，无单点故障

#### 2.2 适用场景
- 大规模应用
- 生产环境
- 对可用性要求高的场景

### 3. 混合模式

#### 3.1 特点
- **灵活**：根据需求选择单机或分布式模式
- **性能**：性能可调，适合不同规模应用
- **可用性**：可用性可调，适合不同可用性要求

#### 3.2 适用场景
- 不同规模应用
- 不同可用性要求
- 需要灵活部署的场景

## 配置文件说明

### 1. 配置文件结构

```
configure/
├── __init__.py
├── celery_config.py          # Celery 配置
├── database_config.py        # 数据库配置
├── global_config.py          # 全局配置
├── logging_config.py         # 日志配置
├── project_config.py         # 项目配置
└── router_registry.py        # 路由注册配置
```

### 2. 配置文件说明

#### 2.1 `celery_config.py`
- **作用**：配置 Celery 相关参数
- **内容**：包括 Broker、Backend、任务序列化、任务路由等
- **示例**：
```python
# Celery 配置
broker_url = 'redis://localhost:6379/0'
result_backend = 'redis://localhost:6379/0'
task_serializer = 'json'
result_serializer = 'json'
accept_content = ['json']
timezone = 'Asia/Shanghai'
enable_utc = True
```

#### 2.2 `database_config.py`
- **作用**：配置数据库相关参数
- **内容**：包括数据库连接、连接池、事务等
- **示例**：
```python
# 数据库配置
DATABASE_URL = 'mysql://user:password@localhost:3306/dbname'
DATABASE_POOL_SIZE = 10
DATABASE_MAX_OVERFLOW = 20
DATABASE_POOL_TIMEOUT = 30
```

#### 2.3 `global_config.py`
- **作用**：配置全局参数
- **内容**：包括日志级别、缓存配置、安全配置等
- **示例**：
```python
# 全局配置
LOG_LEVEL = 'INFO'
CACHE_EXPIRE = 3600
SECRET_KEY = 'your-secret-key'
```

#### 2.4 `logging_config.py`
- **作用**：配置日志相关参数
- **内容**：包括日志格式、日志级别、日志输出等
- **示例**：
```python
# 日志配置
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
LOG_LEVEL = 'INFO'
LOG_FILE = 'logs/app.log'
```

#### 2.5 `project_config.py`
- **作用**：配置项目相关参数
- **内容**：包括项目名称、版本、描述等
- **示例**：
```python
# 项目配置
PROJECT_NAME = 'ForkRunner'
PROJECT_VERSION = '1.0.0'
PROJECT_DESCRIPTION = 'ForkRunner 项目'
```

#### 2.6 `router_registry.py`
- **作用**：配置路由注册相关参数
- **内容**：包括路由前缀、路由标签、路由权限等
- **示例**：
```python
# 路由注册配置
ROUTER_PREFIX = '/api/v1'
ROUTER_TAGS = ['api', 'v1']
ROUTER_PERMISSIONS = ['admin', 'user']
```

## 安装依赖

```shell script
# 将项目中output\docx\ForkRunner_modules.zip依赖源下载并解压
# 全部安装
pip install --no-index --find-links=本地依赖源路径 -r requirements.txt

# 部分安装
pip install --no-index --find-links=本地依赖源路径 [依赖名称(可指定版本号)]
```

## 手动部署项目

```shell script
# 服务器：10.208.24.12
# 切换到项目根目录：
cd /zdhgj/python_projects/ForkRunner/

# 查询进程：
ps aux | grep gunicorn
ps aux | grep python

# 终止进程：
pkill -f -9 "backend_main:app"

# 拉取代码：
git pull origin fastapi-dev-master
> username
> password

# 启动进程
nohup gunicorn -c gunicorn.conf.py backend_main:app > /zdhgj/python_projects/ForkRunner/ForkRunner.log 2>&1 &
```

## 自动部署项目

```shell script
# 查看脚本权限：
ls -al

# 修改脚本权限：
chmod -R 777 deploy.sh

# 运行脚本
./deploy.sh

用户执行: ./deploy.sh full_deploy
    │
    ▼
┌─────────────────┐
│  1. fastapi_stop │  ← 停止 FastAPI (Gunicorn)
│     - 读取 gunicorn.pid
│     - kill -TERM (优雅停止)
│     - 等待15秒
│     - 未退出则 kill -9
│     - 清理残留 worker 进程
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  2. celery_stop  │  ← 停止 Celery
│     - celery_stop_beat (先停调度器)
│     - celery_stop_worker (后停工作器)
│     - 同样 TERM → KILL 渐进式
│     - 清理残留进程
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   sleep 2       │  ← 等待资源释放
│   (关键!)        │    确保端口/文件句柄释放
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  3. fastapi_pull │  ← 拉取最新代码
│     - 检查 git/expect 命令
│     - 检查 GIT_USERNAME/GIT_PASSWORD
│     - expect 交互式 git fetch
│     - git reset --hard 强制覆盖
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  4. celery_start │  ← 启动 Celery
│     - celery_start_worker (先启工作器)
│     - celery_start_beat (后启调度器)
│     - nohup + & 后台启动
│     - 等待10秒检测启动状态
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  5. fastapi_start│  ← 启动 FastAPI
│     - nohup + & 后台启动 Gunicorn
│     - 等待15秒检测启动状态
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  6. fastapi_status│ ← 显示 FastAPI 状态
│     - 运行状态/PID/Worker数/日志大小
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  7. celery_status │ ← 显示 Celery 状态
│     - Worker/Beat 状态/日志大小
└─────────────────┘
```

## 启动Celery Worker服务

```shell script
# celery_scheduler是专用于Celery的Worker实现

# Celery Worker Windows环境启动（Windows没有POSIX信号和Forx事件，因此只能单线程，网上说的可以借助gevent或eventlet实现协程池，但没效果）
celery -A celery_scheduler.celery_worker worker --pool=solo -l INFO

# Celery Worker Linux环境启动（需要添加端口前缀）
celery -A celery_scheduler.celery_worker worker --pool=solo -c 10 -l INFO --hostname=worker1@%h
```

## 启动Celery Beat服务

```shell script
# Celery Beat启动节拍器，定时任务需要
celery -A celery_scheduler.celery_worker beat --loglevel=info --scheduler=redbeat.schedulers:RedBeatScheduler
```

## API 接口 summary 编写规范

新增或修改 `applications/**/views/**/*.py` 中的路由时，**必须**为装饰器填写规范的 `summary`。  
权限初始化与「刷新路由」自动补绑依赖 `summary` 前缀动词识别行为类型，写法不规范会导致角色权限漏绑或错绑。

### 强制前缀（按行为选择其一）

| 行为 | summary 必须以…开头 | 说明 | 标准用户 | 宾客用户 |
|------|---------------------|------|:--------:|:--------:|
| 查询 | `查询` / `导出` / `下载` | 读操作；列表/详情/搜索/导出均属此类 | ✓ | ✓ |
| 新增 | `新增` | 创建资源 | ✓ | ✓ |
| 更新 | `更新` | 修改已有资源（含保存、解绑、移动等写变更） | ✓ | ✗ |
| 删除 | `删除` / `批量删除` / `清空` | 删除或清理 | ✓ | ✗ |
| 执行 | `执行` / `调试` / `启动` / `停止` | 运行、调试、启停类操作 | ✓ | ✗ |
| 导入 | `导入` / `上传` | 导入文件或上传数据 | ✓ | ✗ |
| 运维 | `刷新` | 如刷新路由，仅管理员 | ✗ | ✗ |

### 允许的特例（个人白名单 / 公开接口）

| summary | 用途 |
|---------|------|
| `更新用户密码(个人)` | 当前登录用户改密（标准/宾客可绑；与管理员侧 `更新用户密码(重置)` 区分） |
| `用户登出` | 退出登录（标准/宾客可绑） |
| `生成访问令牌` | 登录换 Token（公开接口） |

### 写法要求

1. **动词必须在开头**：`查询用户列表` ✓；`用户列表查询` ✗；`按条件查询用户` ✗（应写 `查询用户列表`）。
2. **统一同义词**：
   - 不要用 `创建` → 用 `新增`
   - 不要用 `编辑` / `修改` → 用 `更新`（个人改密写 `更新用户密码(个人)`）
   - 不要用 `查看` / `获取` / `预览` / `读取` → 用 `查询`
   - 不要用 `保存` / `移动` / `解绑` 单独作前缀 → 用 `更新…`
   - 异步导出写 `导出…(异步)`，不要写 `异步导出…`
3. **系统域与业务域**：路由 `tags` 使用 `一级目录:二级模块`（如 `系统管理:用户`、`自动化测试:用例`），与侧边栏菜单对齐；`summary` 只表达行为，不重复写模块名到前缀里。
4. **示例**：

```python
@xxx.get("/get", summary="查询用例")
@xxx.post("/create", summary="新增用例")
@xxx.post("/update", summary="更新用例")
@xxx.delete("/delete", summary="删除用例")
@xxx.post("/search", summary="查询用例列表")
@xxx.post("/run", summary="执行任务")
@xxx.post("/import_scripts", summary="导入公共接口脚本")
@xxx.post("/export_scripts", summary="导出公共接口脚本")
@xxx.post("/upload", summary="上传文件")
@xxx.get("/download", summary="下载文件")
```

5. **自检**：提交前确认 `summary` 能被上表某一行前缀匹配；若匹配不到，先改 summary，再合入。

### 角色权限与刷新路由

内置角色路由分配规则见 `applications/base/services/permission_rule.py`，与上表行为一致：

- **管理员**（`Administrators`）：全部路由；`admin` 用户另有 `is_superuser` 旁路
- **标准用户**（`Users`）：业务域全开；系统域仅「查询/导出/下载」；个人白名单可绑
- **宾客用户**（`Guests`）：业务域仅「查询/导出/下载」与「新增」；系统域仅读；个人白名单可绑

「刷新路由」在同步 `tbx_router` 后，会：
1. 对 **summary 无法按规范分类** 的路由打告警日志；
2. 按上述规则对三角色 **补绑缺失路由**（只追加）；
3. 将库中全部菜单 **补绑到三角色**（只追加，系统菜单对标准/宾客可见，写操作仍由路由约束）。

首次空库初始化顺序：`菜单 → 路由 → 角色 → 部门 → 用户 → 应用 → 标签`。
