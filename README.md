# 技术栈：

| 技术                 | 版本       | 作用                                                       |   
|---------------------|------------|-----------------------------------------------------------|
| Python              | 3.8.7      | 主要编程语言(Windos 7 最高支持Python 3.8.10)                  |  
| fastapi             | 0.115.4    | 基于Starlette+Pydantic且支持OpenAPI文档的高性能异步Web开发框架   |
| gunicorn            | 23.0.0     | WSGI进程管理器(用来管理Uvicorn工作进程，实现并发+协程组合)         |
| uvicorn             | 0.32.0     | ASGI异步处理器(专门运行FastAPI&Starlette应用)                  |
| aerich              | 0.7.2      | 数据库模型迁移工具(目前是自动迁移,请了解机制后决定)                 |
| aiomysql            | 0.2.0      | MySQL异步客户端(Tortoise-ORM的异步引擎)                        | 
| tortoise-ORM        | 0.23.0     | 异步ORM框架(纯异步实现,在查询和新增操作时性能较高于SQLAlchemy框架)  |
| pypika-tortoise     | 0.3.2      | 基于Pypika的SQL构建器(为Tortoise-ORM补充复杂的SQL语法支持)       |
| aiohttp             | 3.9.5      | 异步HTTP客户端(支持大量并发)                                   | 
| Celery              | 5.4.0      | 分布式任务队列框架(支持异步/定时/重试任务执行)                     |
| redis               | 5.0.8      | 缓存数据库(本项目中主要用于非重要数据关联和Celery消息管理)          |      
| flower              | 2.0.1      | 监控Celery异步任务执行                                        |
| loguru              | 0.7.2      | 简化的日志收集器(替代内置的logging模块)                          |


--------------------

# 项目结构

```
┌─fastapi_toolbox
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
│  ├─deploy.sh                  - fastapi_toolbox Linux部署脚本
│  ├─fastapi_toolbox.py         - 项目的启动文件
│  ├─gunicorn.configuration.py  - Gunicorn进程管理器的配置文件
│  ├─README.md                  - 项目的说明文档
└─ └─requirements.txt           - 项目的依赖清单
```

# 安装依赖
```shell script
# 将项目中output\docx\fastapi_toolbox_modules.zip依赖源下载并解压
# 全部安装
pip install --no-index --find-links=本地依赖源路径 -r requirements.txt

# 部分安装
pip install --no-index --find-links=本地依赖源路径 [依赖名称(可指定版本号)]

```

# 手动部署项目
```shell script
# 服务器：10.208.24.12
# 切换到项目根目录：
cd /zdhgj/python_projects/fastapi-toolbox/

# 查询进程：
ps aux | grep gunicorn
ps aux | grep python

# 终止进程：
pkill -f -9 "fastapi_toolbox:app"

# 拉取代码：
git pull origin fastapi-dev-master
> username
> password

# 启动进程
nohup gunicorn -c gunicorn.configuration.py fastapi_toolbox:app > /zdhgj/python_projects/fastapi-toolbox/fastapi-toolbox.log 2>&1 &
```

# 自动部署项目
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



# 启动Celery Worker服务
```shell script
# celery_scheduler是专用于Celery的Worker实现

# Celery Worker Windows环境启动（Windows没有POSIX信号和Forx事件，因此只能单线程，网上说的可以借助gevent或eventlet实现协程池，但没效果）
celery -A celery_scheduler.celery_worker worker --pool=solo -l INFO

# Celery Worker Linux环境启动
celery -A celery_scheduler.celery_worker worker --pool=solo -c 10 -l INFO

```

# 启动Celery Beat服务
```shell script
# Celery Beat启动节拍器，定时任务需要
celery -A celery_scheduler.celery_worker beat --loglevel=info --scheduler=redbeat.schedulers:RedBeatScheduler

```

# API 接口 summary 编写规范

新增或修改 `applications/**/views/**/*.py` 中的路由时，**必须**为装饰器填写规范的 `summary`。  
权限初始化与「刷新路由」自动补绑依赖 `summary` 前缀动词识别行为类型，写法不规范会导致角色权限漏绑或错绑。

## 强制前缀（按行为选择其一）

| 行为 | summary 必须以…开头 | 说明 | 标准用户 | 宾客用户 |
|------|---------------------|------|:--------:|:--------:|
| 查询 | `查询` / `导出` / `下载` | 读操作；列表/详情/搜索/导出均属此类 | ✓ | ✓ |
| 新增 | `新增` | 创建资源 | ✓ | ✓ |
| 更新 | `更新` | 修改已有资源（含保存、解绑、移动等写变更） | ✓ | ✗ |
| 删除 | `删除` / `批量删除` / `清空` | 删除或清理 | ✓ | ✗ |
| 执行 | `执行` / `调试` / `启动` / `停止` | 运行、调试、启停类操作 | ✓ | ✗ |
| 导入 | `导入` / `上传` | 导入文件或上传数据 | ✓ | ✗ |
| 运维 | `刷新` | 如刷新路由，仅管理员 | ✗ | ✗ |

## 允许的特例（个人白名单 / 公开接口）

| summary | 用途 |
|---------|------|
| `更新用户密码(个人)` | 当前登录用户改密（标准/宾客可绑；与管理员侧 `更新用户密码(重置)` 区分） |
| `用户登出` | 退出登录（标准/宾客可绑） |
| `生成访问令牌` | 登录换 Token（公开接口） |

## 写法要求

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

## 角色权限与刷新路由

内置角色路由分配规则见 `applications/base/services/permission_rule.py`，与上表行为一致：

- **管理员**（`Administrators`）：全部路由；`admin` 用户另有 `is_superuser` 旁路
- **标准用户**（`Users`）：业务域全开；系统域仅「查询/导出/下载」；个人白名单可绑
- **宾客用户**（`Guests`）：业务域仅「查询/导出/下载」与「新增」；系统域仅读；个人白名单可绑

「刷新路由」在同步 `tbx_router` 后，会：
1. 对 **summary 无法按规范分类** 的路由打告警日志；
2. 按上述规则对三角色 **补绑缺失路由**（只追加）；
3. 将库中全部菜单 **补绑到三角色**（只追加，系统菜单对标准/宾客可见，写操作仍由路由约束）。

首次空库初始化顺序：`菜单 → 路由 → 角色 → 部门 → 用户 → 应用 → 标签`。

