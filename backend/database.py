import logging

from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "sqlite:////app/data/mist.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

logger = logging.getLogger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_columns(conn, table: str, cols: list[tuple[str, str]]) -> None:
    result = conn.execute(text(f"PRAGMA table_info({table})"))
    existing = {row[1] for row in result}
    for col_name, col_type in cols:
        if col_name not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
            logger.info(f"Migration: added {col_name} to {table}")


def _rename_columns(conn, table: str, renames: list[tuple[str, str]]) -> None:
    result = conn.execute(text(f"PRAGMA table_info({table})"))
    existing = {row[1] for row in result}
    for old_name, new_name in renames:
        if old_name in existing and new_name not in existing:
            conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN {old_name} TO {new_name}"))
            logger.info(f"Migration: renamed {old_name} → {new_name} in {table}")


def migrate_db():
    """既存テーブルのスキーマを更新する。"""
    try:
        with engine.connect() as conn:
            # ap_metrics: bandwidth / util breakdown カラム追加
            _add_columns(conn, "ap_metrics", [
                ("radio_24_bandwidth", "INTEGER"),
                ("radio_24_util_tx", "FLOAT"),
                ("radio_24_util_rx_in_bss", "FLOAT"),
                ("radio_24_util_non_wifi", "FLOAT"),
                ("radio_5_bandwidth", "INTEGER"),
                ("radio_5_util_tx", "FLOAT"),
                ("radio_5_util_rx_in_bss", "FLOAT"),
                ("radio_5_util_non_wifi", "FLOAT"),
                ("radio_6_bandwidth", "INTEGER"),
                ("radio_6_util_tx", "FLOAT"),
                ("radio_6_util_rx_in_bss", "FLOAT"),
                ("radio_6_util_non_wifi", "FLOAT"),
            ])

            # app_settings: monitored_site_ids カラム追加
            _add_columns(conn, "app_settings", [
                ("monitored_site_ids", "TEXT"),
            ])

            # radio_config_current: 旧列名 → 新列名へリネーム（先に実行）
            _rename_columns(conn, "radio_config_current", [
                ("device_profile_id", "deviceprofile_id"),
                ("device_profile_name", "deviceprofile_name"),
                ("rf_template_id", "rftemplate_id"),
                ("rf_template_name", "rftemplate_name"),
            ])

            # radio_config_current: 新しい列名で追加（リネーム後でも未存在なら追加）
            _add_columns(conn, "radio_config_current", [
                ("config_source_24", "VARCHAR"),
                ("config_source_5", "VARCHAR"),
                ("config_source_6", "VARCHAR"),
                ("deviceprofile_id", "VARCHAR"),
                ("deviceprofile_name", "VARCHAR"),
                ("rftemplate_id", "VARCHAR"),
                ("rftemplate_name", "VARCHAR"),
            ])

            # radio_config_changes: 旧スキーマ（audit_id列あり）なら DROP して再作成
            result = conn.execute(text(
                "SELECT COUNT(*) FROM pragma_table_info('radio_config_changes') WHERE name='audit_id'"
            ))
            if (result.scalar() or 0) > 0:
                conn.execute(text("DROP TABLE IF EXISTS radio_config_changes"))
                logger.info("Migration: dropped old radio_config_changes table")

            conn.commit()
    except Exception as e:
        logger.warning(f"migrate_db skipped (table may not exist yet): {e}")
