import pytest

from romm_hub.netpolicy import PolicyViolation, check_url, host_matches, url_allowed

PATTERNS = ["archive.org", "*.archive.org"]


def test_exact_host_allowed():
    assert url_allowed("https://archive.org/advancedsearch.php", PATTERNS)


def test_subdomain_allowed_by_wildcard():
    assert url_allowed("https://ia801504.us.archive.org/file.zip", PATTERNS)


def test_unrelated_host_denied():
    assert not url_allowed("https://evil.com/steal", PATTERNS)


def test_suffix_confusion_denied():
    # The classic bug: naive endswith() lets this through.
    assert not url_allowed("https://archive.org.evil.com/", PATTERNS)


def test_userinfo_confusion_denied():
    # Real host here is evil.com, not archive.org.
    assert not url_allowed("https://archive.org@evil.com/", PATTERNS)


def test_query_string_confusion_denied():
    assert not url_allowed("https://evil.com/?x=archive.org", PATTERNS)


def test_host_is_case_insensitive():
    assert url_allowed("https://ARCHIVE.ORG/x", PATTERNS)


def test_http_scheme_denied():
    assert not url_allowed("http://archive.org/x", PATTERNS)


def test_file_scheme_denied():
    assert not url_allowed("file:///etc/passwd", PATTERNS)


def test_empty_patterns_deny_everything():
    assert not url_allowed("https://archive.org/x", [])


def test_wildcard_does_not_match_bare_domain():
    assert not host_matches("archive.org", "*.archive.org")


def test_wildcard_does_not_span_dots():
    assert not host_matches("a.b.archive.org.evil.com", "*.archive.org")


def test_check_url_raises_with_useful_message():
    with pytest.raises(PolicyViolation, match="evil.com"):
        check_url("https://evil.com/x", PATTERNS)


def test_malformed_url_denied():
    assert not url_allowed("not a url", PATTERNS)
