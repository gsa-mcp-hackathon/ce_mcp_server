"""The general rules in ce_sidecars.rules, one test per rule, run through
build_properties so the order against the generator and the overrides is real."""

from ce_sidecars.dictionary import FileDictionary, Variable
from ce_sidecars.generate import StoredColumn, build_properties


def _run(specs, overrides=None, level="consumer_unit"):
    """specs: (name, db_type, dictionary description or None)."""
    d = FileDictionary(file="FMLI", release=2025)
    for name, _, desc in specs:
        if desc is not None:
            d.variables[name] = Variable(name, name.upper(), desc, None, None, None)
    columns = [StoredColumn(n, t, n.upper()) for n, t, _ in specs]
    props, review = build_properties(columns, d, overrides, level=level)
    return {p["name"]: p for p in props}, review


def _custom(p):
    return {cp["property"]: cp["value"] for cp in p["customProperties"]}


def test_quarter_pairs():
    p, _ = _run(
        [("fdawaypq", "DOUBLE", "Food away last quarter"),
         ("fdawaycq", "DOUBLE", "Food away this quarter"),
         ("ttotalp", "DOUBLE", "Trips last quarter"),
         ("ttotalc", "DOUBLE", "Trips this quarter"),
         ("etotapx4", "DOUBLE", "Outlays last quarter"),
         ("etotacx4", "DOUBLE", "Outlays this quarter"),
         ("lonep", "DOUBLE", None)]  # no partner: not a pair
    )  # fmt: skip
    assert _custom(p["fdawaypq"]) | {} == {
        "ce:sourceName": "FDAWAYPQ",
        "ce:role": "measure",
        "ce:scale": "ratio",
        "ce:unit": "usd_quarterly",
        "ce:level": "consumer_unit",
        "ce:additiveOver": ["consumer_units", "months_in_scope"],
        "ce:calendarPair": "fdawaycq",
    }
    assert _custom(p["ttotalc"])["ce:calendarPair"] == "ttotalp"
    assert _custom(p["etotapx4"])["ce:calendarPair"] == "etotacx4"
    assert "ce:role" not in _custom(p["lonep"])


def test_columns_never_share_a_list():
    """A list shared by two columns is written to YAML once and then as a
    reference (*id001), which a reader of the text cannot follow."""
    import yaml

    p, _ = _run(
        [("fdawaypq", "DOUBLE", "Food away last quarter"),
         ("fdawaycq", "DOUBLE", "Food away this quarter"),
         ("ttotalp", "DOUBLE", "Trips last quarter"),
         ("ttotalc", "DOUBLE", "Trips this quarter"),
         ("fincbtax", "DOUBLE", "Amount of income, past 12 months"),
         ("fincbtx1", "DOUBLE", "Amount of income, past 12 months"),
         ("fincbtx2", "DOUBLE", "Amount of income, past 12 months"),
         ("fincb_xm", "VARCHAR", None),
         ("fincb_x1", "VARCHAR", None)]
    )  # fmt: skip
    text = yaml.safe_dump(list(p.values()), sort_keys=False)
    assert "&id" not in text and "*id" not in text


