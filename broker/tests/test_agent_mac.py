"""agent_mac: site matching and the never-copy list (no network, no VM)."""
import pytest
import agent_mac as a


def test_site_of():
    assert a.site_of("https://www.ineedhemp.com/wp-admin/") == "ineedhemp.com"
    assert a.site_of(".github.com") == "github.com"
    assert a.site_of("https://shop.example.co.uk/x") == "example.co.uk"


def test_cookie_match_is_the_site_and_its_subdomains_only():
    assert a._matches(".github.com", "github.com")
    assert a._matches("gist.github.com", "github.com")
    assert not a._matches("notgithub.com", "github.com")


@pytest.mark.parametrize("site", ["cdtfa.ca.gov", "irs.gov", "authorize.net", "chase.com", "paypal.com"])
def test_one_session_sites_are_never_copied(site):
    with pytest.raises(a.NeverCopy):
        a._check_allowed(site)


def test_ordinary_sites_are_allowed():
    for s in ("github.com", "ineedhemp.com", "hostinger.com"):
        a._check_allowed(s)


def test_clean_drops_session_expiry_and_extra_fields():
    c = a._clean({"name": "n", "value": "v", "domain": ".x.com", "path": "/", "session": True,
                  "expires": -1, "size": 2})
    assert "expires" not in c and "size" not in c and c["name"] == "n"
