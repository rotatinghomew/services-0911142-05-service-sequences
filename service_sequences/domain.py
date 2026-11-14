from .validation import require_tables, unique, finite, references


SCHEMA = {'households': {'household': ['str'], 'cohort': ['str'], 'channel': ['str'], 'registered_day': ['number'], 'prior_history': ['boolean'], 'end_day': ['number']}, 'contacts': {'contact': ['str'], 'household': ['str'], 'day': ['number'], 'service': ['str'], 'duration': ['number'], 'reported_at': ['number'], 'source_key': ['str']}, 'coverage': {'span': ['str'], 'household': ['str'], 'start': ['number'], 'stop': ['number'], 'status': ['str'], 'gap_start': ['null', 'number'], 'gap_stop': ['null', 'number']}}
OPTIONS = {'cohort': ['early', 'late'], 'channel': ['all', 'community', 'home']}


def validate_structure(data, schema):
    require_tables(data, list(schema))
    for table, fields in schema.items():
        for row in data[table]:
            if not isinstance(row, dict) or any(field not in row for field in fields):
                raise ValueError("表字段缺失：" + table)
            for field, kinds in fields.items():
                value = row[field]
                kind = "null" if value is None else ("boolean" if isinstance(value,bool) else ("number" if isinstance(value,(int,float)) else type(value).__name__))
                if kind not in kinds:
                    raise ValueError("字段类型错误：" + table + "." + field)
                if kind == "number":
                    finite(value)


def validate(data):
    validate_structure(data, SCHEMA)
    unique(data['households'], ['household'])
    unique(data['contacts'], ['contact'])
    unique(data['coverage'], ['span'])
    references(data['contacts'], 'household', data['households'], 'household')
    references(data['coverage'], 'household', data['households'], 'household')
    for row in data["coverage"]:
        if row["stop"] <= row["start"] or row["status"] not in {"partial","complete"}:
            raise ValueError("观察覆盖无效")
    for row in data["contacts"]:
        finite(row["duration"], minimum=0)


def view(rows, measure, note):
    return {"summary":{"明细条数":len(rows),"有数值记录":sum(isinstance(row.get(measure),(int,float)) for row in rows)},
            "rows":rows,"series":[{"label":str(position+1),"y":row.get(measure)} for position,row in enumerate(rows)],
            "notes":[note]}


def explore(data, selection):
    for key, choices in OPTIONS.items():
        if selection.get(key) not in choices:
            raise ValueError("选择范围无效：" + key)
    households = {row["household"]:row for row in data["households"] if row["cohort"] == selection["cohort"] and (selection["channel"] == "all" or row["channel"] == selection["channel"])}
    rows = []
    for household,details in households.items():
        contacts = sorted([row for row in data["contacts"] if row["household"] == household], key=lambda row:(row["day"],row["contact"]))
        rows.append({"household":household,"contacts":len(contacts),"services":[row["service"] for row in contacts],"days":[row["day"] for row in contacts],"prior_history":details["prior_history"]})
    return view(rows, "contacts", "上报次数包含重复来源，短历程不是已确认的独立类型。")
