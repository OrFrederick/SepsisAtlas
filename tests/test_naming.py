from sepsis_atlas.naming import year_from_stem


def test_year_from_bare_stem():
    assert year_from_stem("Baloch_2022") == 2022
    assert year_from_stem("Seymour_2016") == 2016


def test_year_from_pdf_filename():
    assert year_from_stem("Baloch_2022.pdf") == 2022
    assert year_from_stem("Zhang_2021.PDF") == 2021


def test_year_with_disambiguator_suffix():
    assert year_from_stem("Li_2020_a") == 2020
    assert year_from_stem("Zhang_2021_predictor.pdf") == 2021


def test_returns_none_when_no_year():
    assert year_from_stem("NoYearHere") is None
    assert year_from_stem("Author_99") is None
    assert year_from_stem("") is None
    assert year_from_stem(None) is None


def test_rejects_implausible_years():
    assert year_from_stem("Author_1700") is None
    assert year_from_stem("Author_2200") is None
