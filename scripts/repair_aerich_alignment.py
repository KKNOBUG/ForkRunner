# -*- coding: utf-8 -*-
"""
修复 aerich 迁移记录与迁移文件错位问题的通用工具。

背景:
    aerich 用 aerich 表登记"哪些迁移文件已应用", 迁移序号和下次迁移的对比基线都取自表中id最大一条记录。
    这张表一旦与磁盘迁移文件对不上(漏记/多记/重复), 轻则迁移文件撞号(生产启动卡在
    "Miration file exists..."确认), 重则对比基线错乱(下次迁移生成全量甚至错误的SQL)。

修复动作(默认进入交互菜单选择执行; 追加 --execute 跳过菜单直接全流程修复):
    1. 去重: 同一version的多条记录只保留id最大一条;
    2. 删残留: 删除登记表里有、磁盘上已不存在的迁移记录;
    3. 补缺失: 磁盘有文件但登记表漏记的, 序号在补登上限内的仅补登记不执行SQL, 超过的真实执行SQL;
    4. 基线重建: 最新登记的快照为空占位({})时, 可用当前模型快照覆盖, 避免下次迁移全量diff;
    5. 迁移预览: 打印下次migrate将生成的SQL并标出DROP/RENAME风险, 防止删字段/改名字段被误判丢数据;
    6. 结果校验: 记录与文件一一对应、无重复、序号可正常推进、基线有效, 不通过则退出码为1。

行为边界(只修登记表, 不修表结构):
    - 数据库真实结构与迁移链的偏差(schema drift)无法感知和校正;
    - 基线过期导致的错误SQL、以及已被误删列的数据, 无法由本工具恢复;
    - 不防应用多进程(gunicorn多worker)同时迁移的冲突, 修复期间请先停止应用。

用法(在项目根目录执行):
    .venv/bin/python scripts/repair_aerich_alignment.py                            # 进入交互菜单
    .venv/bin/python scripts/repair_aerich_alignment.py --execute                  # 非交互全流程修复
    .venv/bin/python scripts/repair_aerich_alignment.py --execute --fake-max 8     # 非交互指定补登上限
    .venv/bin/python scripts/repair_aerich_alignment.py --execute --rebuild-baseline   # 非交互重建基线
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

import aiomysql
from dotenv import dotenv_values

BACKEND_DIR: Path = Path(__file__).resolve().parent.parent
MIGRATION_DIR: Path = BACKEND_DIR / "migrations" / "models"
# 补登记录用占位快照; 若它成为最新登记, 下次迁移会误判全库为空, 需 --rebuild-baseline 重建
FAKE_CONTENT: str = "{}"
# --fake-max all 时使用的上限, 效果是所有漏记迁移只补登记不执行SQL
FAKE_MAX_ALL: int = 10 ** 9
# MySQL GET_LOCK锁名前缀, 防止同时运行多个修复; 连接断开锁自动释放
LOCK_KEY_PREFIX: str = "aerich_repair"


def list_migration_files() -> list[tuple[int, str]]:
    """列出磁盘迁移文件，根据版本号升序。"""
    out: list[tuple[int, str]] = []
    for file in MIGRATION_DIR.glob("*.py"):
        num_str: str = file.name.split("_")[0]
        if num_str.isdigit():
            out.append((int(num_str), file.name))
    return sorted(out)


def find_duplicate_records(rows: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """找出同version多条记录中除id最大以外的全部记录(保留最后写入的一条)。"""
    latest_id_by_version: dict[str, int] = {}
    for row_id, version in rows:
        latest_id_by_version[version] = max(latest_id_by_version.get(version, 0), row_id)
    return [(row_id, version) for row_id, version in rows if row_id != latest_id_by_version[version]]


def resolve_fake_max(raw: str, matched_max_num: int | None) -> int:
    """解析补登上限: auto为已登记且与磁盘文件匹配的最大序号(无则-1即全部真实应用), all为全部仅补登, 其余为指定序号。"""
    if raw == "all":
        return FAKE_MAX_ALL
    if raw == "auto":
        return matched_max_num if matched_max_num is not None else -1
    return int(raw)


def fake_max_type(raw: str) -> str:
    if raw in ("auto", "all") or raw.isdigit():
        return raw
    raise argparse.ArgumentTypeError("fake-max 仅支持 auto/all/非负整数")


def print_repair_plan(
    duplicate_rows: list[tuple[int, str]],
    stale_rows: list[tuple[int, str]],
    missing_files: list[tuple[int, str]],
    fake_max: int,
) -> None:
    """打印修复计划: 重复/残留/缺失记录及对应动作。"""
    print(f"[重复记录](同version多条, 保留id最大, 其余将删除): {len(duplicate_rows)}")
    for row_id, version in duplicate_rows:
        print(f"    - id={row_id} version={version}")
    print(f"[残留记录](DB有记录/磁盘无文件, 将删除): {len(stale_rows)}")
    for row_id, version in stale_rows:
        print(f"    - id={row_id} version={version}")
    print(f"[缺失记录](磁盘有文件/DB无记录): {len(missing_files)}")
    for num, name in missing_files:
        action = "仅补登(不执行SQL)" if num <= fake_max else "真实应用(执行SQL)"
        print(f"    - {name} -> {action}")
    if any(num > fake_max for num, _ in missing_files):
        print("[提示] 标记为真实应用的文件将执行其SQL; 若此前已生效(如曾被fake登记), 执行会报列重复等错误,")
        print("       可改用 --fake-max <最高已生效序号> 将其仅补登。")


async def open_db_connection() -> aiomysql.Connection:
    """根据.env配置创建数据库连接。"""
    cfg = dotenv_values(BACKEND_DIR / ".env")
    required_keys = ("DATABASE_HOST", "DATABASE_PORT", "DATABASE_USERNAME", "DATABASE_PASSWORD", "DATABASE_NAME")
    missing_keys = [key for key in required_keys if not cfg.get(key)]
    if missing_keys:
        raise RuntimeError(f".env缺少数据库配置项: {missing_keys}")
    return await aiomysql.connect(
        host=cfg["DATABASE_HOST"],
        port=int(cfg["DATABASE_PORT"]),
        user=cfg["DATABASE_USERNAME"],
        password=cfg["DATABASE_PASSWORD"],
        db=cfg["DATABASE_NAME"],
        connect_timeout=5,
    )


def ensure_project_import() -> None:
    """将项目根目录加入sys.path, 支持从任意工作目录导入configure等包。"""
    root = str(BACKEND_DIR)
    if root not in sys.path:
        sys.path.insert(0, root)


def build_tortoise_config(project_config, app: str) -> dict:
    """构建与app_initialization.register_database一致的tortoise配置。"""
    return {
        "connections": project_config.DATABASE_CONNECTIONS,
        "apps": {
            app: {
                "models": project_config.APPLICATIONS_MODELS,
                "default_connection": "default",
            }
        },
        "use_tz": False,
        "timezone": "Asia/Shanghai",
    }


async def repair_records(app: str, fake_max: str, execute: bool, assume_yes: bool) -> tuple[list[tuple[int, str]], int]:
    """修复aerich登记表: 去重/删残留/补缺失; GET_LOCK防止同时运行多个修复。

    execute为False只分析打印; assume_yes为False时打印计划后需交互确认。
    返回(需要真实执行SQL的漏记文件列表, 计划变更的记录数)。
    """
    files = list_migration_files()
    file_names: set[str] = {name for _, name in files}
    conn = await open_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT GET_LOCK(%s, 0)", (f"{LOCK_KEY_PREFIX}_{app}",))
            if (await cur.fetchone())[0] != 1:
                print(f"获取修复锁失败: 存在其他修复实例({LOCK_KEY_PREFIX}_{app}), 请稍后重试。")
                sys.exit(1)
            await cur.execute("SELECT id, version FROM aerich WHERE app=%s ORDER BY id", (app,))
            rows: list[tuple[int, str]] = await cur.fetchall()

            duplicate_rows = find_duplicate_records(rows)
            duplicate_ids = {row_id for row_id, _ in duplicate_rows}
            effective_rows = [(row_id, version) for row_id, version in rows if row_id not in duplicate_ids]
            recorded: dict[str, int] = {version: row_id for row_id, version in effective_rows}
            stale_rows = [(row_id, version) for row_id, version in effective_rows if version not in file_names]
            missing_files = [(num, name) for num, name in files if name not in recorded]
            matched_max_num = max((num for num, name in files if name in recorded), default=None)
            resolved_fake_max = resolve_fake_max(fake_max, matched_max_num)
            pending_real = [(num, name) for num, name in missing_files if num > resolved_fake_max]
            planned_changes = len(duplicate_rows) + len(stale_rows) + len(missing_files)

            print(f"迁移目录: {MIGRATION_DIR}")
            print(f"aerich表记录数: {len(rows)}(其中重复{len(duplicate_rows)}条); 磁盘迁移文件数: {len(files)}; 补登上限(fake-max): {resolved_fake_max}")
            print_repair_plan(duplicate_rows, stale_rows, missing_files, resolved_fake_max)

            if not execute:
                print("\n以上为只读分析, 未做任何改动; 可通过菜单[2]登记修复或 --execute 执行。")
                return pending_real, planned_changes
            if not assume_yes:
                if planned_changes == 0:
                    print("[登记修复] 登记层无错位, 无需修复。")
                    return pending_real, 0
                if not confirm("确认执行以上登记修复?"):
                    print("已取消登记修复。")
                    return pending_real, 0

            for row_id, version in duplicate_rows:
                await cur.execute("DELETE FROM aerich WHERE id=%s", (row_id,))
                print(f"已删除重复记录: id={row_id} version={version}")
            for row_id, version in stale_rows:
                await cur.execute("DELETE FROM aerich WHERE id=%s", (row_id,))
                print(f"已删除残留记录: id={row_id} version={version}")
            for num, name in missing_files:
                if num > resolved_fake_max:
                    continue
                await cur.execute(
                    "INSERT INTO aerich (version, app, content) VALUES (%s, %s, %s)",
                    (name, app, FAKE_CONTENT),
                )
                print(f"已补登记录: {name}(仅登记, 不执行SQL)")
            await conn.commit()
            return pending_real, planned_changes
    finally:
        conn.close()


async def apply_pending_migrations(app: str) -> None:
    """调用aerich upgrade真实执行漏记迁移的SQL, 并由aerich写入真实模型快照。"""
    ensure_project_import()
    from aerich import Command
    from configure import PROJECT_CONFIG

    command = Command(app=app, tortoise_config=build_tortoise_config(PROJECT_CONFIG, app), location=str(MIGRATION_DIR.parent))
    await command.init()
    try:
        migrated = await command.upgrade(run_in_transaction=True)
    finally:
        await command.close()
    print(f"aerich upgrade 完成, 应用文件: {migrated}")


async def apply_pending_migrations_with_hint(app: str, pending_real: list[tuple[int, str]]) -> None:
    """真实应用未登记迁移文件, 失败时输出fake-max补登引导。"""
    pending_names = [name for _, name in pending_real]
    print(f"\n调用 aerich upgrade 真实应用 {len(pending_names)} 个文件: {pending_names}")
    try:
        await apply_pending_migrations(app)
    except Exception as exc:
        print(f"\n真实应用失败: {exc}")
        print("提示: 若报列重复/表已存在等错误, 说明这些文件的SQL此前已生效(如曾被fake登记),")
        print("      请改用 --fake-max <最高已生效序号> 将其仅补登后重跑。")
        raise


def preview_next_migrate(app: str) -> None:
    """预览下次migrate将生成的SQL(只算不落盘), 标出DROP/RENAME高风险操作。"""
    ensure_project_import()
    from aerich import Migrate
    from aerich.utils import get_models_describe

    # Migrate的操作列表是类级共享状态, diff前清空防止残留上次结果
    Migrate.upgrade_operators = []
    Migrate.downgrade_operators = []
    Migrate._upgrade_fk_m2m_index_operators = []
    Migrate._downgrade_fk_m2m_index_operators = []
    new_content = get_models_describe(app)
    Migrate.diff_models(Migrate._last_version_content, new_content, no_input=True)
    Migrate.diff_models(new_content, Migrate._last_version_content, False, no_input=True)
    Migrate._merge_operators()
    operators = Migrate.upgrade_operators
    if not operators:
        print("[预览] 下次migrate无变更: 基线快照与当前models一致, 重启服务不会再生成迁移文件。")
        return
    print(f"[预览] 下次aerich migrate将生成 {len(operators)} 条SQL:")
    drop_count = 0
    rename_count = 0
    for index, operator in enumerate(operators, start=1):
        print(f"    {index}. {operator.strip()}")
        upper_sql = operator.upper()
        if "DROP" in upper_sql:
            drop_count += 1
        elif "RENAME" in upper_sql:
            rename_count += 1
    print(f"[预览] 风险统计: DROP {drop_count} 条, RENAME {rename_count} 条。")
    print("[预览] 说明: RENAME为字段改名识别结果, 真实migrate时需交互确认(生产无人值守会卡住, 建议在开发环境生成迁移文件);")
    print("[预览]       若存在非预期的DROP, 说明基线与models错位已导致删字段误判, 执行前务必人工核对, 避免列数据丢失!")


async def check_baseline_and_preview(app: str, execute: bool, rebuild: bool, preview_enabled: bool) -> bool:
    """检查最新登记的快照基线, 按需重建; 随后预览下次迁移。返回基线是否有效。"""
    ensure_project_import()
    from aerich import Migrate
    from configure import PROJECT_CONFIG
    from tortoise import connections

    # aerich的Migrate.init会先用app查基线再赋值app, 调用前必须先设置否则报错
    Migrate.app = app
    await Migrate.init(build_tortoise_config(PROJECT_CONFIG, app), app, str(MIGRATION_DIR.parent))
    try:
        last = await Migrate.get_last_version()
        if last is None:
            print("[基线] aerich表无记录, 无基线可用; 空库场景请走应用启动的init-db初始化。")
            return False
        baseline_ok = bool(Migrate._last_version_content)
        if baseline_ok:
            print(f"[基线] 有效: id最大记录 {last.version}, 模型数 {len(Migrate._last_version_content)}。")
        else:
            print(f"[基线] 警告: id最大记录 {last.version} 的content为占位空快照, 下次migrate将以空基线全量diff!")
            if execute and rebuild:
                from aerich.utils import get_models_describe

                last.content = get_models_describe(app)
                await last.save(update_fields=["content"])
                Migrate._last_version_content = last.content
                baseline_ok = True
                print(f"[基线] 已重建: {last.version} -> 当前models快照({len(last.content)}个模型)。")
            else:
                print("[基线] 修复方式: 追加 --execute --rebuild-baseline 重建基线(前提: 所有迁移文件均已真实生效)。")
        if not preview_enabled:
            return baseline_ok
        if baseline_ok:
            preview_next_migrate(app)
        else:
            print("[预览] 基线无效, 跳过预览; 请先修复登记并重建基线。")
        return baseline_ok
    finally:
        await connections.close_all()


async def verify_repair(app: str) -> bool:
    """校验登记表与文件一一对应、无重复version、序号可正常推进、基线快照有效。"""
    files = list_migration_files()
    max_num = max(num for num, _ in files) if files else -1
    conn = await open_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id, version FROM aerich WHERE app=%s ORDER BY id", (app,))
            rows: list[tuple[int, str]] = await cur.fetchall()
            await cur.execute("SELECT id, version FROM aerich WHERE app=%s ORDER BY id DESC LIMIT 1", (app,))
            last = await cur.fetchone()
            await cur.execute("SELECT content FROM aerich WHERE app=%s ORDER BY id DESC LIMIT 1", (app,))
            last_content = (await cur.fetchone())[0]

        version_count: dict[str, int] = {}
        for _, version in rows:
            version_count[version] = version_count.get(version, 0) + 1
        duplicated = {version for version, count in version_count.items() if count > 1}

        ok = True
        print(f"\n[校验] aerich表记录数: {len(rows)}; 磁盘文件数: {len(files)}")
        if len(rows) == len(files) and not duplicated:
            print("[校验] 通过: 记录与文件一一对应, 无重复version。")
        else:
            ok = False
            print(f"[校验] 失败: 记录数与文件数不一致或存在重复version: {sorted(duplicated)}")
        last_num = int(last[1].split("_")[0]) if last else -1
        if last_num == max_num:
            print(f"[校验] 通过: id最大记录 {last[1]} 与最高版本文件一致, 后续migrate将生成序号 {max_num + 1}, 不再撞号。")
        else:
            ok = False
            print(f"[校验] 失败: id最大记录序号({last_num})与最高版本文件序号({max_num})不一致!")
        if last_content is None or str(last_content).strip() in ("{}", "null", ""):
            ok = False
            print("[校验] 失败: id最大记录content为占位空快照, 下次migrate将全量diff!")
        else:
            print("[校验] 通过: id最大记录content为真实快照。")
        return ok
    finally:
        conn.close()


async def repair(args: argparse.Namespace) -> None:
    """非交互模式(--execute): 按命令行参数执行 登记修复 -> 真实应用 -> 基线检查与预览 -> 结果校验。"""
    pending_real, _ = await repair_records(args.app, args.fake_max, execute=True, assume_yes=True)
    if pending_real:
        await apply_pending_migrations_with_hint(args.app, pending_real)
    baseline_ok = await check_baseline_and_preview(args.app, args.execute, args.rebuild_baseline, not args.no_preview)
    verify_ok = await verify_repair(args.app)
    if not (baseline_ok and verify_ok):
        print("\n修复结果存在警告或失败项, 请按上方提示人工核查!")
        sys.exit(1)
    print("\n修复完成。")


def read_input(prompt: str) -> str:
    """读取用户输入, 无标准输入(如管道执行)时提示并退出。"""
    try:
        return input(prompt).strip().lower()
    except EOFError:
        print("\n未检测到交互输入, 退出; 自动执行请追加 --execute 参数。")
        raise SystemExit(0) from None


def confirm(prompt: str, default: bool = False) -> bool:
    """交互确认, 直接回车取默认值。"""
    default_hint = "[Y/n]" if default else "[y/N]"
    raw = read_input(f"{prompt} {default_hint}: ")
    if not raw:
        return default
    return raw in ("y", "yes")


def prompt_fake_max(args: argparse.Namespace) -> str:
    """交互询问补登上限, 直接回车取默认值。"""
    raw = read_input(f"补登上限 fake-max [{args.fake_max}]: ")
    if not raw:
        return args.fake_max
    if raw in ("auto", "all") or raw.isdigit():
        return raw
    print(f"输入无效({raw}), 使用默认 {args.fake_max}。")
    return args.fake_max


def print_menu(args: argparse.Namespace) -> None:
    """打印能力菜单。"""
    print("-" * 90)
    print(" aerich 迁移修复工具 (只修迁移登记表, 不动业务数据; 写操作执行前都会二次确认)")
    print(f" 迁移目录: {MIGRATION_DIR}")
    print("-" * 90)
    print(" [1] 体检诊断   只读检查: 登记表与迁移文件是否对齐、基线是否有效, 并预演下次迁移")
    print(" [2] 登记修复   修正登记表与迁移文件一致: 去除重复、删除无效记录、补充遗漏(超上限的漏记会真实执行迁移应用)")
    print(" [3] 重建基线   最新登记的快照是空占位({})时, 用当前模型快照覆盖, 防止下次迁移生成全量建表SQL")
    print(" [4] 迁移预览   只读预演: 下次迁移将执行的SQL, 重点标出删列(DROP)和改列名(RENAME)高风险操作")
    print(" [5] 结果校验   只读复查: 登记表与迁移文件一一对应、序号可正常推进、基线快照有效")
    print(" [0] 一键修复   登记修复 -> 基线按需重建 -> 迁移预览 -> 结果校验(入口确认一次)")
    print(" [q] 退出")
    print("-" * 90)


async def capability_diagnose(args: argparse.Namespace) -> None:
    """体检诊断: 全程只读。"""
    await repair_records(args.app, args.fake_max, execute=False, assume_yes=True)
    await check_baseline_and_preview(args.app, execute=False, rebuild=False, preview_enabled=True)
    await verify_repair(args.app)


async def capability_repair_records(args: argparse.Namespace) -> bool:
    """登记修复: 打印计划并二次确认后执行。"""
    fake_max = prompt_fake_max(args)
    _, planned_changes = await repair_records(args.app, fake_max, execute=True, assume_yes=False)
    return planned_changes > 0


async def capability_rebuild_baseline(args: argparse.Namespace) -> None:
    """重建基线: 用当前模型快照覆盖空占位基线, 前提是所有迁移均已真实生效。"""
    if not confirm("重建会把id最大记录的content替换为当前models快照(仅当其为占位空快照时生效), 确认?"):
        print("已取消重建基线。")
        return
    baseline_ok = await check_baseline_and_preview(args.app, execute=True, rebuild=True, preview_enabled=False)
    print("[重建基线] 基线有效。" if baseline_ok else "[重建基线] 基线仍无效, 请检查上方输出。")


async def capability_preview(args: argparse.Namespace) -> None:
    """迁移预览: 打印下次migrate将生成的SQL并统计DROP/RENAME。"""
    await check_baseline_and_preview(args.app, execute=False, rebuild=False, preview_enabled=True)


async def capability_verify(args: argparse.Namespace) -> None:
    """结果校验: 只读校验登记一致性。"""
    verify_ok = await verify_repair(args.app)
    print("[校验] 全部通过。" if verify_ok else "[校验] 存在失败项, 请按上方提示人工核查!")


async def capability_full_fix(args: argparse.Namespace) -> None:
    """一键修复: 串联登记修复/真实应用/基线重建/迁移预览/结果校验。"""
    if not confirm("一键修复将依次执行登记修复、真实应用、基线按需重建、迁移预览与结果校验, 确认?"):
        print("已取消一键修复。")
        return
    pending_real, _ = await repair_records(args.app, args.fake_max, execute=True, assume_yes=True)
    if pending_real:
        await apply_pending_migrations_with_hint(args.app, pending_real)
    baseline_ok = await check_baseline_and_preview(args.app, True, True, True)
    verify_ok = await verify_repair(args.app)
    print("\n一键修复完成。" if baseline_ok and verify_ok else "\n一键修复存在失败项, 请按上方提示人工核查!")


async def interactive_main(args: argparse.Namespace) -> None:
    """交互模式: 展示能力菜单, 循环响应选择, 直至退出。"""
    while True:
        print_menu(args)
        choice = read_input("请选择功能编号: ")
        if choice in ("q", "quit", "exit"):
            print("已退出。")
            return
        if choice == "0":
            await capability_full_fix(args)
        elif choice == "1":
            await capability_diagnose(args)
        elif choice == "2":
            await capability_repair_records(args)
        elif choice == "3":
            await capability_rebuild_baseline(args)
        elif choice == "4":
            await capability_preview(args)
        elif choice == "5":
            await capability_verify(args)
        else:
            print(f"无效选择: {choice}, 请输入菜单中的编号。")
        print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="修复 aerich 迁移记录与迁移文件错位")
    parser.add_argument("--execute", action="store_true", help="非交互模式: 直接执行全流程修复(默认进入交互菜单)")
    parser.add_argument("--fake-max", type=fake_max_type, default="auto",
                        help="仅补登不执行SQL的序号上限: auto(默认, 已登记匹配的最大序号)/all/具体序号")
    parser.add_argument("--rebuild-baseline", action="store_true",
                        help="配合--execute: id最大记录content为占位空快照时, 重建为当前models快照")
    parser.add_argument("--no-preview", action="store_true", help="跳过下次迁移SQL预览")
    parser.add_argument("--app", default="models", help="aerich应用标签(默认 models)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    os.chdir(BACKEND_DIR)
    if args.execute:
        asyncio.run(repair(args))
    else:
        asyncio.run(interactive_main(args))
