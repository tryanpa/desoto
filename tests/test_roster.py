from desoto.roster import merge, normalize_period, normalize_roster, parse_csv


def test_period_normalising():
    assert [normalize_period(x) for x in ["3", "Period 3", "P3", "pd. 03", " 7 "]] == ["3", "3", "3", "3", "7"]
    assert normalize_period("Lab") == "Lab"


def test_basic_csv_with_first_last():
    r, w, e = parse_csv("First,Last,Student ID,Period\nAva,Johnson,0012345,1\nBen,Carter,222,Period 2\n")
    assert not e and not w
    assert r == {"1": [{"name": "Ava Johnson", "id": "0012345"}], "2": [{"name": "Ben Carter", "id": "222"}]}


def test_last_comma_first_and_bom_and_semicolons():
    r, _, e = parse_csv("﻿Name;Barcode;Pd\n\"Carreras, Leticia M.\";4281405;7\n")
    assert not e and r["7"] == [{"name": "Leticia M. Carreras", "id": "4281405"}]


def test_default_period_and_missing_period_error():
    r, _, e = parse_csv("Name,ID\nAva Johnson,1\n", default_period="Period 4")
    assert not e and list(r) == ["4"]
    _, _, e = parse_csv("Name,ID\nAva Johnson,1\n")
    assert e and "Period" in e[0]


def test_missing_name_column():
    _, _, e = parse_csv("ID,Period\n1,1\n")
    assert e and "name" in e[0].lower()


def test_duplicates_and_blank_rows():
    text = "Name,ID,Period\nMaria Garcia,1,1\nMaria Garcia,2,1\nSomeone,1,1\n,5,1\nNo Id,,1\n"
    r, w, e = parse_csv(text)
    assert not e
    assert [s["name"] for s in r["1"]] == ["Maria Garcia", "Maria Garcia (2)", "No Id"]
    assert any("already listed" in x for x in w) and any("no name" in x for x in w) and any("no ID" in x for x in w)


def test_empty_file():
    assert parse_csv("")[2]


def test_normalize_legacy_and_merge():
    cur = normalize_roster({"1": ["A", {"name": "B", "id": "9"}]})
    assert cur["1"][0] == {"name": "A", "id": ""}
    out = merge(cur, {"1": [{"name": "B2", "id": "9"}, {"name": "C", "id": "3"}], "2": [{"name": "D", "id": "4"}]})
    assert [s["name"] for s in out["1"]] == ["A", "B2", "C"] and "2" in out
