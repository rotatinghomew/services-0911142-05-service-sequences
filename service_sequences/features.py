"""历程特征工程：把每户的原始接触记录转成可比较的历程表示。

口径要点（与 docs/data-contract.md 一致）：
- 同一 source_key 是重复上报，去重后只算一次服务事件，但保留重复计数作为数据质量特征；
- coverage 的 gap_start/gap_stop 是未观测区间，不能与覆盖内“有窗无服务”的沉默混为一谈；
- prior_history 表示系统上线前已使用服务，观察起点不按第一条接触重置；
- early/late 是观察长度不同的登记组，所有长度类特征都按各自观察窗归一化。
"""

SERVICE_TYPES = ("meal", "bath", "clean", "respite")
# 间隔分箱按天取对数边界，覆盖 1 天到 200 天，箱内为概率质量。
GAP_BIN_EDGES = (1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 34.0, 55.0, 89.0, 200.0)
LONG_GAP_DAYS = 21
STRUCTURE_ORDER = (
    "log_events", "meal_share", "bath_share", "clean_share", "respite_share",
    "gap_mean", "gap_cv", "gap_max_ratio", "long_gap_share",
    "log_window", "active_span_ratio", "leading_silence_ratio",
    "trailing_silence_ratio", "uncovered_ratio", "initial_delay_ratio",
    "duplicate_ratio", "prior_history",
)


def _dedup_events(contacts):
    """按 source_key 去重，保持 (day, contact) 次序；返回 (事件列表, 重复条数)。"""
    events, seen, duplicates = [], set(), 0
    for row in sorted(contacts, key=lambda item: (item["day"], item["contact"])):
        if row["source_key"] in seen:
            duplicates += 1
            continue
        seen.add(row["source_key"])
        events.append(row)
    return events, duplicates


def extract_journey(household, contacts, coverage_rows):
    """抽取单户历程。任何输入都不会被丢弃：零接触也是一种可分型的历程。"""
    events, duplicates = _dedup_events(contacts)
    window_start = min(row["start"] for row in coverage_rows)
    window_stop = max(row["stop"] for row in coverage_rows)
    window_length = max(1.0, float(window_stop - window_start))

    uncovered = 0.0
    for row in coverage_rows:
        if row["status"] == "partial" and row["gap_start"] is not None and row["gap_stop"] is not None:
            uncovered += max(0.0, float(row["gap_stop"] - row["gap_start"]))
    uncovered = min(uncovered, window_length)

    days = [float(row["day"]) for row in events]
    services = [row["service"] for row in events]
    gaps = [later - earlier for earlier, later in zip(days, days[1:])]

    if days:
        first_day, last_day = days[0], days[-1]
        leading_silence = max(0.0, first_day - window_start)
        trailing_silence = max(0.0, window_stop - last_day)
        active_span = max(0.0, last_day - first_day)
        initial_delay = max(0.0, first_day - float(household["registered_day"]))
    else:
        leading_silence = trailing_silence = window_length
        active_span = 0.0
        initial_delay = max(0.0, window_stop - float(household["registered_day"]))

    n_events = len(events)
    service_counts = {name: 0 for name in SERVICE_TYPES}
    for name in services:
        if name in service_counts:
            service_counts[name] += 1

    if gaps:
        gap_mean = sum(gaps) / len(gaps)
        gap_variance = sum((value - gap_mean) ** 2 for value in gaps) / len(gaps)
        gap_cv = (gap_variance ** 0.5) / gap_mean if gap_mean > 0 else 0.0
        gap_max = max(gaps)
        long_gap_share = sum(1 for value in gaps if value >= LONG_GAP_DAYS) / len(gaps)
    else:
        gap_mean = gap_cv = gap_max = long_gap_share = 0.0

    structure = {
        "log_events": _safe_log(n_events),
        "meal_share": service_counts["meal"] / n_events if n_events else 0.0,
        "bath_share": service_counts["bath"] / n_events if n_events else 0.0,
        "clean_share": service_counts["clean"] / n_events if n_events else 0.0,
        "respite_share": service_counts["respite"] / n_events if n_events else 0.0,
        "gap_mean": gap_mean / window_length,
        "gap_cv": gap_cv,
        "gap_max_ratio": gap_max / window_length,
        "long_gap_share": long_gap_share,
        "log_window": _safe_log(window_length),
        "active_span_ratio": active_span / window_length,
        "leading_silence_ratio": leading_silence / window_length,
        "trailing_silence_ratio": trailing_silence / window_length,
        "uncovered_ratio": uncovered / window_length,
        "initial_delay_ratio": initial_delay / window_length,
        "duplicate_ratio": duplicates / len(contacts) if contacts else 0.0,
        "prior_history": 1.0 if household["prior_history"] else 0.0,
    }
    return {
        "household": household["household"],
        "cohort": household["cohort"],
        "channel": household["channel"],
        "events": [
            {"day": row["day"], "service": row["service"], "duration": row["duration"]}
            for row in events
        ],
        "service_sequence": services,
        "gaps": gaps,
        "structure": structure,
        "window_start": window_start,
        "window_stop": window_stop,
        "window_length": window_length,
        "uncovered_days": uncovered,
        "raw_contact_count": len(contacts),
        "duplicate_count": duplicates,
    }


def extract_journeys(data):
    """对一份完整资料抽取全部户的历程，保持 households 表的稳定次序。"""
    contacts_by_household = {}
    coverage_by_household = {}
    for row in data["contacts"]:
        contacts_by_household.setdefault(row["household"], []).append(row)
    for row in data["coverage"]:
        coverage_by_household.setdefault(row["household"], []).append(row)

    journeys = []
    for household in data["households"]:
        hid = household["household"]
        coverage_rows = coverage_by_household.get(hid)
        if not coverage_rows:
            raise ValueError("缺少观察覆盖记录：" + hid)
        journeys.append(extract_journey(
            household, contacts_by_household.get(hid, []), coverage_rows))
    return journeys


def truncate_journeys(data, cutoff_day):
    """把接触截断到指定观察窗右端，用于窗长敏感性检验。

    覆盖表不修改：截断只模拟“晚期登记组能看到多长的窗”，未观测口径保持原样。
    """
    trimmed = {
        "households": data["households"],
        "coverage": data["coverage"],
        "contacts": [row for row in data["contacts"] if row["day"] <= cutoff_day],
    }
    return extract_journeys(trimmed)


def gap_histogram(gaps):
    """间隔概率质量向量；无间隔的户落在首个“近期接触”箱，不伪造为零。"""
    histogram = [0.0] * (len(GAP_BIN_EDGES) - 1)
    if not gaps:
        histogram[0] = 1.0
        return histogram
    for value in gaps:
        placed = False
        for index in range(len(histogram)):
            if GAP_BIN_EDGES[index] <= value < GAP_BIN_EDGES[index + 1]:
                histogram[index] += 1.0
                placed = True
                break
        if not placed:
            histogram[-1] += 1.0
    total = sum(histogram)
    return [value / total for value in histogram]


def structure_vector(journey):
    return [journey["structure"][key] for key in STRUCTURE_ORDER]


def _safe_log(value):
    import math
    return math.log1p(max(0.0, float(value)))
