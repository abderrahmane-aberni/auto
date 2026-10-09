from app.dedupe import normalize_url


def test_strips_www_and_trailing_slash():
    assert normalize_url("https://www.example.com/jobs/123/") == normalize_url(
        "https://example.com/jobs/123"
    )


def test_strips_tracking_params():
    tracked = "https://example.com/jobs/123?utm_source=linkedin&utm_campaign=x&gclid=abc"
    plain = "https://example.com/jobs/123"
    assert normalize_url(tracked) == normalize_url(plain)


def test_keeps_meaningful_query_params():
    a = normalize_url("https://example.com/jobs?id=123")
    b = normalize_url("https://example.com/jobs?id=456")
    assert a != b


def test_query_param_order_does_not_matter():
    a = normalize_url("https://example.com/jobs?id=123&loc=uk")
    b = normalize_url("https://example.com/jobs?loc=uk&id=123")
    assert a == b


def test_scheme_and_host_case_insensitive():
    a = normalize_url("HTTPS://Example.COM/jobs/123")
    b = normalize_url("https://example.com/jobs/123")
    assert a == b


def test_different_paths_stay_different():
    a = normalize_url("https://example.com/jobs/123")
    b = normalize_url("https://example.com/jobs/456")
    assert a != b