def test_imputation_set_inherits_from_its_base_after_overrides():
    specs = [("fsalaryx", "DOUBLE", "Amount of salary in the past 12 months")]
    specs += [
        (f"fsalary{i}", "DOUBLE", f"Imputation Iteration #{i} - FSALARYX")
        for i in range(1, 6)
    ]
    specs += [
        ("fsalarym", "DOUBLE", "Salary, mean of imputations"),
        ("fsalaryi", "DOUBLE", "Indicator/descriptor variable for income imputation"),
        # numbered like a set, but the dictionary doesn't call them iterations
        ("as_comp1", "DOUBLE", "Number of males age 16 and over in CU"),
        ("as_comp2", "DOUBLE", "Number of females age 16 and over in CU"),
    ]
    overrides = {"fsalaryx": {"customProperties": {"ce:level": "member"}}}
    p, review = _run(specs, overrides)
    base = _custom(p["fsalaryx"])
    assert (base["ce:unit"], base["ce:level"]) == ("usd_annual", "member")
    for m in ["fsalary1", "fsalary5", "fsalarym"]:
        c = _custom(p[m])
        assert c["ce:role"] == "measure" and c["ce:unit"] == "usd_annual"
        assert c["ce:level"] == "member"  # the override on the base carries over
        assert c["ce:imputed"] == {"multiple": True, "iterations": 5}
    indicator = _custom(p["fsalaryi"])
    assert (indicator["ce:role"], indicator["ce:flagFor"]) == ("flag", "fsalaryx")
    assert review.items["Imputation indicator: code meanings not described"] == [
        "fsalaryi"
    ]
    assert _custom(p["as_comp1"])["ce:unit"] == "count"
    assert "ce:imputed" not in _custom(p["as_comp1"])


def test_imputation_set_base_from_the_stem_when_the_dictionary_misnames_it():
    specs = [("intrdvx", "DOUBLE", "Amount of interest and dividends, past year")]
    specs += [
        (f"intrdvx{i}", "DOUBLE", f"Imputation Iteration #{i} - INTRDIVX")
        for i in range(1, 6)
    ]
    p, _ = _run(specs)
    assert _custom(p["intrdvx3"])["ce:unit"] == "usd_annual"


def test_imputation_set_without_a_base_is_reviewed():
    specs = [
        (f"fs_amtx{i}", "DOUBLE", f"Imputation Iteration #{i} - FS_AMTX")
        for i in range(1, 6)
    ] + [("fs_amtxi", "DOUBLE", "Indicator/descriptor variable for income imputation")]
    p, review = _run(specs)
    assert review.items["Imputation set: base column not found"] == [
        f"fs_amtx{i}" for i in range(1, 6)
    ]
    assert _custom(p["fs_amtxi"])["ce:role"] == "flag"
    assert "ce:flagFor" not in _custom(p["fs_amtxi"])
    assert "review:verify" in p["fs_amtxi"]["tags"]


def test_bracket_median():
    p, _ = _run([("intrdvbx", "DOUBLE", "Median of Bracket Range")])
    c = _custom(p["intrdvbx"])
    assert (c["ce:role"], c["ce:unit"]) == ("measure", "VERIFY")


def test_counts_and_open_units_and_levels():
    p, review = _run(
        [("vehq", "DOUBLE", "Total number of owned vehicles"),
         ("inc_hrs2", "DOUBLE", "Number of hours usually worked per week by spouse"),
         ("perslt18", "DOUBLE", "# of CU Members less than 18")]
    )  # fmt: skip
    assert _custom(p["vehq"])["ce:unit"] == "count"
    assert _custom(p["vehq"])["ce:level"] == "consumer_unit"
    assert _custom(p["perslt18"])["ce:unit"] == "count"
    # no hours unit in the vocabulary; a spouse's value: level still open
    assert _custom(p["inc_hrs2"])["ce:unit"] == "VERIFY"
    assert _custom(p["inc_hrs2"])["ce:level"] == "VERIFY"
    assert review.items["Still VERIFY: ce:unit"] == ["inc_hrs2"]


def test_dollar_amounts():
    p, _ = _run(
        [("fgovretm", "DOUBLE", "Total amount of government retirement deducted"),
         ("creditx", "DOUBLE", "What is the total amount owed on all cards?"),
         ("misctaxx", "DOUBLE", "Other taxes paid during past 12 months"),
         ("fsmpfrmx", "DOUBLE", "Self-employment income before deductions")]
    )  # fmt: skip
    assert _custom(p["misctaxx"])["ce:unit"] == "usd_annual"
    # an annual amount repeats on every interview: never summed across them
    assert _custom(p["misctaxx"])["ce:additiveOver"] == ["consumer_units"]
    assert "ce:additiveOver" not in _custom(p["creditx"])  # unit still open
    assert _custom(p["creditx"])["ce:unit"] == "VERIFY"  # a balance: no unit yet
    assert _custom(p["fgovretm"])["ce:role"] == "measure"  # by its text
    assert _custom(p["fsmpfrmx"])["ce:role"] == "measure"  # by its name, ending x


