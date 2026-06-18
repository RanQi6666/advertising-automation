from collections.abc import Iterable

FIELD_ALIASES = {
    "project_name": {"项目名称", "项目", "工单名称"},
    "country": {"投放国家", "国家", "地区", "投放地区"},
    "launch_time": {"投放时间", "上线时间", "开始时间"},
    "report_timezone": {"日报时区", "时区", "报表时区"},
    "media": {"投放媒体", "媒体", "渠道", "平台"},
    "event_name": {"投放事件", "事件", "转化事件", "优化事件"},
    "audience_description": {"投放人群", "人群", "受众", "目标人群"},
    "product_name": {"产品名称", "产品", "品名"},
    "payout_amount": {"打款金额", "预算", "金额"},
    "service_fee": {"服务费", "费率"},
    "business_owner": {"商务", "商务负责人", "销售"},
    "landing_url": {"投放链接", "链接", "落地页", "落地页链接", "url", "URL"},
}


def parse_work_order_text(raw_content: str) -> dict:
    parsed: dict[str, str] = {}
    unknown: dict[str, str] = {}

    for line in _non_empty_lines(raw_content):
        key, value = _split_key_value(line)
        if not key or not value:
            continue

        normalized_key = _normalize_key(key)
        if normalized_key:
            parsed[normalized_key] = value
        else:
            unknown[key] = value

    if unknown:
        parsed["_unknown"] = unknown
    return parsed


def _non_empty_lines(raw_content: str) -> Iterable[str]:
    for line in raw_content.replace("\r\n", "\n").split("\n"):
        cleaned = line.strip()
        if cleaned:
            yield cleaned


def _split_key_value(line: str) -> tuple[str | None, str | None]:
    separators = ["：", ":", "；", ";"]
    positions = [(separator, line.find(separator)) for separator in separators]
    positions = [(separator, index) for separator, index in positions if index > 0]
    if not positions:
        return None, None

    separator, index = min(positions, key=lambda item: item[1])
    key = line[:index].strip().strip("：:；; ")
    value = line[index + len(separator) :].strip().strip("：:；; ")
    return key, value


def _normalize_key(key: str) -> str | None:
    cleaned = key.strip()
    for normalized_key, aliases in FIELD_ALIASES.items():
        if cleaned in aliases:
            return normalized_key
    return None
