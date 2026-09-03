"""League launch-status drift guard (E45-status-source, wave 3).

`LEAGUE_STATUS` in api/entitlements.py is the ONE declared authority for
which leagues are live vs. coming soon. Two static surfaces state the same
facts as hand-maintained text and have drifted before (the CFL card sat on
"Coming Soon" after launch; Soccer's copy has wobbled too):

  * static/index.html — the homepage card badges (`.card-badge` = Live,
    `.card-badge.soon` = Coming Soon), and
  * static/help/index.html — the "what leagues" answer, which exists TWICE
    (visible <details> AND the FAQPage JSON-LD; the pair must match per the
    CLAUDE.md gotcha).

The pages stay static on purpose — these tests are the drift guard. They
PARSE the shipped HTML and fail on any disagreement with the map, so a
league launch that flips only one of the three statements breaks the suite
instead of shipping a stale front door.
"""
from __future__ import annotations

import os
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.app import STATIC_DIR  # noqa: E402
from api.entitlements import LEAGUE_STATUS, SPORT_SLUGS  # noqa: E402

# Display names as the /help copy spells them.
DISPLAY_NAMES = {
    "nfl": "NFL",
    "ncaaf": "NCAA Football",
    "cfl": "CFL",
    "golf": "Golf",
    "soccer": "Soccer",
    "nba": "NBA",
    "nhl": "NHL",
}


# ── The map itself stays coherent ────────────────────────────────────────────

def test_status_map_covers_every_sport_slug_with_known_values():
    assert set(LEAGUE_STATUS) == set(SPORT_SLUGS), (
        "LEAGUE_STATUS and SPORT_SLUGS disagree on what leagues exist — "
        "add/remove the league in both places."
    )
    assert set(LEAGUE_STATUS.values()) <= {"live", "coming-soon"}


# ── Homepage card badges ─────────────────────────────────────────────────────

class _CardBadgeParser(HTMLParser):
    """Collect {league slug: (badge classes, badge text)} from the homepage
    project cards — an <a class="card" href="/<slug>/"> wrapping a
    <span class="card-badge …">."""

    def __init__(self):
        super().__init__()
        self.badges: dict[str, tuple[list[str], str]] = {}
        self._card_slug = None
        self._badge_classes = None
        self._badge_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "a" and "card" in classes:
            m = re.fullmatch(r"/([a-z]+)/?", a.get("href") or "")
            self._card_slug = m.group(1) if m else None
        elif tag == "span" and "card-badge" in classes and self._card_slug:
            self._badge_classes = classes
            self._badge_text = []

    def handle_data(self, data):
        if self._badge_classes is not None:
            self._badge_text.append(data)

    def handle_endtag(self, tag):
        if tag == "span" and self._badge_classes is not None:
            text = " ".join("".join(self._badge_text).split())
            self.badges[self._card_slug] = (self._badge_classes, text)
            self._badge_classes = None
        elif tag == "a":
            self._card_slug = None


def _homepage_badges():
    parser = _CardBadgeParser()
    parser.feed((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
    return parser.badges


def test_homepage_has_a_badge_for_every_league():
    assert set(_homepage_badges()) == set(LEAGUE_STATUS), (
        "Homepage cards and LEAGUE_STATUS disagree on which leagues exist."
    )


def test_homepage_badges_agree_with_the_status_map():
    for slug, (classes, text) in _homepage_badges().items():
        want = LEAGUE_STATUS[slug]
        # Badge → status, both the class (colour) and the label (words). An
        # UNKNOWN modifier must fail rather than default to "live" — e.g. a
        # future .card-badge.archive would otherwise be silently misread.
        modifiers = [c for c in classes if c not in ("card-badge",)]
        assert modifiers in ([], ["soon"]), (
            f"Homepage badge for {slug!r} carries an unrecognised modifier "
            f"{modifiers!r} — teach this test what it means before shipping it."
        )
        got = "coming-soon" if "soon" in classes else "live"
        assert got == want, (
            f"Homepage badge drift for {slug!r}: static/index.html renders "
            f"{got!r} (classes={classes}, text={text!r}) but LEAGUE_STATUS "
            f"says {want!r}. Flip the card and the map in the same commit."
        )
        want_text = "Live" if want == "live" else "Coming Soon"
        assert text == want_text, (
            f"Homepage badge label drift for {slug!r}: text is {text!r}, "
            f"expected {want_text!r} per LEAGUE_STATUS."
        )


# ── /help live-products copy (visible FAQ + FAQPage JSON-LD) ─────────────────

_COPY_RE = re.compile(
    r"([^.;]+?)\s+have live dashboards today;\s*([^.;]+?)\s+are in development"
)


def _help_copy_statements():
    """Every 'X have live dashboards today; Y are in development' statement
    in /help, as (live slugs, coming-soon slugs) pairs. The answer exists
    twice — visible <details> and the FAQPage JSON-LD — so two statements
    are expected, and both must agree with the map."""
    html = (STATIC_DIR / "help" / "index.html").read_text(encoding="utf-8")
    statements = []
    for live_clause, soon_clause in _COPY_RE.findall(html):
        def names_in(clause):
            return {
                slug
                for slug, name in DISPLAY_NAMES.items()
                if re.search(rf"\b{re.escape(name)}\b", clause)
            }
        statements.append((names_in(live_clause), names_in(soon_clause)))
    return statements


def test_help_carries_the_copy_twice_visible_and_jsonld():
    assert len(_help_copy_statements()) == 2, (
        "/help should state the live-products sentence exactly twice — the "
        "visible FAQ answer and the FAQPage JSON-LD (they must match; see "
        "the CLAUDE.md gotcha). Rewording it breaks this drift guard — "
        "update tests/test_league_status_drift.py in the same commit."
    )


def test_help_copy_agrees_with_the_status_map():
    want_live = {s for s, v in LEAGUE_STATUS.items() if v == "live"}
    want_soon = {s for s, v in LEAGUE_STATUS.items() if v == "coming-soon"}
    for i, (got_live, got_soon) in enumerate(_help_copy_statements()):
        where = "visible FAQ" if i == 0 else f"statement {i + 1} (JSON-LD)"
        assert got_live == want_live, (
            f"/help {where} live-products drift: copy names "
            f"{sorted(got_live)} as live, LEAGUE_STATUS says "
            f"{sorted(want_live)}. Fix BOTH /help copies and the map "
            f"together."
        )
        assert got_soon == want_soon, (
            f"/help {where} in-development drift: copy names "
            f"{sorted(got_soon)}, LEAGUE_STATUS says {sorted(want_soon)}. "
            f"Fix BOTH /help copies and the map together."
        )
