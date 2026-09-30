"""bot/parser.py: the counting rules behind every statistic."""
import pytest

from bot import parser


class TestControlCodes:
    def test_bold_and_reset_are_removed(self):
        assert parser.strip_controls("\x02bold\x0f text") == "bold text"

    def test_colour_codes_with_and_without_background_are_removed(self):
        assert parser.strip_controls("\x0304red\x03 \x0304,12redonblue\x03") == "red redonblue"

    def test_words_are_counted_after_stripping(self):
        # "\x02hi\x02" is one word, not something the counter trips on
        assert parser.parse_message("\x02hi\x02 there", [])["words"] == 2


class TestSmileys:
    def test_smiley_at_end_of_line(self):
        assert parser.count_smileys("nice one :)", [":)"]) == 1

    def test_smiley_followed_by_punctuation(self):
        assert parser.count_smileys("nice :), really", [":)"]) == 1

    def test_smiley_inside_url_is_not_counted(self):
        # the classic false positive: ":/" in "https://"
        assert parser.count_smileys("see https://example.com now", [":/"]) == 0

    def test_smiley_glued_to_a_word_is_not_counted(self):
        assert parser.count_smileys("a:)b", [":)"]) == 0

    def test_several_smileys_on_one_line(self):
        assert parser.count_smileys(":) :) :D", [":)", ":D"]) == 3

    def test_sad_smileys_counted_separately(self):
        assert parser.count_sad("oh no :(", [":("]) == 1
        assert parser.count_smileys("oh no :(", [":)"]) == 0

    def test_per_smiley_frequency(self):
        assert parser.count_specific_smileys(":) :) :D", [":)", ":D", ":("]) == {":)": 2, ":D": 1}


class TestQuestionsAndCaps:
    def test_a_line_counts_as_one_question_however_many_marks(self):
        assert parser.count_questions("what??? really???") == 1

    def test_no_question_mark_no_question(self):
        assert parser.count_questions("hello") == 0

    def test_shouting_needs_mostly_uppercase(self):
        assert parser.is_all_caps("STOP SHOUTING AT ME")
        assert not parser.is_all_caps("Stop shouting at me")

    def test_short_words_are_never_shouting(self):
        # fewer than 4 letters: "OK", "LOL" must not count as SHOUTING
        assert not parser.is_all_caps("OK")
        assert not parser.is_all_caps("LOL")

    def test_digits_and_punctuation_do_not_dilute_the_ratio(self):
        assert parser.is_all_caps("WHAT?!?! 12345 !!!")


class TestWords:
    def test_words_are_lowercase_agnostic_but_keep_original_casing(self):
        assert parser.extract_words("Hello WORLD", min_length=3) == ["Hello", "WORLD"]

    def test_short_words_are_dropped(self):
        assert parser.extract_words("a an the house", min_length=4) == ["house"]

    def test_surrounding_punctuation_is_stripped(self):
        assert parser.extract_words('"hello," (world)!', min_length=3) == ["hello", "world"]

    def test_numbers_alone_are_not_words(self):
        assert parser.extract_words("12345 house", min_length=3) == ["house"]

    def test_urls_are_not_words(self):
        assert parser.extract_words("look https://example.com/page now", min_length=3) == ["look", "now"]

    def test_min_length_zero_does_not_crash(self):
        assert "x" in parser.extract_words("x", min_length=0)


class TestUrls:
    def test_http_and_www(self):
        found = parser.extract_urls("a http://a.example/x and www.b.example")
        assert found == ["http://a.example/x", "www.b.example"]

    def test_no_url(self):
        assert parser.extract_urls("just words") == []


class TestNickReferences:
    def test_plain_mention(self):
        assert parser.find_nick_refs("hey bob how are you", ["bob", "carol"]) == ["bob"]

    def test_mention_with_colon_and_case(self):
        assert parser.find_nick_refs("BOB: ping", ["bob"]) == ["bob"]

    def test_nick_that_is_only_a_substring_is_not_a_mention(self):
        assert parser.find_nick_refs("hello bobby", ["bob"]) == []


class TestFoulAndViolent:
    def test_foul_word_with_punctuation_and_case(self):
        assert parser.count_foul("Damn! that hurt", ["damn"]) == 1

    def test_violent_word_anywhere(self):
        assert parser.count_violent("slaps bob around", ["slaps"]) == 1


class TestParseMessage:
    def test_letters_counts_characters_of_the_cleaned_line(self):
        assert parser.parse_message("hello world", [])["letters"] == len("hello world")

    def test_all_keys_present(self):
        keys = set(parser.parse_message("x", []))
        assert keys == {"words", "letters", "smileys", "sad", "smiley_freq", "questions",
                        "caps", "violent", "foul", "nick_refs", "urls", "word_list"}

    def test_words_per_line(self):
        assert parser.words_per_line(10, 4) == 2.5
        assert parser.words_per_line(10, 0) == 0.0
