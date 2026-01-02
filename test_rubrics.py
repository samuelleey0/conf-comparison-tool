import json
import tempfile
from scheme_manager import SchemeManager
from rubric_manager import RubricManager


def main():
    with tempfile.TemporaryDirectory() as td:
        rm = RubricManager(storage_path=td + "/test_rubrics")
        sm = SchemeManager(storage_path=td + "/test_schemes")

        # sample rubric with placeholders
        sample_rubric = {
            "name": "Test Rubric",
            "description": "rubric for testing",
            "sections": [
                {
                    "name": "hostname",
                    "type": "regex",
                    "pattern": "^hostname\\s+{{hostname}}",
                    "points": 5,
                },
                {
                    "name": "vlan",
                    "type": "regex",
                    "pattern": "^vlan\\s+{{vlan}}",
                    "points": 5,
                },
                {
                    "name": "interfaces",
                    "type": "block_diff",
                    "start": "interface",
                    "points": 10,
                    "weight_by_lines": True,
                },
            ],
        }

        # sample scheme providing variables
        sample_scheme = {
            "name": "Test Scheme",
            "variables": {"hostname": "r1-student", "vlan": 20},
        }

        # create and list
        rid = rm.create_rubric(sample_rubric)
        sid = sm.save_scheme(sample_scheme)

        print("Created rubric id:", rid)
        print("Created scheme id:", sid)

        print("\nSaved rubrics list:")
        print(json.dumps(rm.get_rubrics(), indent=2))

        print("\nSaved schemes list:")
        print(json.dumps(sm.get_all_schemes(), indent=2))

        # load detail and prepare rubric with scheme variables
        rubric_detail = rm.get_rubric_detail(rid)
        scheme_detail = sm.get_scheme_by_id(sid)

        prepared = sm.prepare_rubric_for_grading(rubric_detail, scheme_detail)
        print("\nPrepared rubric (placeholders replaced):")
        print(json.dumps(prepared, indent=2))

        # validation checks
        print("\nRubric valid?:", rm.validate_rubric(rubric_detail))
        print("Scheme valid?:", sm.validate_scheme(scheme_detail))


if __name__ == "__main__":
    main()
