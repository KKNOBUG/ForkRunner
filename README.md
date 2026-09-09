# ForkRunner 项目说明文档

## 目录

1. [技术栈](#技术栈)
2. [项目结构](#项目结构)
3. [核心架构](#核心架构)
4. [任务中心](#任务中心)
5. [调度机制](#调度机制)
6. [配置文件说明](#配置文件说明)
7. [安装依赖](#安装依赖)
8. [手动部署项目](#手动部署项目)
9. [自动部署项目](#自动部署项目)
10. [启动Celery Worker服务](#启动celery-worker服务)
11. [启动Celery Beat服务](#启动celery-beat服务)
12. [API 接口 summary 编写规范](#api-接口-summary-编写规范)

## 技术栈
| 类别 | 技术组合 | 说明 |
|---|---|---|
| Web 框架 | FastAPI | 异步框架，自动 Swagger/ReDoc |
| 数据校验 | Pydantic | 请求/响应模型校验 |
| ORM 框架 | Tortoise ORM + Aerich | 异步 ORM + 迁移 |
| SQL 构建 | pypika-tortoise | 基于 Pypika 构建 SQL |
| 数据库 | MySQL + aiomysql | 数据存储，异步驱动 |
| 缓存 / 队列 | Redis | 任务队列 + 缓存 |
| 任务调度 | Celery + Beat + RedBeat | 周期 / 定时 / 一次性任务 |
| 认证 | JWT + argon2 | 无状态认证，Token 可吊销 |
| 配置管理 | pydantic-settings | .env 类型安全加载 |
| HTTP 客户端 | aiohttp | 异步 HTTP 请求 |
| JSON 处理 | jsonpath + orjson | JSON 定位查询 + 高速序列化 |
| 日志 | loguru | 日志收集 |
| 部署 | Gunicorn + Uvicorn | 生产级 ASGI 部署 |


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
│  │  └─ responses              - 响应处理
│  ├─enums                      - 总项目中的枚举构造
│  ├─output                     - 总项目中的输出文件存储目录
│  │  ├─__init__.py
│  │  ├─datagram                - 业务文件
│  │  ├─docx                    - 需求/开发/依赖/说明类文档
│  │  ├─download                - 下载文件
│  │  ├─jmx                     - Jmeter脚本
│  │  ├─logs                    - 日志文件
│  │  │  └─ celery_logs         - celery worker/beat日志文件
│  │  ├─template                - 模板文件
│  │  ├─upload                  - 上传文件
│  │  └─ xlsx                   - 其他数据文件
│  ├─scripts                    - 总项目中的辅助脚本
│  ├─service                    - 总项目中的公共业务实现、场景实现、业务底座等
│  ├─static                     - 静态文件, 如OpenAPI文档
│  ├─celery_start.sh            - Celery 部署脚本
│  ├─fastapi_deploy.sh          - FastAPI 部署脚本
│  ├─backend_main.py            - 项目的启动文件
│  ├─gunicorn.conf.py           - Gunicorn进程管理器的配置文件
│  ├─README.md                  - 项目的说明文档
└─ └─requirements.txt           - 项目的依赖清单
```

## 核心架构

### 1. 总体架构

**功能目标**：构建「Web 服务 + 任务调度」双进程模型的测试管理平台。FastAPI 进程承载同步 API 服务（接口、鉴权、数据管理），Celery 进程承载异步耗时任务（用例执行、数据导出），两者共享同一套业务模型与服务层，通过 Redis 队列解耦。

**能力边界**：

- Web 进程只负责同步请求、任务提交与结果查询，不执行耗时作业
- Celery 进程只负责后台异步执行，运行时无登录态，触发人身份随任务参数传递
- MySQL 是唯一事实来源；Redis 只承担消息队列、调度持久化与缓存，不沉淀业务数据

**实施策略**：

- 分层结构：views（视图）→ services（业务）→ models（模型），schemas 负责出入参校验与序列化，各层职责单一
- 公共能力下沉：common（工具组件）、core（中间件、异常、响应、初始化）、enums、services（跨应用业务底座）统一供各应用复用
- 应用即插即用：applications 下新增子目录即自动注册为子应用，模型自动纳入迁移，子应用内部目录结构保持一致

### 2. autotest 应用（自动化测试）

autotest 模块的功能是完成接口自动化测试，提供了 HTTP请求、TCP请求、数据库请求、Python代码请求、Redis请求、条件分支、循环结构、等待控制、断言、引用公共脚本/接口、报文比对等类型操作步骤：

1. **HTTP请求**：发送 HTTP/HTTPS 协议的网络请求，支持常用请求方法与多种请求体类型，按项目环境自动拼装目标地址，支持变量提取、断言与参数化数据驱动
2. **TCP请求**：发送 TCP 协议报文，支持变量提取、断言与参数化数据驱动
3. **数据库请求**：按环境配置连接被测数据库执行 SQL，单步骤支持多条操作串行执行，查询结果可存为会话变量供后续步骤引用，支持查到即止
4. **Redis请求**：连接目标 Redis 执行命令，单步骤支持多条操作，结果可存为会话变量
5. **代码请求(Python)**：执行自定义 Python 代码，用于复杂业务逻辑的编排处理
6. **条件分支**：提供 if/elif/else 语义，按顺序评估条件、命中即执行对应子步骤；全部未命中且无 else 时本步视为通过
7. **循环结构**：支持次数、列表、字典、条件四种循环模式，提供中断循环/停止整个用例/继续下一次三类错误处理策略，内置防死循环保护
8. **等待控制**：在步骤执行间插入固定等待，用于节奏控制
9. **断言**：提供等于/不等于、大小比较、长度比较、包含、集合归属、前后缀匹配、空值判断等十余种比较方式
10. **引用公共脚本/接口**：引用公共用例的步骤集合，随父用例执行，实现测试资产复用
11. **报文比对**：对请求/响应报文进行字段级差异比对，用于数据一致性验证

配套能力：

- **用例管理**：树形步骤编排、用例与脚本的导入导出
- **环境配置**：应用（项目）→ 环境 → 配置节点三级模型，管理各被测系统的地址与连接信息
- **数据驱动**：数据源管理与数据集参数化，同一用例可按数据集批量执行
- **任务管理**：手动/定时双触发，任务执行记录与状态自动回写
- **报告与明细**：用例级测试报告、步骤级执行明细（含循环/分支结构展示）与执行日志留存
- **调试工具**：TCP/HTTP 报文调试，便于用例开发期快速验证

### 3. base 应用（系统管理）

base 模块的功能是提供平台底座与权限体系，提供认证、用户、角色、菜单、路由管理能力：

1. **认证**：JWT 无状态认证（argon2 密码加密），支持 Token 吊销
2. **权限绑定**：以路由 summary 前缀识别行为类型（查询/新增/更新/删除/执行/导入/运维），「刷新路由」时为管理员、标准用户、宾客三类内置角色自动补绑路由与菜单
3. **数据初始化**：首次空库按「菜单 → 路由 → 角色 → 部门 → 用户 → 应用 → 标签」顺序完成初始化

### 4. 其余应用

- **user**：用户管理，维护用户资料
- **department**：部门管理，维护组织架构
- **toolbox**：便捷工具，沉淀通用小工具

## 任务中心

### 1. 功能目标

任务中心承载平台全部耗时后台作业，与 Web 进程解耦，保证接口响应速度与任务执行稳定性。当前注册五类任务：

| 任务 | 说明 |
|---|---|
| 调度扫描 | Beat 周期触发，扫描到期定时任务并下发执行 |
| 用例编排 | 按任务绑定的用例集合批量执行自动化用例 |
| 用例执行 | 后台执行单用例步骤树 |
| 导出用例数据 | 将用例请求头/请求体导出为 xlsx |
| 导出公共接口 | 将公共接口脚本导出为 xlsx |

### 2. 能力边界

- 任务运行时无登录态，触发人随任务参数传递；定时触发且未显式传入时回退任务维护人
- 任务状态、入参快照、执行结果全部落库（执行记录），不依赖 Celery result backend 作业务查询
- 任务失败不自动重试（失败结果已落库，重试只会重复写失败记录）；worker 进程崩溃由消息重投机制兜底

### 3. 实施策略

- **任务注册契约**：所有任务在 `celery_task_contract` 统一登记「注册名 → 任务类型/展示名」，执行记录据此自动分类
- **观测自动化**：任务执行前由 worker 信号自动创建 RUNNING 执行记录（含入参快照与链路 ID），成功/失败回调统一回写终态与结果摘要
- **链路追踪**：trace_id/span_id 从 Web 提交贯穿到 Worker 执行与日志，跨进程可检索
- **可靠性保障**：失败/超时即 ack 终结消息；worker 崩溃自动重投；消息可见性超时大于任务硬时限，防止长任务执行期间被判失联而重复消费

## 调度机制

### 1. 功能目标

为自动化任务提供「手动触发 + 定时调度」两种触发方式，定时精度为分钟级。

### 2. 能力边界

- 定时任务定义存于业务表（周期/时刻表达式），支持执行 1 次与周期执行；新增定时任务无需改动调度配置
- Beat 自身只承载一个固定扫描入口，不直接挂载业务任务；业务任务的到期判断由扫描任务完成
- 扫描调度与业务执行分队列运行互不阻塞；单次任务执行时长受任务级时限约束

### 3. 实施策略

- **扫描下发**：Beat 每 60 秒触发一次扫描任务，检索启用中的定时任务，对到期任务逐点下发执行；一次性任务在触发点全部消费后自动关闭
- **调度器高可用**：采用 RedBeat 调度器，调度状态基于 Redis 持久化并持有分布式锁，Beat 多实例部署时调度不重复
- **队列隔离**：按服务端口划分 default 与 autotest 双队列，同机多项目天然隔离
- **执行模型**：Worker 使用 prefork 进程池，适合读取数据、执行脚本、写库等 IO 密集任务；队列积压时优先扩并发，内存受限时评估切换 threads 模式，不使用 gevent/eventlet 协程池（避免 oracledb thick 驱动阻塞事件循环）

## 配置文件说明

配置统一收口于 `configure/` 目录，各文件职责与边界如下：

| 配置文件 | 作用 | 边界 |
|---|---|---|
| `project_config.py` | 项目主配置：从根目录 `.env` 加载应用元信息、服务地址/端口/调试开关、JWT 密钥、日志轮转、目录路径、CORS、上传限制、数据库/Redis 连接信息与 Celery DB 编号、Oracle 客户端模式；开发/生产差异经 `SERVER_DEBUG` 自动切换 | 平台自身运行配置的唯一入口；不含 Celery 运行细节与日志格式 |
| `celery_config.py` | Celery 运行配置：基于 project_config 组装 broker/backend/RedBeat 连接地址、队列命名与路由、任务导入清单、可靠性参数（任务时限、消息可见性超时）、Beat 扫描入口与 RedBeat 锁参数、Celery 专用日志文件路径 | 只描述任务队列与调度器行为，不含业务逻辑 |
| `database_config.py` | 被测系统数据源登记表：按「环境 → 单元 → 分区 → 分片」层级组织被测业务库连接信息，供自动化测试「数据库请求」步骤使用 | 面向被测系统，不是平台自身数据库（平台库连接由 `.env` 与 project_config 提供） |
| `logging_config.py` | 日志体系初始化：Loguru 统一日志、多进程安全的文件轮转、接管 stdlib/uvicorn/gunicorn logger、每条日志自动注入 trace_id/span_id | Web 与 Celery 进程共用；Celery 专用日志文件落盘路径由 celery_config 提供 |
| `global_config.py` | 全局常量：日期/时间格式等无环境差异的通用常量 | 不放任何环境相关配置；运行时路由元数据见 router_registry |
| `router_registry.py` | 运行时路由元数据容器：应用启动后由 lifespan 填充路由 summary/tags，供审计/日志中间件读取 | 只存运行时状态，不含静态配置 |

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
git pull origin toolbox-runner
> username
> password

# 启动进程
nohup gunicorn -c gunicorn.conf.py backend_main:app > /zdhgj/python_projects/fastapi-toolbox-runner/backend_main.log 2>&1 &
```

## 自动部署项目

仓库根目录提供两个相互独立的部署脚本（均在脚本内自动激活 `.venv` 虚拟环境，且不相互编排）：

| 脚本 | 职责 |
|---|---|
| `fastapi_deploy.sh` | 仅管理 Gunicorn(FastAPI)：start(停止旧服务+拉取代码+启动) / restart / stop / status / pull；Git 分支(toolbox-runner)/账号/密码内置于脚本 |
| `celery_deploy.sh` | 仅管理 Celery Worker/Beat：start / stop / restart / status 及 worker/beat 级子命令；队列(8520_default,8520_autotest)与并发(默认4)内置于脚本，第二参数可覆盖并发 |

```shell script
# 查看脚本权限：
ls -al *.sh

# 添加执行权限：
chmod +x fastapi_deploy.sh celery_deploy.sh

# 完整部署（两脚本按序组合执行）：
./celery_deploy.sh stop              # 1. 停止 Celery(先 Beat 后 Worker + 兜底清理残留)
./fastapi_deploy.sh start            # 2. 停止旧 Gunicorn -> 拉取 toolbox-runner 分支代码 -> 启动 FastAPI
./celery_deploy.sh start             # 3. 启动 Celery Worker + Beat

# 单独操作示例：
./fastapi_deploy.sh restart          # 仅重启 FastAPI(不拉取代码)
./celery_deploy.sh restart 8         # 重启 Celery(并发数覆盖为 8)
./fastapi_deploy.sh status           # 查看 FastAPI 状态
./celery_deploy.sh status            # 查看 Celery 状态
```

脚本内部细节：

- **停止**：按命令行模式 pgrep -f 定位进程（FastAPI 匹配 `backend_main:app`；Celery 匹配 `celery_scheduler.celery_worker` 并区分 worker/beat，均排除脚本自身 PID），先 `kill -TERM` 逐秒等待（FastAPI 10秒、Celery Worker 15秒、Beat 10秒），超时后 `kill -9`，终验仍存活则报错并以非零退出
- **拉取代码**（fastapi_deploy.sh）：expect 交互式 `git pull origin toolbox-runner`；pull 失败或存在本地改动时 `git reset --hard origin/toolbox-runner` 强制对齐远端
- **启动**：nohup 后台启动（FastAPI 日志重定向项目根 `toolbox-runner.log`；Celery 先导出 `CELERY_LOGFILE` 再落 `output/logs/celery_logs/`），等待 10 秒进程仍存活即判定成功，失败输出最近日志并以非零退出

补充说明：

- 日志位置：FastAPI → 项目根 `toolbox-runner.log`；Celery → `output/logs/celery_logs/celery_worker.log` 与 `celery_beat.log`

## 启动Celery Worker服务

`celery_scheduler` 是专用于 Celery 的 Worker 实现。

```shell script
# 手动启动必须显式监听双队列：用例执行任务被路由到 {port}_autotest 队列，
# 只消费默认队列时该类任务将滞留；端口与 configure/celery_config.py 保持一致（当前 8520）
celery -A celery_scheduler.celery_worker worker -Q 8520_default,8520_autotest -c 4 -l INFO

# Windows 开发环境（无 POSIX 信号，仅能单进程串行执行）
celery -A celery_scheduler.celery_worker worker --pool=solo -l INFO
```

生产环境建议直接使用部署脚本（自动激活虚拟环境、后台启动、失败检测）：

```shell script
./celery_deploy.sh start [并发数]         # Worker + Beat
./celery_deploy.sh start-worker [并发数]  # 仅 Worker
```

关键约定：

- **队列**：`{port}_default`（默认队列）与 `{port}_autotest`（用例执行队列）双队列，与 `configure/celery_config.py` 对齐，celery_deploy.sh 内置同名配置
- **并发模型**：默认 prefork 进程池，并发数默认 4，脚本第二参数可覆盖
- **日志**：统一落 `output/logs/celery_logs/celery_worker.log`。Worker 经 `setup_logging` 信号将日志接入 Loguru，文件按「`--logfile` → `CELERY_LOGFILE` 环境变量 → 代码默认路径」的顺序确定；prefork 子进程依据 `CELERY_LOGFILE` 环境变量写同一文件（部署脚本启动前已导出）

## 启动Celery Beat服务

```shell script
# Celery Beat启动节拍器，定时任务需要；调度器(RedBeat)已在 configure/celery_config.py
# 的 CELERY_BEAT_SCHEDULER 中配置，手动启动无需再显式传 --scheduler
celery -A celery_scheduler.celery_worker beat -l INFO
```

生产环境建议直接使用部署脚本：

```shell script
./celery_deploy.sh start-beat    # 仅启动 Beat
```

说明：

- 调度状态持久化在 Redis 的 RedBeat 专用库，RedBeat 分布式锁保证 Beat 多实例部署时调度不重复
- Beat 仅承载调度扫描入口（每 60 秒触发一次），业务定时任务的定义与到期判断在数据库侧完成
- 日志统一落 `output/logs/celery_logs/celery_beat.log`

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