def test_rules_never_overwrite_the_generator_or_overrides():
    overrides = {"ttotalp": {"customProperties": {"ce:role": "dimension"}}}
    p, _ = _run(
        [("ttotalp", "DOUBLE", "Trips last quarter"),
         ("ttotalc", "DOUBLE", "Trips this quarter"),
         ("finlwt21", "DOUBLE", "Final weight, amount of units")]
    , overrides)  # fmt: skip
    assert _custom(p["ttotalp"])["ce:role"] == "dimension"
    assert _custom(p["finlwt21"])["ce:role"] == "weight"
    assert "ce:unit" not in _custom(p["finlwt21"])


def test_imputation_set_inherits_top_coding_and_additivity():
    specs = [("fincbtax", "DOUBLE", "Total amount of income in the last 12 months")]
    specs += [
        (f"fincbtx{i}", "DOUBLE", f"Imputation Iteration #{i} - FINCBTAX")
        for i in range(1, 6)
    ]
    specs += [("fincbtxm", "DOUBLE", "Income before taxes, mean of imputations")]
    top = {
        "ce:topcoded": True,
        "ce:topcodeMethod": "replaced_by_mean_above_critical_value",
    }
    p, _ = _run(specs, {"fincbtax": {"customProperties": top}})
    assert _custom(p["fincbtax"])["ce:additiveOver"] == ["consumer_units"]
    for m in ["fincbtx1", "fincbtx5", "fincbtxm"]:
        c = _custom(p[m])
        assert c["ce:topcoded"] is True
        assert c["ce:topcodeMethod"] == "replaced_by_mean_above_critical_value"
        assert c["ce:additiveOver"] == ["consumer_units"]


def test_inherited_values_do_not_replace_a_decision_on_the_member():
    specs = [("fincbtax", "DOUBLE", "Total amount of income in the last 12 months")]
    specs += [
        (f"fincbtx{i}", "DOUBLE", f"Imputation Iteration #{i} - FINCBTAX")
        for i in range(1, 6)
    ]
    overrides = {
        "fincbtax": {"customProperties": {"ce:topcoded": True}},
        "fincbtx3": {"customProperties": {"ce:topcoded": False}},
    }
    p, _ = _run(specs, overrides)
    assert _custom(p["fincbtx3"])["ce:topcoded"] is False
    assert _custom(p["fincbtx4"])["ce:topcoded"] is True


def test_level_comes_from_the_table():
    specs = [
        ("salaryx", "DOUBLE", "Amount of salary in the past 12 months"),
        ("inc_hrsq", "DOUBLE", "Number of hours usually worked per week"),
        ("earnq", "DOUBLE", "Number of jobs held by the reference person"),
        ("socrrx", "DOUBLE", "Median of Bracket Range"),
        ("ttotalp", "DOUBLE", "Trips last quarter"),
        ("ttotalc", "DOUBLE", "Trips this quarter"),
    ]
    p, _ = _run(specs, level="member")
    for name, _, _ in specs:
        assert _custom(p[name])["ce:level"] == "member", name
    # in a consumer-unit table, a column about one person leaves the level open
    p, _ = _run(specs)
    assert _custom(p["salaryx"])["ce:level"] == "consumer_unit"
    assert _custom(p["earnq"])["ce:level"] == "VERIFY"


def test_a_name_pair_that_is_not_stored_as_a_pair_is_not_a_quarter_pair():
    p, _ = _run(
        [("occup", "DOUBLE", "Occupation group"),
         ("regionc", "DOUBLE", "Region group"),
         ("statusp", "DOUBLE", None)]
    )  # fmt: skip
    for name in ("occup", "regionc", "statusp"):
        assert "ce:calendarPair" not in _custom(p[name]), name
