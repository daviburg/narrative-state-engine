"""Tests for extraction template content quality gates."""
import os

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "templates", "extraction")


def _load_template(name):
    with open(os.path.join(TEMPLATE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


class TestRelationshipMapperTemplate:
    def test_has_kinship_section(self):
        content = _load_template("relationship-mapper.md")
        assert "KINSHIP RELATIONSHIPS" in content

    def test_kinship_has_examples(self):
        content = _load_template("relationship-mapper.md")
        assert "kinship" in content.lower()
        assert "parent" in content.lower() or "spouse" in content.lower()


class TestEntityDiscoveryTemplate:
    def test_has_phantom_prevention(self):
        content = _load_template("entity-discovery.md")
        assert "ENTITY NAME VALIDATION" in content

    def test_compound_term_warning(self):
        content = _load_template("entity-discovery.md")
        assert "compound term" in content.lower() or "SUBSTRING" in content

    def test_coreference_examples_present(self):
        """Coreference examples must stay in the template to guide dedup behaviour."""
        content = _load_template("entity-discovery.md")
        assert "Coreference examples" in content, (
            "entity-discovery.md is missing the 'Coreference examples' section"
        )

    def test_coreference_identity_reveal_example(self):
        content = _load_template("entity-discovery.md")
        assert "Identity reveals" in content or "identity reveal" in content.lower(), (
            "entity-discovery.md is missing the identity-reveal coreference example"
        )

    def test_coreference_location_alias_example(self):
        content = _load_template("entity-discovery.md")
        assert "Location aliases" in content or "location alias" in content.lower(), (
            "entity-discovery.md is missing the location-alias coreference example"
        )

    def test_personal_name_component_coreference_rule(self):
        """Partial/compound personal-name references must resolve to one id (#524)."""
        content = _load_template("entity-discovery.md")
        assert "PERSONAL NAME COREFERENCE" in content, (
            "entity-discovery.md is missing the personal-name coreference rule (#524)"
        )
        assert "given name" in content.lower() and "surname" in content.lower(), (
            "entity-discovery.md personal-name coreference rule must reference "
            "given name and surname components"
        )

    def test_personal_name_coreference_example_present(self):
        """The compound-name example must stay to guide dedup behaviour (#524)."""
        content = _load_template("entity-discovery.md")
        assert "Personal-name components" in content, (
            "entity-discovery.md is missing the personal-name-component "
            "coreference example (#524)"
        )

    def test_personal_name_disambiguation_guard_present(self):
        """Shared token must be necessary but NOT sufficient to merge (#524)."""
        content = _load_template("entity-discovery.md")
        lower = content.lower()
        assert "necessary but not sufficient" in lower, (
            "entity-discovery.md personal-name rule must state a shared token is "
            "necessary but NOT sufficient to merge (#524)"
        )
        assert "exactly one" in lower, (
            "entity-discovery.md must guard against ambiguous bare references by "
            "requiring the token to match EXACTLY ONE existing character (#524)"
        )
        assert "conflicting" in lower, (
            "entity-discovery.md must reject merges when a conflicting second "
            "name component marks a different person (#524)"
        )
        assert "Joren Veylin" in content, (
            "entity-discovery.md is missing the negative (different-person) "
            "personal-name example (#524)"
        )

    def test_personal_name_continuity_condition_present(self):
        """Fresh introduction must override the bare-name merge (#524 HIGH)."""
        content = _load_template("entity-discovery.md")
        lower = content.lower()
        assert "continuity" in lower, (
            "entity-discovery.md personal-name rule must require context "
            "CONTINUITY before merging a bare name (#524)"
        )
        assert "for the first time" in lower, (
            "entity-discovery.md must treat first-introduction language as a "
            "signal to mint a NEW id, not merge (#524)"
        )
        assert "the baker" in lower, (
            "entity-discovery.md is missing the conflicting-appositive "
            "fresh-introduction example (Mara, the baker) (#524)"
        )
        assert "another mara" in lower, (
            "entity-discovery.md is missing the 'another Mara' distinct-person "
            "example (#524)"
        )

    def test_personal_name_callback_compact_form_present(self):
        """Callbacks should be emitted in the compact known-entity form (#524 MED1)."""
        content = _load_template("entity-discovery.md")
        assert "PERSONAL-NAME CALLBACK FORM" in content, (
            "entity-discovery.md must instruct personal-name callbacks to use "
            "the compact known-entity form so they survive fragment filtering (#524)"
        )


class TestEntityDetailTemplates:
    def test_personal_name_coreference_in_solo_detail(self):
        """entity-detail must canonicalize compound names via aliases (#524)."""
        content = _load_template("entity-detail.md")
        assert "PERSONAL NAME COREFERENCE" in content, (
            "entity-detail.md is missing the personal-name coreference rule (#524)"
        )

    def test_personal_name_coreference_in_batch_detail(self):
        """entity-detail-batch must mirror the solo personal-name rule (#524)."""
        content = _load_template("entity-detail-batch.md")
        assert "PERSONAL NAME COREFERENCE" in content, (
            "entity-detail-batch.md is missing the personal-name coreference rule (#524)"
        )

    def test_detail_templates_carry_fresh_introduction_caveat(self):
        """Both detail templates must let a fresh introduction override the merge (#524)."""
        for name in ("entity-detail.md", "entity-detail-batch.md"):
            content = _load_template(name)
            lower = content.lower()
            assert "fresh-introduction" in lower or "fresh introduction" in lower, (
                f"{name} personal-name rule must note that a fresh introduction "
                "overrides the canonicalization merge (#524)"
            )
