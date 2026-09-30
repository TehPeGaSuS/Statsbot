"""i18n.py and locale/*.po: a bad translation must never break a page."""
import re
from datetime import datetime

import pytest

import i18n

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def entries(lang):
    return i18n._get_catalogue(lang).items()


@pytest.mark.parametrize("lang", i18n.SUPPORTED)
class TestCatalogues:
    def test_catalogue_loads_and_is_not_empty(self, lang):
        if lang != i18n.DEFAULT:
            assert len(i18n._get_catalogue(lang)) > 20

    def test_translations_use_the_same_placeholders_as_the_original(self, lang):
        bad = []
        for msgid, (msgstr, forms) in entries(lang):
            wanted = set(PLACEHOLDER.findall(msgid))
            for text in ([msgstr] if msgstr else []):
                if set(PLACEHOLDER.findall(text)) != wanted:
                    bad.append((msgid, text))
        assert not bad, f"placeholder mismatch: {bad[:3]}"

    def test_plural_forms_only_use_known_placeholders(self, lang):
        bad = []
        for msgid, (msgstr, forms) in entries(lang):
            known = set(PLACEHOLDER.findall(msgid)) | {"count"}
            for form in forms or []:
                if form and not set(PLACEHOLDER.findall(form)) <= known:
                    bad.append((msgid, form))
        assert not bad, f"unknown placeholder in a plural form: {bad[:3]}"

    def test_month_and_weekday_lists_have_the_right_length(self, lang):
        assert len(i18n._get_list(i18n._EN_MONTHS, lang)) == 12
        assert len(i18n._get_list(i18n._EN_WEEKDAYS, lang)) == 7

    def test_long_dates_format(self, lang):
        assert "2026" in i18n.format_date_long(datetime(2026, 10, 7, 12, 30), lang)


class TestLookup:
    def test_missing_translation_falls_back_to_the_english_text(self):
        assert i18n.t("Text that no catalogue knows", "fr_FR") == "Text that no catalogue knows"

    def test_placeholders_are_filled(self):
        assert i18n.t("No quotes for {nick}.", "en_US", nick="bob") == "No quotes for bob."

    def test_a_missing_placeholder_value_does_not_crash(self):
        assert "{nick}" in i18n.t("No quotes for {nick}.", "en_US")

    def test_plural_rule_for_english(self):
        assert i18n.tn("{count} line", "{count} lines", 1, "en_US") == "1 line"
        assert i18n.tn("{count} line", "{count} lines", 5, "en_US") == "5 lines"

    def test_unsupported_language_is_never_stored(self, db):
        assert i18n.set_lang("n", "#c", "xx_XX") is False
        assert i18n.get_lang("n", "#c") == i18n.DEFAULT

    def test_language_choice_is_per_channel(self, db):
        assert i18n.set_lang("n", "#a", "fr_FR")
        assert i18n.get_lang("n", "#a") == "fr_FR" and i18n.get_lang("n", "#b") == i18n.DEFAULT
