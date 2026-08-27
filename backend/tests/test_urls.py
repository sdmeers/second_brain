"""URL normalisation for duplicate-capture detection.

Regression cover for the bug where `url.split("?")[0]` discarded the entire query
string, collapsing every `youtube.com/watch?v=...` URL to `https://www.youtube.com/watch`
so that each new video was rejected as a duplicate of the first one ingested.
"""

import pytest

from backend.utils.urls import extract_youtube_id, normalize_url, urls_match

ALPHAGO = "https://www.youtube.com/watch?v=qoinGjj60Fo"
OTHER = "https://www.youtube.com/watch?v=tBjgCj_dGZM"


class TestYouTubeIsNotOverCollapsed:
    def test_different_videos_do_not_match(self):
        """The actual reported bug."""
        assert not urls_match(ALPHAGO, OTHER)
        assert normalize_url(ALPHAGO) != normalize_url(OTHER)

    def test_video_id_is_preserved(self):
        assert "qoinGjj60Fo" in normalize_url(ALPHAGO)

    def test_many_distinct_videos_yield_distinct_keys(self):
        ids = ["qoinGjj60Fo", "yuISv0s3I6A", "7xTGNNLPyMI", "8tEXn_UGPYA",
               "EWvNQjAaOHw", "zjkBMFhNj_g", "Rgwty6dGsYI", "kv-b6RFRbfI"]
        keys = {normalize_url(f"https://www.youtube.com/watch?v={i}") for i in ids}
        assert len(keys) == len(ids)


class TestYouTubeEquivalentForms:
    @pytest.mark.parametrize("variant", [
        "https://www.youtube.com/watch?v=qoinGjj60Fo",
        "https://youtube.com/watch?v=qoinGjj60Fo",
        "http://www.youtube.com/watch?v=qoinGjj60Fo",
        "https://m.youtube.com/watch?v=qoinGjj60Fo",
        "https://music.youtube.com/watch?v=qoinGjj60Fo",
        "https://youtu.be/qoinGjj60Fo",
        "https://youtu.be/qoinGjj60Fo?si=sharetoken",
        "https://www.youtube.com/embed/qoinGjj60Fo",
        "https://www.youtube.com/shorts/qoinGjj60Fo",
        "https://www.youtube.com/live/qoinGjj60Fo",
        "https://www.youtube.com/watch?v=qoinGjj60Fo&t=2069s",
        "https://www.youtube.com/watch?v=qoinGjj60Fo&list=PLxyz&index=4",
        "https://www.youtube.com/watch?v=qoinGjj60Fo&feature=share&ab_channel=Foo",
        "  https://www.youtube.com/watch?v=qoinGjj60Fo  ",
    ])
    def test_all_forms_of_one_video_match(self, variant):
        assert urls_match(variant, ALPHAGO), variant

    def test_canonical_form(self):
        assert normalize_url("https://youtu.be/qoinGjj60Fo") == \
            "https://youtube.com/watch?v=qoinGjj60Fo"


class TestExtractYouTubeId:
    @pytest.mark.parametrize("url,expected", [
        ("https://www.youtube.com/watch?v=qoinGjj60Fo", "qoinGjj60Fo"),
        ("https://youtu.be/qoinGjj60Fo", "qoinGjj60Fo"),
        ("https://www.youtube.com/shorts/qoinGjj60Fo", "qoinGjj60Fo"),
        ("https://www.youtube.com/embed/qoinGjj60Fo?autoplay=1", "qoinGjj60Fo"),
    ])
    def test_extracts(self, url, expected):
        assert extract_youtube_id(url) == expected

    @pytest.mark.parametrize("url", [
        "https://example.com/watch?v=qoinGjj60Fo",     # not youtube
        "https://www.youtube.com/watch?v=tooshort",    # wrong ID length
        "https://www.youtube.com/",                    # no video
        "https://www.youtube.com/@somechannel",        # a channel, not a video
        "https://www.youtube.com/results?search_query=ai",
        "",
    ])
    def test_returns_none(self, url):
        assert extract_youtube_id(url) is None


class TestGeneralUrls:
    def test_meaningful_query_params_are_kept(self):
        """The core mistake: query params can identify the document."""
        a = normalize_url("https://example.com/article?id=42")
        b = normalize_url("https://example.com/article?id=99")
        assert a != b
        assert "id=42" in a

    @pytest.mark.parametrize("tracking", [
        "utm_source=twitter", "utm_medium=social&utm_campaign=x",
        "fbclid=abc123", "gclid=xyz", "ref=newsletter", "si=token",
    ])
    def test_tracking_params_are_dropped(self, tracking):
        assert urls_match(f"https://example.com/post?{tracking}", "https://example.com/post")

    def test_param_order_does_not_matter(self):
        assert urls_match("https://example.com/x?a=1&b=2", "https://example.com/x?b=2&a=1")

    @pytest.mark.parametrize("a,b", [
        ("https://example.com/page", "https://www.example.com/page"),
        ("https://example.com/page", "https://example.com/page/"),
        ("https://example.com/page", "https://example.com/page#section"),
        ("https://EXAMPLE.com/page", "https://example.com/page"),
        ("http://example.com/page", "https://example.com/page"),
    ])
    def test_cosmetic_differences_match(self, a, b):
        assert urls_match(a, b)

    def test_different_paths_do_not_match(self):
        assert not urls_match("https://example.com/a", "https://example.com/b")

    def test_different_hosts_do_not_match(self):
        assert not urls_match("https://a.com/page", "https://b.com/page")


class TestEdgeCases:
    @pytest.mark.parametrize("value", ["", None])
    def test_empty_is_falsy_and_never_matches(self, value):
        assert normalize_url(value or "") == ""
        assert not urls_match(value or "", "https://example.com")

    def test_two_empty_urls_do_not_match(self):
        assert not urls_match("", "")

    def test_internal_placeholder_urls_stay_distinct(self):
        """The pipeline synthesises local.brain URLs for non-URL captures."""
        assert not urls_match("https://local.brain/bookmark/1", "https://local.brain/bookmark/2")

    def test_non_http_scheme_is_returned_lowercased(self):
        assert normalize_url("MAILTO:Someone@Example.com") == "mailto:someone@example.com"

    def test_garbage_does_not_raise(self):
        for junk in ("not a url", "://///", "http://[", "   "):
            normalize_url(junk)


class TestYouTubeDetectionInPipeline:
    """capture.py and fetch_cover previously tested for "youtube.com/watch" or
    "youtu.be/" only, so Shorts, embeds, live URLs and mobile hosts were treated as
    ordinary web pages -- no transcript extraction and no thumbnail."""

    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/shorts/qoinGjj60Fo",
        "https://www.youtube.com/embed/qoinGjj60Fo",
        "https://www.youtube.com/live/qoinGjj60Fo",
        "https://m.youtube.com/watch?v=qoinGjj60Fo",
        "https://youtu.be/qoinGjj60Fo",
    ])
    def test_all_youtube_forms_are_detected(self, url):
        assert extract_youtube_id(url) is not None, url

    @pytest.mark.parametrize("url", [
        "https://example.com/article",
        "https://vimeo.com/123456789",
        "https://www.youtube.com/@channel",
    ])
    def test_non_videos_are_not_detected(self, url):
        assert extract_youtube_id(url) is None, url
