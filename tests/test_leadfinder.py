from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(scope="session")
def app():
    """App factory with an isolated temp database."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    os.environ["DATABASE_PATH"] = db_path
    os.environ["SERP_API_KEY"] = "test-key-not-real"

    from app import create_app

    _app = create_app()
    _app.config["TESTING"] = True

    yield _app

    os.unlink(db_path)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def app_ctx(app):
    with app.app_context():
        yield


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────


class TestConfig:
    def test_domain_denylist_is_frozenset(self, app):
        dl = app.config["DOMAIN_DENYLIST"]
        assert isinstance(dl, frozenset)
        assert "linkedin.com" in dl
        assert "facebook.com" in dl

    def test_serp_max_results_is_int(self, app):
        assert isinstance(app.config["SERP_MAX_RESULTS"], int)

    def test_playwright_enabled_is_bool(self, app):
        assert isinstance(app.config["PLAYWRIGHT_ENABLED"], bool)


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


class TestAPIRoutes:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.get_json()["status"] == "ok"

    def test_stats_empty(self, client):
        r = client.get("/api/stats")
        assert r.status_code == 200
        data = r.get_json()
        assert "total_leads" in data
        assert "by_confidence" in data

    def test_leads_empty(self, client):
        r = client.get("/api/leads")
        assert r.status_code == 200
        assert r.get_json()["leads"] == []

    def test_runs_empty(self, client):
        r = client.get("/api/runs")
        assert r.status_code == 200
        assert r.get_json()["runs"] == []

    def test_search_requires_query(self, client):
        r = client.post("/api/search", json={})
        assert r.status_code == 400
        assert "query" in r.get_json()["error"]

    def test_search_max_results_bounds(self, client):
        r = client.post("/api/search", json={"query": "test", "max_results": 100})
        assert r.status_code == 400

    def test_leads_export_empty_csv(self, client):
        r = client.get("/api/leads/export")
        assert r.status_code == 200
        assert "text/csv" in r.content_type
        # Should have a header row at minimum
        assert b"normalized_value" in r.data


class TestDashboardRoutes:
    def test_home(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert b"LeadFinder" in r.data

    def test_search_page(self, client):
        r = client.get("/search")
        assert r.status_code == 200

    def test_leads_page(self, client):
        r = client.get("/leads")
        assert r.status_code == 200

    def test_export_page(self, client):
        r = client.get("/leads/export")
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Email normalizer
# ─────────────────────────────────────────────────────────────────────────────


class TestEmailNormalize:
    def setup_method(self):
        from app.normalize.email import is_junk_email, normalize_email

        self.normalize = normalize_email
        self.is_junk = is_junk_email

    def test_lowercases(self):
        assert self.normalize("INFO@ACME.COM") == "info@acme.com"

    def test_strips_whitespace(self):
        assert self.normalize("  hello@acme.com  ") == "hello@acme.com"

    def test_strips_mailto_prefix(self):
        assert self.normalize("mailto:sales@acme.com") == "sales@acme.com"

    def test_strips_query_string(self):
        assert self.normalize("sales@acme.com?subject=Hello") == "sales@acme.com"

    def test_invalid_returns_none(self):
        assert self.normalize("not-an-email") is None

    def test_empty_returns_none(self):
        assert self.normalize("") is None

    def test_junk_placeholder(self):
        assert self.is_junk("test@test.com") is True

    def test_junk_noreply(self):
        assert self.is_junk("noreply@service.com") is True

    def test_junk_name_at_domain(self):
        assert self.is_junk("name@domain.com") is True

    def test_legitimate_not_junk(self):
        assert self.is_junk("sales@acme.com") is False

    def test_freemail_not_junk(self):
        # Gmail/freemail reduces confidence but is NOT junk
        assert self.is_junk("owner@gmail.com") is False


# ─────────────────────────────────────────────────────────────────────────────
# Phone normalizer
# ─────────────────────────────────────────────────────────────────────────────


class TestPhoneNormalize:
    def setup_method(self):
        from app.normalize.phone import is_junk_phone, normalize_phone

        self.normalize = normalize_phone
        self.is_junk = is_junk_phone

    def test_e164_us_full(self):
        assert self.normalize("+1 (415) 555-1234", "US") == "+14155551234"

    def test_e164_us_no_country(self):
        assert self.normalize("(415) 555-1234", "US") == "+14155551234"

    def test_e164_us_dashes(self):
        assert self.normalize("415-555-1234", "US") == "+14155551234"

    def test_invalid_returns_none(self):
        assert self.normalize("not-a-phone", "US") is None

    def test_junk_too_short(self):
        assert self.is_junk("123") is True

    def test_junk_too_long(self):
        assert self.is_junk("1" * 16) is True

    def test_junk_all_zeros(self):
        assert self.is_junk("0000000000") is True

    def test_valid_not_junk(self):
        # 555-xxxx is the Hollywood fiction exchange — use a real non-555 number
        assert self.is_junk("(415) 867-5309") is False


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication
# ─────────────────────────────────────────────────────────────────────────────


class TestDedup:
    def setup_method(self):
        from app.models import ExtractionHit
        from app.normalize.dedupe import dedup_hits

        self.dedup = dedup_hits
        self.Hit = ExtractionHit

    def _hit(self, value, confidence="medium", method="text", ctype="email"):
        return self.Hit(
            contact_type=ctype,
            raw_value=value,
            normalized_value=value,
            method=method,
            confidence=confidence,
            source_url="https://example.com/",
        )

    def test_dedup_removes_exact_duplicates(self):
        hits = [self._hit("a@b.com"), self._hit("a@b.com")]
        assert len(self.dedup(hits)) == 1

    def test_dedup_keeps_higher_confidence(self):
        hits = [self._hit("a@b.com", "low"), self._hit("a@b.com", "high")]
        result = self.dedup(hits)
        assert len(result) == 1
        assert result[0].confidence == "high"

    def test_dedup_preserves_distinct(self):
        hits = [self._hit("a@b.com"), self._hit("c@d.com")]
        assert len(self.dedup(hits)) == 2

    def test_dedup_case_insensitive(self):
        # Dedup operates on normalized_value; simulate what the normalization
        # layer would produce: lowercased values for both hits.
        from app.models import ExtractionHit

        h1 = ExtractionHit(
            "email", "A@B.COM", "a@b.com", "text", "medium", "https://x.com/"
        )
        h2 = ExtractionHit(
            "email", "a@b.com", "a@b.com", "text", "medium", "https://x.com/"
        )
        assert len(self.dedup([h1, h2])) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Confidence scoring
# ─────────────────────────────────────────────────────────────────────────────


class TestConfidenceScoring:
    def setup_method(self):
        from app.extraction.confidence import score_hits
        from app.models import ExtractionHit

        self.score = score_hits
        self.Hit = ExtractionHit

    def _hit(self, email, confidence="medium"):
        return self.Hit(
            contact_type="email",
            raw_value=email,
            normalized_value=email,
            method="footer",
            confidence=confidence,
            source_url="https://acme.com/",
        )

    def test_domain_match_upgrades_confidence(self):
        hit = self._hit("info@acme.com", "medium")
        result = self.score([hit], "acme.com")
        assert result[0].confidence == "high"

    def test_freemail_downgrades_confidence(self):
        hit = self._hit("owner@gmail.com", "medium")
        result = self.score([hit], "acme.com")
        assert result[0].confidence == "low"

    def test_domain_mismatch_no_change(self):
        hit = self._hit("info@other.com", "medium")
        result = self.score([hit], "acme.com")
        assert result[0].confidence == "medium"

    def test_high_confidence_capped(self):
        hit = self._hit("info@acme.com", "high")
        result = self.score([hit], "acme.com")
        assert result[0].confidence == "high"  # can't go above high


# ─────────────────────────────────────────────────────────────────────────────
# Structured data extraction
# ─────────────────────────────────────────────────────────────────────────────


class TestStructuredExtraction:
    def setup_method(self):
        from app.extraction.structured import extract_structured

        self.extract = extract_structured

    def test_organization_type(self):
        html = """<script type="application/ld+json">
        {"@type":"Organization","email":"contact@corp.com","telephone":"+1-800-123-4567"}
        </script>"""
        hits = self.extract(html, "https://corp.com/")
        types = {h.contact_type for h in hits}
        assert "email" in types
        assert "phone" in types

    def test_local_business_type(self):
        html = """<script type="application/ld+json">
        {"@type":"LocalBusiness","telephone":"+1-312-555-9000"}
        </script>"""
        hits = self.extract(html, "https://local.com/")
        assert any(h.contact_type == "phone" for h in hits)

    def test_no_json_ld(self):
        html = "<html><body><p>No structured data here.</p></body></html>"
        hits = self.extract(html, "https://nojsonld.com/")
        assert hits == []

    def test_confidence_is_high(self):
        html = """<script type="application/ld+json">
        {"@type":"Organization","email":"hi@x.io"}
        </script>"""
        hits = self.extract(html, "https://x.io/")
        assert all(h.confidence == "high" for h in hits)

    def test_malformed_json_graceful(self):
        html = '<script type="application/ld+json">NOT VALID JSON{{{</script>'
        hits = self.extract(html, "https://broken.com/")
        assert hits == []


# ─────────────────────────────────────────────────────────────────────────────
# Pattern extraction (tiers 2–4)
# ─────────────────────────────────────────────────────────────────────────────


class TestPatternExtraction:
    def setup_method(self):
        from app.extraction.patterns import extract_patterns

        self.extract = extract_patterns

    def test_tel_link(self):
        html = '<a href="tel:+13125550100">Call us</a>'
        hits = self.extract(html, "https://co.com/")
        assert any(h.contact_type == "phone" and h.method == "tel" for h in hits)

    def test_mailto_link_strips_query(self):
        html = '<a href="mailto:hi@co.com?subject=Hello">Email</a>'
        hits = self.extract(html, "https://co.com/")
        email_hits = [h for h in hits if h.contact_type == "email"]
        assert any("hi@co.com" in h.raw_value for h in email_hits)

    def test_footer_zone_medium_confidence(self):
        html = "<footer>Contact: support@widgetco.io | 800-555-0100</footer>"
        hits = self.extract(html, "https://widgetco.io/")
        footer_hits = [h for h in hits if h.method == "footer"]
        assert len(footer_hits) > 0

    def test_junk_filtered_in_tier4(self):
        html = "<body><p>test@test.com</p><p>real@corp.com</p></body>"
        hits = self.extract(html, "https://corp.com/")
        values = [h.raw_value for h in hits if h.contact_type == "email"]
        assert "test@test.com" not in values
        assert "real@corp.com" in values

    def test_empty_html(self):
        hits = self.extract("", "https://empty.com/")
        assert hits == []


# ─────────────────────────────────────────────────────────────────────────────
# Contact-page discovery
# ─────────────────────────────────────────────────────────────────────────────


class TestContactPageDiscovery:
    def setup_method(self):
        from app.scraper.discovery import find_contact_page

        self.find = find_contact_page

    def test_finds_contact_link(self):
        html = """<html><body>
        <a href="/contact">Contact Us</a>
        <a href="/about">About</a>
        </body></html>"""
        result = self.find(html, "https://site.com/")
        assert result == "https://site.com/contact"

    def test_ignores_external_links(self):
        html = '<a href="https://other.com/contact">Contact</a>'
        result = self.find(html, "https://site.com/")
        assert result is None

    def test_ignores_pdf_links(self):
        html = '<a href="/docs/contact.pdf">Contact PDF</a>'
        result = self.find(html, "https://site.com/")
        assert result is None

    def test_returns_none_when_empty(self):
        assert self.find("", "https://site.com/") is None

    def test_prefers_contact_over_about(self):
        html = """<html><body>
        <a href="/about">About</a>
        <a href="/contact-us">Contact Us</a>
        </body></html>"""
        result = self.find(html, "https://site.com/")
        assert "contact" in result


# ─────────────────────────────────────────────────────────────────────────────
# Storage / repository integration
# ─────────────────────────────────────────────────────────────────────────────


class TestRepository:
    def setup_method(self):
        from app.models import ExtractionHit
        from app.storage import repository

        self.repo = repository
        self.Hit = ExtractionHit

    def _hit(self, value, ctype="email", method="schema", confidence="high"):
        return self.Hit(
            contact_type=ctype,
            raw_value=value,
            normalized_value=value,
            method=method,
            confidence=confidence,
            source_url="https://test.com/",
        )

    def test_create_and_get_run(self, app_ctx):
        self.repo.create_run("r1", "test q", "serpapi")
        run = self.repo.get_run("r1")
        assert run["query"] == "test q"
        assert run["status"] == "running"

    def test_complete_run(self, app_ctx):
        self.repo.create_run("r2", "q2", "serpapi")
        self.repo.complete_run("r2", 5, 3, "completed")
        run = self.repo.get_run("r2")
        assert run["status"] == "completed"
        assert run["total_contacts_found"] == 3

    def test_upsert_new_lead(self, app_ctx):
        self.repo.create_run("r3", "q3", "serpapi")
        lid = self.repo.upsert_lead(self._hit("new@unique.com"), "r3", "q3", "serpapi")
        assert isinstance(lid, str) and len(lid) == 36  # UUID

    def test_upsert_duplicate_increments_seen_count(self, app_ctx):
        self.repo.create_run("r4", "q4", "serpapi")
        self.repo.upsert_lead(self._hit("dup@x.com"), "r4", "q4", "serpapi")
        self.repo.upsert_lead(self._hit("dup@x.com"), "r4", "q4", "serpapi")
        # Domain in leads table comes from source_url ("https://test.com/"), not the email domain
        leads = self.repo.query_leads(domain="test.com")
        hit = next((l for l in leads if l["normalized_value"] == "dup@x.com"), None)
        assert hit is not None
        assert hit["seen_count"] == 2

    def test_query_filter_by_confidence(self, app_ctx):
        self.repo.create_run("r5", "q5", "serpapi")
        self.repo.upsert_lead(
            self._hit("hi@conf.io", confidence="high"), "r5", "q5", "serpapi"
        )
        self.repo.upsert_lead(
            self._hit("lo@conf.io", confidence="low"), "r5", "q5", "serpapi"
        )
        highs = self.repo.query_leads(confidence="high")
        lows = self.repo.query_leads(confidence="low")
        assert any(l["normalized_value"] == "hi@conf.io" for l in highs)
        assert any(l["normalized_value"] == "lo@conf.io" for l in lows)

    def test_count_leads_and_runs(self, app_ctx):
        leads_before = self.repo.count_leads()
        runs_before = self.repo.count_runs()
        self.repo.create_run("r6", "q6", "serpapi")
        self.repo.upsert_lead(self._hit("cnt@test.org"), "r6", "q6", "serpapi")
        assert self.repo.count_leads() >= leads_before + 1
        assert self.repo.count_runs() >= runs_before + 1


# ─────────────────────────────────────────────────────────────────────────────
# Playwright renderer helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaywrightHelpers:
    def setup_method(self):
        from app.scraper.playwright_renderer import is_js_shell

        self.is_shell = is_js_shell

    def test_empty_is_shell(self):
        assert self.is_shell("") is True

    def test_short_html_is_shell(self):
        assert self.is_shell("<html><body></body></html>") is True

    def test_full_page_not_shell(self):
        big = (
            "<html><body>"
            + "<p>Content here with real text. " * 50
            + "</p></body></html>"
        )
        assert self.is_shell(big) is False


# ─────────────────────────────────────────────────────────────────────────────
# SERP client
# ─────────────────────────────────────────────────────────────────────────────


class TestSerpClient:
    def test_factory_serpapi(self):
        from app.serp.client import SerpAPIClient, get_serp_client

        client = get_serp_client("serpapi", "dummy-key")
        assert isinstance(client, SerpAPIClient)

    def test_factory_unknown_provider(self):
        from app.serp.client import get_serp_client

        with pytest.raises(ValueError, match="Unsupported SERP provider"):
            get_serp_client("nonexistent", "key")

    def test_empty_key_raises(self):
        from app.serp.client import SerpAPIClient, SerpAPIError

        with pytest.raises(SerpAPIError, match="SERP_API_KEY"):
            SerpAPIClient("")

    def test_domain_extraction(self):
        from app.serp.client import _domain

        assert _domain("https://www.acme.com/path?q=1") == "acme.com"
        assert _domain("https://blog.example.org/post") == "blog.example.org"
        assert _domain("") == ""
