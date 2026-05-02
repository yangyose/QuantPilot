"""DataStatus schema 字段一致性测试"""
from datetime import date

import pytest
from pydantic import ValidationError

from quantpilot.schemas.data import DataStatus


def test_schema_01_data_status_fields_match_service_output() -> None:
    """SCHEMA-01: DataStatus 字段集与 DataService.get_status() 返回 dict 键名完全一致"""
    # get_data_status() 返回的键
    repo_keys = {"latest_quote_date", "stock_count", "index_codes", "latest_financial_date"}
    # DataService.get_status() 追加的键
    service_keys = {"is_up_to_date"}
    expected = repo_keys | service_keys

    assert set(DataStatus.model_fields.keys()) == expected


def test_schema_02_data_status_parses_valid_dict() -> None:
    """SCHEMA-02: DataStatus 正确解析 get_status() 典型输出"""
    raw = {
        "latest_quote_date": date(2026, 1, 2),
        "stock_count": 5302,
        "index_codes": ["000001.SH", "000300.SH", "000905.SH", "399006.SZ"],
        "latest_financial_date": date(2026, 1, 2),
        "is_up_to_date": True,
    }
    status = DataStatus(**raw)

    assert status.stock_count == 5302
    assert status.is_up_to_date is True
    assert len(status.index_codes) == 4
    assert status.latest_quote_date == date(2026, 1, 2)


def test_schema_03_data_status_allows_none_dates() -> None:
    """SCHEMA-03: 空库场景 — latest_quote_date / latest_financial_date 允许为 None"""
    raw = {
        "latest_quote_date": None,
        "stock_count": 0,
        "index_codes": [],
        "latest_financial_date": None,
        "is_up_to_date": False,
    }
    status = DataStatus(**raw)
    assert status.latest_quote_date is None
    assert status.latest_financial_date is None


def test_schema_04_data_status_rejects_missing_required_field() -> None:
    """SCHEMA-04: 缺少必填字段 → ValidationError（捕获 API 层与 repo 层的字段不匹配）"""
    incomplete = {
        "latest_quote_date": date(2026, 1, 2),
        "stock_count": 100,
        # 缺少 index_codes、is_up_to_date、latest_financial_date
    }
    with pytest.raises(ValidationError):
        DataStatus(**incomplete)
