from pathlib import Path

from shinobi_runtime.store import RepositoryStore, RegisteredSchemaValidator, RegisteredTemplateValidator

ROOT = Path(__file__).resolve().parents[2]


def test_family_owner_accepts_lawful_nested_pregnancy_and_named_arrays():
    repository = RepositoryStore(ROOT)
    family = {
        "schema": "jianghu-family-state-1.0",
        "marriages": {
            "marriage.regression": {
                "spouse_refs": ["person.mother", "person.father"],
                "status": "married",
                "faction_ref": "faction.regression",
                "started_at": "0061-01-01T00:00:00",
                "pregnancy": {
                    "mother_ref": "person.mother",
                    "father_ref": "person.father",
                    "conceived_at": "0061-09-13T21:15:00",
                    "due_at": "0062-06-10T21:15:00",
                    "child_ref": "person.child",
                },
                "last_birth_at": "0060-01-01T00:00:00",
            }
        },
        "parentage": {
            "person.child": {
                "parent_refs": ["person.mother", "person.father"],
            }
        },
        "households": {
            "household.regression": {
                "faction_ref": "faction.regression",
                "head_ref": "person.mother",
                "member_refs": ["person.mother", "person.father", "person.child"],
                "residence_ref": "site.regression",
                "status": "active",
            }
        },
        "succession_claims": {
            "claim.regression": {
                "faction_ref": "faction.regression",
                "person_ref": "person.child",
                "priority": 1,
                "basis": "lineal_descendant",
            }
        },
    }

    schema_validator = RegisteredSchemaValidator(repository)
    schema_validator.validators["jianghu-family-state-1.0"].validate(family)

    template_validator = RegisteredTemplateValidator(repository)
    RegisteredTemplateValidator._validate_document(
        family,
        template_validator.templates["jianghu-family-state-1.0"],
        label="state/martial-world/family.json",
    )
