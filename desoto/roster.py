"""Roster parsing and normalisation.

On disk and in memory the roster is {period: [{"name": ..., "id": ...}]}. IDs are private:
only admin endpoints ever see them (see PassState.snapshot).
"""
import csv
import io
import re
from typing import Dict, List, Tuple

Roster = Dict[str, List[dict]]

NAME_KEYS = {"name", "studentname", "fullname", "student"}
FIRST_KEYS = {"first", "firstname", "givenname"}
LAST_KEYS = {"last", "lastname", "surname", "familyname"}
ID_KEYS = {"id", "studentid", "studentnumber", "idnumber", "barcode", "number", "sid"}
PERIOD_KEYS = {"period", "classperiod", "pd", "per", "hour"}


def normalize_period(value: str) -> str:
    v = value.strip()
    m = re.match(r"^(?:period|per|pd|p)[\s.#:-]*(.+)$", v, re.I)
    if m:
        v = m.group(1).strip()
    return str(int(v)) if v.isdigit() else v


def period_sort_key(p: str):
    return (0, int(p), "") if p.isdigit() else (1, 0, p)


def normalize_roster(data) -> Roster:
    """Accept {period: [name | {name, id}]} and return the canonical form."""
    out: Roster = {}
    for period, students in data.items():
        out[str(period)] = [
            {"name": s, "id": ""} if isinstance(s, str)
            else {"name": str(s["name"]), "id": str(s.get("id", ""))}
            for s in students]
    return dict(sorted(out.items(), key=lambda kv: period_sort_key(kv[0])))


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _key(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.lower())


def parse_csv(text: str, default_period: str = "") -> Tuple[Roster, List[str], List[str]]:
    """Parse a roster CSV. Returns (roster, warnings, errors); errors mean nothing usable."""
    text = text.lstrip("﻿")
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        return {}, [], ["The file is empty."]
    delim = max(",;\t", key=lines[0].count)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any(c.strip() for c in r)]
    headers = [_key(h) for h in rows[0]]

    def col(keys):
        return next((i for i, h in enumerate(headers) if h in keys), None)

    c_name, c_first, c_last = col(NAME_KEYS), col(FIRST_KEYS), col(LAST_KEYS)
    c_id, c_period = col(ID_KEYS), col(PERIOD_KEYS)
    errors = []
    if c_name is None and (c_first is None or c_last is None):
        errors.append("Couldn't find a name column. Use a header like Name, or First and Last.")
    default_period = normalize_period(default_period) if default_period else ""
    if c_period is None and not default_period:
        errors.append("Couldn't find a Period column. Add one, or enter a period for the whole file.")
    if errors:
        return {}, [], errors

    warnings: List[str] = []
    roster: Roster = {}
    seen_ids = set()
    id_names: Dict[str, str] = {}
    names_in_period: Dict[str, Dict[str, int]] = {}
    get = lambda row, i: row[i] if i is not None and i < len(row) else ""
    for n, row in enumerate(rows[1:], start=2):
        if c_name is not None:
            name = _clean(get(row, c_name))
            if name.count(",") == 1:  # "Last, First" -> "First Last"
                last, first = (_clean(x) for x in name.split(","))
                name = f"{first} {last}".strip()
        else:
            name = _clean(f"{get(row, c_first)} {get(row, c_last)}")
        if not name:
            warnings.append(f"Row {n}: no name, skipped.")
            continue
        period = normalize_period(get(row, c_period)) if c_period is not None else default_period
        if not period:
            period = default_period
        if not period:
            warnings.append(f"Row {n} ({name}): no period, skipped.")
            continue
        sid = _clean(get(row, c_id))
        if sid and (period, sid) in seen_ids:
            warnings.append(f"Row {n} ({name}): ID {sid} already listed in period {period}, skipped.")
            continue
        if sid:
            seen_ids.add((period, sid))
            if id_names.setdefault(sid, name) != name:
                warnings.append(f"Row {n}: ID {sid} is also used by {id_names[sid]}. Scanning it would pick the first one.")
        counts = names_in_period.setdefault(period, {})
        counts[name] = counts.get(name, 0) + 1
        if counts[name] > 1:
            renamed = f"{name} ({counts[name]})"
            warnings.append(f"Row {n}: {name} appears twice in period {period}; second is listed as '{renamed}'.")
            name = renamed
        if not sid:
            warnings.append(f"Row {n} ({name}): no ID.")
        roster.setdefault(period, []).append({"name": name, "id": sid})
    if not roster:
        return {}, warnings, ["No students found in the file."]
    return dict(sorted(roster.items(), key=lambda kv: period_sort_key(kv[0]))), warnings, []


def merge(current: Roster, incoming: Roster) -> Roster:
    """Add or update students: match on (period, id) when there is an ID, else (period, name)."""
    out = {p: [dict(s) for s in students] for p, students in current.items()}
    for period, students in incoming.items():
        mine = out.setdefault(period, [])
        for s in students:
            hit = next((m for m in mine if (s["id"] and m["id"] == s["id"]) or (not s["id"] and m["name"] == s["name"])), None)
            if hit:
                hit["name"] = s["name"]
            else:
                mine.append(dict(s))
    return dict(sorted(out.items(), key=lambda kv: period_sort_key(kv[0])))


def keys(roster: Roster):
    return {(p, s["name"]) for p, students in roster.items() for s in students}
